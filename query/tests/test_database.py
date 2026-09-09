"""Database-group tests for SQLite's concurrent queue access."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from query_service.database import EmailDatabase, ListingDatabase, ResolvedListingDatabase


pytestmark = pytest.mark.database


def test_queue_indexes_filter_unread_before_each_stable_record_id(tmp_path) -> None:
    """Every queue lookup has an index matching its unread-first read order."""
    paths_and_indexes = (
        (tmp_path / "staging.sqlite3", EmailDatabase, "seen_emails_unread_signature_idx", ("is_read", "signature")),
        (tmp_path / "listings.sqlite3", ListingDatabase, "listings_unread_source_url_idx", ("is_read", "source_signature", "url")),
        (tmp_path / "resolved.sqlite3", ResolvedListingDatabase, "resolved_listings_unread_source_url_idx", ("is_read", "source_signature", "source_url", "resolved_url")),
    )

    for path, database_type, index_name, expected_columns in paths_and_indexes:
        database_type(path)
        with sqlite3.connect(path) as connection:
            columns = tuple(row[2] for row in connection.execute(f"PRAGMA index_info({index_name})"))
        assert columns == expected_columns


def test_concurrent_duplicate_inserts_create_exactly_one_email(tmp_path) -> None:
    """Concurrent pollers keep one raw-email queue item for one signature."""
    database = EmailDatabase(tmp_path / "staging.sqlite3")

    def insert() -> bool:
        return database.remember_if_new(
            "same-signature", "<same@example.test>", "alerts@stekkies.com",
            "Listings", b"raw email",
        )

    with ThreadPoolExecutor(max_workers=8) as workers:
        inserted = list(workers.map(lambda _number: insert(), range(32)))

    assert sum(inserted) == 1
    assert len(database.unread_emails()) == 1


def test_poller_writes_while_processor_reads_without_losing_emails(tmp_path) -> None:
    """WAL lets one poller write while one processor drains the same queue."""
    database = EmailDatabase(tmp_path / "staging.sqlite3")
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
                    database.mark_read(email.signature)
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
