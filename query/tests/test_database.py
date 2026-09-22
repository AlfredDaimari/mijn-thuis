"""Database-group tests for SQLite's concurrent queue access."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from query_service.database import PipelineDatabase
from query_service.extractor import ListingDetails


pytestmark = pytest.mark.database


def test_single_pipeline_database_indexes_each_unread_queue(tmp_path) -> None:
    """One file contains every unread-first queue index in the pipeline."""
    path = tmp_path / "pipeline.sqlite3"
    PipelineDatabase(path)
    indexes = (
        ("seen_emails_unread_signature_idx", ("is_read", "signature")),
        ("listings_unread_source_url_idx", ("is_read", "source_signature", "url")),
        ("resolved_listings_unread_source_url_idx", ("is_read", "source_signature", "source_url", "resolved_url")),
        ("applications_resolved_url_idx", ("resolved_url",)),
    )

    for index_name, expected_columns in indexes:
        with sqlite3.connect(path) as connection:
            columns = tuple(row[2] for row in connection.execute(f"PRAGMA index_info({index_name})"))
        assert columns == expected_columns


def test_concurrent_duplicate_inserts_create_exactly_one_email(tmp_path) -> None:
    """Concurrent pollers keep one raw-email queue item for one signature."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")

    def insert() -> bool:
        return database.remember_if_new(
            "same-signature", "<same@example.test>", "alerts@stekkies.com",
            "Listings", b"raw email",
        )

    with ThreadPoolExecutor(max_workers=8) as workers:
        inserted = list(workers.map(lambda _number: insert(), range(32)))

    assert sum(inserted) == 1
    assert len(database.unread_emails()) == 1


def test_room_count_flows_from_listing_to_resolved_and_application_work(tmp_path) -> None:
    """A provider application receives the room count extracted by the processor."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    database.add_listing_if_new(
        "email-1", "https://stekkies.test/1", "View match: 3-kamerwoning", "Listings", room_count=3
    )
    listing = database.unread_listings()[0]

    assert database.add_resolved_listing_and_mark_source_read(
        listing, "https://provider.test/home/42"
    )
    assert database.unread_resolved_listings()[0].room_count == 3

    application = database.claim_next_application()

    assert application is not None
    assert application.room_count == 3


def test_resolved_listing_keeps_normalized_provider_details_for_the_frontend(tmp_path) -> None:
    """Resolved provider facts are stored once and can be joined by provider URL."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    database.add_listing_if_new("email-1", "https://stekkies.test/1", "View match", "Listings")
    listing = database.unread_listings()[0]

    database.add_resolved_listing_and_mark_source_read(
        listing,
        "https://provider.test/home/42",
        ListingDetails("Canal apartment", "Amsterdam", 150_000, 72, 3),
    )

    details = database.listing_details_for_resolved_url("https://provider.test/home/42")
    assert details is not None
    assert details.provider_title == "Canal apartment"
    assert details.location == "Amsterdam"
    assert details.monthly_rent_cents == 150_000
    assert details.area_m2 == 72
    assert details.room_count == 3


def test_poller_writes_while_processor_reads_without_losing_emails(tmp_path) -> None:
    """WAL lets one poller write while one processor drains the same queue."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    start = threading.Event()
    writer_finished = threading.Event()
    errors: list[BaseException] = []
    handled: set[str] = set()

    def writer() -> None:
        try:
            start.wait()
            for number in range(40):
                database.remember_if_new(
                    f"signature-{number}", f"<{number}@example.test>",
                    "alerts@stekkies.com", "Listings", f"raw {number}".encode(),
                )
                time.sleep(0.001)
        except BaseException as error:  # surface worker failures in the test
            errors.append(error)
        finally:
            writer_finished.set()

    def reader() -> None:
        try:
            start.wait()
            while not writer_finished.is_set() or database.unread_emails():
                for email in database.unread_emails(limit=10):
                    database.mark_email_read(email.signature)
                    handled.add(email.signature)
                time.sleep(0.001)
        except BaseException as error:  # surface worker failures in the test
            errors.append(error)

    writer_thread = threading.Thread(target=writer)
    reader_thread = threading.Thread(target=reader)
    writer_thread.start()
    reader_thread.start()
    start.set()
    writer_thread.join(timeout=10)
    reader_thread.join(timeout=10)

    assert not writer_thread.is_alive(), "writer did not finish"
    assert not reader_thread.is_alive(), "reader did not finish"
    assert errors == []
    assert handled == {f"signature-{number}" for number in range(40)}
    assert database.unread_emails() == []
