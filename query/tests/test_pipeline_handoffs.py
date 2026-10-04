"""Status-contract tests for the three durable pipeline handoffs."""

import sqlite3

import pytest

from query_service.database import PipelineDatabase


pytestmark = pytest.mark.database


def _rows(path, table: str) -> list[tuple]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            f"SELECT id, status, processing_error FROM {table} ORDER BY id"
        ).fetchall()


def _new_email(database: PipelineDatabase) -> None:
    assert database.remember_if_new(
        "message-signature", "<message@example.test>", "alerts@stekkies.com", "New listings", b"raw message"
    )


def test_email_reader_only_inserts_unique_pending_email(tmp_path) -> None:
    """Polling is idempotent and does not process the email it stores."""
    path = tmp_path / "pipeline.sqlite3"
    database = PipelineDatabase(path)

    _new_email(database)
    assert not database.remember_if_new(
        "message-signature", "<message@example.test>", "alerts@stekkies.com", "New listings", b"raw message"
    )

    assert _rows(path, "emails") == [(1, "pending", None)]
    assert _rows(path, "stekkies_links") == []


def test_email_claim_transfers_links_then_completes_its_own_row(tmp_path) -> None:
    """The extractor transitions email pending → processing → processed atomically with link creation."""
    path = tmp_path / "pipeline.sqlite3"
    database = PipelineDatabase(path)
    _new_email(database)

    email = database.claim_next_email()
    assert email is not None
    assert _rows(path, "emails") == [(email.id, "processing", None)]

    database.add_links_and_mark_email_processed(email, [("https://www.stekkies.test/redirect/1", "Home", 2)])

    assert _rows(path, "emails") == [(email.id, "processed", None)]
    assert _rows(path, "stekkies_links") == [(1, "pending", None)]


def test_email_extraction_error_is_terminal_and_has_no_downstream_row(tmp_path) -> None:
    """An extractor failure stays on the email row and cannot be claimed again."""
    path = tmp_path / "pipeline.sqlite3"
    database = PipelineDatabase(path)
    _new_email(database)
    email = database.claim_next_email()
    assert email is not None

    database.mark_email_read(email.signature, extraction_error="message did not contain a listing")

    assert _rows(path, "emails") == [(email.id, "error", "message did not contain a listing")]
    assert database.claim_next_email() is None
    assert _rows(path, "stekkies_links") == []


def test_link_claim_transfers_provider_listing_then_completes_link(tmp_path) -> None:
    """The resolver transitions link pending → processing → processed with one provider row."""
    path = tmp_path / "pipeline.sqlite3"
    database = PipelineDatabase(path)
    _new_email(database)
    email = database.claim_next_email()
    assert email is not None
    database.add_links_and_mark_email_processed(email, [("https://www.stekkies.test/redirect/1", "Home", 2)])

    link = database.claim_next_stekkies_link()
    assert link is not None
    assert _rows(path, "stekkies_links") == [(link.id, "processing", None)]
    assert database.add_provider_listing_and_mark_link_processed(link, "https://provider.test/homes/1")

    assert _rows(path, "stekkies_links") == [(link.id, "processed", None)]
    assert _rows(path, "provider_listings") == [(1, "pending", None)]


def test_link_error_is_terminal_and_excluded_from_normal_resolution(tmp_path) -> None:
    """A resolver failure is recorded on its link and is never silently retried."""
    path = tmp_path / "pipeline.sqlite3"
    database = PipelineDatabase(path)
    assert database.add_listing_if_new("email", "https://www.stekkies.test/redirect/1", "Home", "Listings")
    link = database.claim_next_stekkies_link()
    assert link is not None

    database.mark_link_failed(link.id, "session cookie expired")

    assert _rows(path, "stekkies_links") == [(link.id, "error", "session cookie expired")]
    assert database.claim_next_stekkies_link() is None
    assert _rows(path, "provider_listings") == []


def test_provider_error_is_terminal_and_summarized_from_source_row(tmp_path) -> None:
    """The final worker records failure on provider_listings; summary is derived later."""
    path = tmp_path / "pipeline.sqlite3"
    database = PipelineDatabase(path)
    assert database.add_listing_if_new("email", "https://www.stekkies.test/redirect/1", "Home", "Listings")
    link = database.claim_next_stekkies_link()
    assert link is not None
    assert database.add_provider_listing_and_mark_link_processed(link, "https://provider.test/homes/1")
    listing = database.claim_next_provider_listing()
    assert listing is not None

    database.mark_provider_listing_failed(listing, "no supported contact form")

    assert _rows(path, "provider_listings") == [(listing.id, "error", "no supported contact form")]
    assert database.claim_next_provider_listing() is None
    assert database.refresh_error_summaries() == 1
    assert database.error_summaries()[0].source_table == "provider_listings"


def test_first_refactor_state_column_is_rebuilt_as_status(tmp_path) -> None:
    """A prior processing_state database migrates safely to the literal status contract."""
    path = tmp_path / "pipeline.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE emails (
              id INTEGER PRIMARY KEY, signature TEXT UNIQUE, message_id TEXT, sender TEXT,
              subject TEXT, raw_message BLOB, accessed_at TEXT, processing_state TEXT,
              processed_at TEXT, processing_error TEXT
            );
            INSERT INTO emails VALUES (7, 'sig', '<id>', 'sender', 'subject', X'01',
              '2026-01-01 00:00:00', 'failed', '2026-01-01 00:01:00', 'old error');
            """
        )

    PipelineDatabase(path)

    assert _rows(path, "emails") == [(7, "error", "old error")]
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(emails)")}
    assert "status" in columns
    assert "processing_state" not in columns
