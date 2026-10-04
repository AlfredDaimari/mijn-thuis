"""SQLite data access for the small, linear housing pipeline.

The durable model deliberately has only three work entities: an email, a
Stekkies link extracted from it, and the provider listing reached from that
link. Each entity owns its processing state and failure reason. Screenshots
belong to provider listings; error summaries are a derived observability view,
not an additional error source of truth.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TypeVar

from .extractor import ListingDetails


Result = TypeVar("Result")
_LOCK_RETRIES = 6
_SCREENSHOT_STAGES = {"capture", "before_fill", "after_fill", "before_submit", "after_submit", "failure"}


class SQLiteDatabase:
    """One local-file connection factory with WAL and short write transactions."""

    def __init__(self, path: str | Path, schema: str, migrate: Callable[[sqlite3.Connection], None]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.schema = schema
        self.migrate = migrate
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _locked(error: sqlite3.OperationalError) -> bool:
        return "locked" in str(error).lower() or "busy" in str(error).lower()

    def _run(self, operation: Callable[[sqlite3.Connection], Result], *, write: bool) -> Result:
        for attempt in range(_LOCK_RETRIES):
            connection = self._connect()
            try:
                if write:
                    connection.execute("BEGIN IMMEDIATE")
                result = operation(connection)
                if write:
                    connection.commit()
                return result
            except sqlite3.OperationalError as error:
                connection.rollback()
                if not self._locked(error) or attempt == _LOCK_RETRIES - 1:
                    raise
                time.sleep(0.02 * (2**attempt))
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        raise RuntimeError("SQLite retry loop ended unexpectedly")

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
        finally:
            connection.close()
        def initialize(connection: sqlite3.Connection) -> None:
            # A prior v1 database has indexes/checks against processing_state;
            # creating the new status indexes before its rebuild would fail.
            if _table_exists(connection, "emails") and _column_exists(connection, "emails", "processing_state"):
                self.migrate(connection)
            connection.executescript(self.schema)
            self.migrate(connection)

        self._run(initialize, write=True)

    def read(self, operation: Callable[[sqlite3.Connection], Result]) -> Result:
        return self._run(operation, write=False)

    def write(self, operation: Callable[[sqlite3.Connection], Result]) -> Result:
        return self._run(operation, write=True)


@dataclass(frozen=True)
class PendingEmail:
    id: int
    signature: str
    message_id: str | None
    sender: str
    subject: str
    raw_message: bytes


@dataclass(frozen=True)
class StekkiesLink:
    id: int
    email_id: int
    stekkies_url: str
    title: str | None
    source_subject: str
    room_count: int | None

    @property
    def url(self) -> str:
        """Compatibility spelling while worker code moves to ``stekkies_url``."""
        return self.stekkies_url


@dataclass(frozen=True)
class ProviderListingWork:
    id: int
    provider_url: str
    title: str | None
    room_count: int | None
    attempt_count: int

    @property
    def resolved_url(self) -> str:
        """Compatibility spelling while worker code moves to ``provider_url``."""
        return self.provider_url


# Renamed in the schema; the alias keeps the worker import stable during this
# focused database migration.
ApplicationWork = ProviderListingWork


@dataclass(frozen=True)
class ResolvedCandidate:
    id: int
    resolved_url: str


@dataclass(frozen=True)
class StoredListingDetails:
    provider_listing_id: int
    provider_title: str | None
    location: str | None
    monthly_rent_cents: int | None
    area_m2: int | None
    room_count: int | None


@dataclass(frozen=True)
class Screenshot:
    id: int
    provider_listing_id: int
    attempt_count: int
    stage: str
    nginx_path: str
    captured_at: str


@dataclass(frozen=True)
class ErrorSummary:
    source_table: str
    error_fingerprint: str
    error_message: str
    error_count: int
    first_failed_at: str
    last_failed_at: str
    refreshed_at: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY,
    signature TEXT NOT NULL UNIQUE,
    message_id TEXT UNIQUE,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    raw_message BLOB NOT NULL,
    accessed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'processed', 'error')),
    processed_at TEXT,
    processing_error TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TEXT,
    CHECK ((status = 'error') = (processing_error IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS emails_status_id_idx ON emails (status, id);

CREATE TABLE IF NOT EXISTS stekkies_links (
    id INTEGER PRIMARY KEY,
    email_id INTEGER NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    stekkies_url TEXT NOT NULL,
    title TEXT,
    source_subject TEXT NOT NULL,
    room_count INTEGER CHECK (room_count IS NULL OR room_count > 0),
    provider_listing_id INTEGER REFERENCES provider_listings(id),
    accessed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'processed', 'error')),
    processed_at TEXT,
    processing_error TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TEXT,
    CHECK ((status = 'error') = (processing_error IS NOT NULL)),
    UNIQUE (email_id, stekkies_url)
);
CREATE INDEX IF NOT EXISTS stekkies_links_status_id_idx ON stekkies_links (status, id);

CREATE TABLE IF NOT EXISTS provider_listings (
    id INTEGER PRIMARY KEY,
    provider_url TEXT NOT NULL UNIQUE,
    provider_title TEXT,
    location TEXT,
    monthly_rent_cents INTEGER CHECK (monthly_rent_cents IS NULL OR monthly_rent_cents > 0),
    area_m2 INTEGER CHECK (area_m2 IS NULL OR area_m2 > 0),
    room_count INTEGER CHECK (room_count IS NULL OR room_count > 0),
    accessed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'awaiting_review', 'submitted', 'error')),
    processed_at TEXT,
    processing_error TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TEXT,
    screenshot_id INTEGER REFERENCES screenshots(id),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((status = 'error') = (processing_error IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS provider_listings_status_id_idx ON provider_listings (status, id);
CREATE INDEX IF NOT EXISTS provider_listings_accessed_id_idx ON provider_listings (accessed_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS provider_listings_status_processed_id_idx ON provider_listings (status, processed_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS screenshots (
    id INTEGER PRIMARY KEY,
    provider_listing_id INTEGER NOT NULL REFERENCES provider_listings(id) ON DELETE CASCADE,
    attempt_count INTEGER NOT NULL CHECK (attempt_count > 0),
    stage TEXT NOT NULL CHECK (stage IN ('capture', 'before_fill', 'after_fill', 'before_submit', 'after_submit', 'failure')),
    nginx_path TEXT NOT NULL CHECK (nginx_path GLOB '/screenshots/*'),
    captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (provider_listing_id, attempt_count, stage)
);
CREATE INDEX IF NOT EXISTS screenshots_listing_capture_idx ON screenshots (provider_listing_id, captured_at, id);

-- Rebuilt periodically from source-table failures; never written by workers.
CREATE TABLE IF NOT EXISTS error_summaries (
    source_table TEXT NOT NULL CHECK (source_table IN ('emails', 'stekkies_links', 'provider_listings')),
    error_fingerprint TEXT NOT NULL,
    error_message TEXT NOT NULL,
    error_count INTEGER NOT NULL CHECK (error_count > 0),
    first_failed_at TEXT NOT NULL,
    last_failed_at TEXT NOT NULL,
    refreshed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_table, error_fingerprint)
);
CREATE INDEX IF NOT EXISTS error_summaries_latest_idx ON error_summaries (last_failed_at DESC, source_table, error_fingerprint);
"""


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def _column_exists(connection: sqlite3.Connection, table: str, column: str) -> bool:
    return column in {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _migrate_linear_state_to_status(connection: sqlite3.Connection) -> None:
    """Rebuild the short-lived v1 tables with the explicit status contract.

    A column rename alone would retain SQLite's v1 CHECK constraint, which
    still accepts ``succeeded``/``failed``. Rebuilding keeps current database
    contents while enforcing the new ``processed``/``error`` vocabulary.
    """
    if not _table_exists(connection, "emails") or not _column_exists(connection, "emails", "processing_state"):
        return

    legacy_tables = ("emails", "stekkies_links", "provider_listings", "screenshots", "error_summaries")
    for table in legacy_tables:
        if _table_exists(connection, table):
            connection.execute(f"ALTER TABLE {table} RENAME TO {table}_linear_v1")
    connection.executescript(_SCHEMA)

    def status(value: str | None) -> str:
        return {"succeeded": "processed", "failed": "error"}.get(value or "pending", value or "pending")

    for row in connection.execute(
        "SELECT id, signature, message_id, sender, subject, raw_message, accessed_at, processing_state, processed_at, processing_error FROM emails_linear_v1"
    ):
        state = status(row[7])
        connection.execute(
            """INSERT INTO emails
               (id, signature, message_id, sender, subject, raw_message, accessed_at, status, processed_at, processing_error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*row[:7], state, row[8], row[9] if state == "error" else None),
        )

    if _table_exists(connection, "provider_listings_linear_v1"):
        for row in connection.execute(
            """SELECT id, provider_url, provider_title, location, monthly_rent_cents, area_m2, room_count,
                      accessed_at, processing_state, processed_at, processing_error, attempt_count, lease_expires_at, updated_at
               FROM provider_listings_linear_v1"""
        ):
            state = status(row[8])
            connection.execute(
                """INSERT INTO provider_listings
                   (id, provider_url, provider_title, location, monthly_rent_cents, area_m2, room_count,
                    accessed_at, status, processed_at, processing_error, attempt_count, lease_expires_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*row[:8], state, row[9], row[10] if state == "error" else None, *row[11:]),
            )

    if _table_exists(connection, "stekkies_links_linear_v1"):
        for row in connection.execute(
            """SELECT id, email_id, stekkies_url, title, source_subject, room_count, provider_listing_id,
                      accessed_at, processing_state, processed_at, processing_error
               FROM stekkies_links_linear_v1"""
        ):
            state = status(row[8])
            connection.execute(
                """INSERT INTO stekkies_links
                   (id, email_id, stekkies_url, title, source_subject, room_count, provider_listing_id,
                    accessed_at, status, processed_at, processing_error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*row[:8], state, row[9], row[10] if state == "error" else None),
            )

    if _table_exists(connection, "screenshots_linear_v1"):
        connection.execute(
            """INSERT INTO screenshots (id, provider_listing_id, attempt_count, stage, nginx_path, captured_at)
               SELECT id, provider_listing_id, attempt_count, stage, nginx_path, captured_at FROM screenshots_linear_v1"""
        )
        connection.execute(
            """UPDATE provider_listings SET screenshot_id = (
                 SELECT s.id FROM screenshots AS s WHERE s.provider_listing_id = provider_listings.id
                 ORDER BY s.captured_at DESC, s.id DESC LIMIT 1
               )"""
        )
    for table in ("error_summaries", "screenshots", "stekkies_links", "provider_listings", "emails"):
        if _table_exists(connection, f"{table}_linear_v1"):
            connection.execute(f"DROP TABLE {table}_linear_v1")


def _migrate_legacy_schema(connection: sqlite3.Connection) -> None:
    """Copy the previous overlapping schema once, then drop only copied tables."""
    if not _table_exists(connection, "seen_emails"):
        _migrate_linear_state_to_status(connection)
        return
    for signature, message_id, sender, subject, raw, is_read, read_at, error in connection.execute(
        "SELECT signature, message_id, sender, subject, raw_message, is_read, read_at, extraction_error FROM seen_emails"
    ):
        state = "error" if error else "processed" if is_read else "pending"
        connection.execute(
            """INSERT OR IGNORE INTO emails (signature, message_id, sender, subject, raw_message, status, processed_at, processing_error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (signature, message_id, sender, subject, raw or b"", state, read_at, error)
        )
    if _table_exists(connection, "listings"):
        rooms_column = "room_count" if _column_exists(connection, "listings", "room_count") else "NULL"
        for signature, url, title, subject, rooms, is_read, read_at in connection.execute(
            f"SELECT source_signature, url, title, source_subject, {rooms_column}, is_read, read_at FROM listings"
        ):
            email = connection.execute("SELECT id FROM emails WHERE signature = ?", (signature,)).fetchone()
            if email:
                connection.execute(
                    """INSERT OR IGNORE INTO stekkies_links (email_id, stekkies_url, title, source_subject, room_count, status, processed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""", (email[0], url, title, subject, rooms, "processed" if is_read else "pending", read_at)
                )
    if _table_exists(connection, "resolved_listings"):
        details = {}
        if _table_exists(connection, "listing_details"):
            details = {row[0]: row[1:] for row in connection.execute(
                "SELECT resolved_listing_id, provider_title, location, monthly_rent_cents, area_m2, room_count FROM listing_details"
            )}
        applications = {}
        if _table_exists(connection, "applications"):
            applications = {row[2]: row[3:] for row in connection.execute(
                "SELECT source_signature, source_url, resolved_url, status, attempt_count, error, screenshot_key, completed_at, lease_expires_at FROM applications"
            )}
        legacy_provider_ids: dict[int, int] = {}
        resolved_id_column = "id" if _column_exists(connection, "resolved_listings", "id") else "rowid"
        resolved_rooms_column = "room_count" if _column_exists(connection, "resolved_listings", "room_count") else "NULL"
        for legacy_id, signature, source_url, provider_url, title, rooms, read_at in connection.execute(
            f"SELECT {resolved_id_column}, source_signature, source_url, resolved_url, title, {resolved_rooms_column}, read_at FROM resolved_listings"
        ):
            email = connection.execute("SELECT id FROM emails WHERE signature = ?", (signature,)).fetchone()
            if not email:
                continue
            link = connection.execute("SELECT id FROM stekkies_links WHERE email_id = ? AND stekkies_url = ?", (email[0], source_url)).fetchone()
            if not link:
                continue
            meta = details.get(legacy_id, (None, None, None, None, rooms))
            app = applications.get(provider_url)
            state = app[0] if app and app[0] in {"pending", "processing", "awaiting_review", "submitted", "failed"} else "pending"
            state = {"failed": "error", "succeeded": "processed"}.get(state, state)
            connection.execute(
                """INSERT OR IGNORE INTO provider_listings
                   (provider_url, provider_title, location, monthly_rent_cents, area_m2, room_count, status, processed_at, processing_error, attempt_count, lease_expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (provider_url, meta[0] or title, meta[1], meta[2], meta[3], meta[4] or rooms,
                 state, app[4] if app else read_at, app[2] if app and state == "error" else None,
                 app[1] if app else 0, app[5] if app else None),
            )
            provider = connection.execute("SELECT id FROM provider_listings WHERE provider_url = ?", (provider_url,)).fetchone()
            legacy_provider_ids[legacy_id] = provider[0]
            connection.execute(
                """UPDATE stekkies_links SET provider_listing_id = ?, status = 'processed',
                   processed_at = COALESCE(processed_at, ?) WHERE id = ?""", (provider[0], read_at, link[0])
            )
        if _table_exists(connection, "application_screenshots"):
            for legacy_id, stage, nginx_path, captured_at in connection.execute(
                "SELECT resolved_listing_id, stage, nginx_path, captured_at FROM application_screenshots"
            ):
                provider_id = legacy_provider_ids.get(legacy_id)
                if provider_id is None or stage not in _SCREENSHOT_STAGES:
                    continue
                connection.execute(
                    """INSERT OR IGNORE INTO screenshots
                       (provider_listing_id, attempt_count, stage, nginx_path, captured_at)
                       VALUES (?, 1, ?, ?, ?)""", (provider_id, stage, nginx_path, captured_at)
                )
        for provider_url, app in applications.items():
            screenshot_key = app[3]
            if not screenshot_key or "/" in screenshot_key or "\\" in screenshot_key:
                continue
            provider = connection.execute("SELECT id FROM provider_listings WHERE provider_url = ?", (provider_url,)).fetchone()
            if provider:
                connection.execute(
                    """INSERT OR IGNORE INTO screenshots
                       (provider_listing_id, attempt_count, stage, nginx_path)
                       VALUES (?, 1, 'capture', ?)""", (provider[0], f"/screenshots/{screenshot_key}")
                )
        connection.execute(
            """UPDATE provider_listings SET screenshot_id = (
                 SELECT s.id FROM screenshots AS s WHERE s.provider_listing_id = provider_listings.id
                 ORDER BY s.captured_at DESC, s.id DESC LIMIT 1
               ) WHERE screenshot_id IS NULL"""
        )
    for table in ("application_screenshots", "pipeline_errors", "provider_sources", "applications", "listing_details", "resolved_listings", "listings", "seen_emails"):
        if _table_exists(connection, table):
            connection.execute(f"DROP TABLE {table}")


class PipelineDatabase:
    """Operations for the email → Stekkies link → provider listing pipeline."""

    def __init__(self, database_path: str | Path) -> None:
        self._database = SQLiteDatabase(database_path, _SCHEMA, _migrate_legacy_schema)

    def remember_if_new(self, signature: str, message_id: str, sender: str, subject: str, raw_message: bytes) -> bool:
        return self._database.write(lambda c: c.execute(
            "INSERT OR IGNORE INTO emails (signature, message_id, sender, subject, raw_message) VALUES (?, ?, ?, ?, ?)",
            (signature, message_id or None, sender, subject, raw_message),
        ).rowcount == 1)

    def pending_emails(self, limit: int = 50) -> list[PendingEmail]:
        return self._database.read(lambda c: [PendingEmail(*row) for row in c.execute(
            "SELECT id, signature, message_id, sender, subject, raw_message FROM emails WHERE status = 'pending' ORDER BY id LIMIT ?", (limit,)
        ).fetchall()])

    unread_emails = pending_emails

    def claim_next_email(self, *, lease_seconds: int = 300) -> PendingEmail | None:
        """Atomically claim one pending email; an abandoned claim becomes error."""
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        def claim(c: sqlite3.Connection) -> PendingEmail | None:
            c.execute(
                """UPDATE emails SET status = 'error', processed_at = CURRENT_TIMESTAMP,
                   processing_error = 'Worker lease expired before email extraction completed', lease_expires_at = NULL
                   WHERE status = 'processing' AND lease_expires_at <= CURRENT_TIMESTAMP"""
            )
            row = c.execute("SELECT id, signature, message_id, sender, subject, raw_message FROM emails WHERE status = 'pending' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            c.execute(
                """UPDATE emails SET status = 'processing', attempt_count = attempt_count + 1,
                   lease_expires_at = datetime('now', ?) WHERE id = ? AND status = 'pending'""",
                (f"+{lease_seconds} seconds", row[0]),
            )
            return PendingEmail(*row)
        return self._database.write(claim)

    def mark_email_read(self, signature: str, extraction_error: str | None = None) -> None:
        self._database.write(lambda c: c.execute(
            """UPDATE emails SET status = ?, processed_at = CURRENT_TIMESTAMP, processing_error = ?, lease_expires_at = NULL
               WHERE signature = ? AND status = 'processing'""",
            ("error" if extraction_error else "processed", self._safe_error(extraction_error) if extraction_error else None, signature),
        ))

    def mark_email_unread(self, signature: str) -> None:
        self._database.write(lambda c: c.execute(
            """UPDATE emails SET status = 'pending', processed_at = NULL, processing_error = NULL,
               lease_expires_at = NULL WHERE signature = ?""", (signature,)
        ))

    def add_links_and_mark_email_processed(self, email: PendingEmail, links: list[tuple[str, str | None, int | None]]) -> list[StekkiesLink]:
        def write(c: sqlite3.Connection) -> list[StekkiesLink]:
            if not c.execute(
                "SELECT 1 FROM emails WHERE id = ? AND status = 'processing'", (email.id,)
            ).fetchone():
                return []
            added = []
            for url, title, rooms in links:
                cursor = c.execute(
                    "INSERT OR IGNORE INTO stekkies_links (email_id, stekkies_url, title, source_subject, room_count) VALUES (?, ?, ?, ?, ?)",
                    (email.id, url, title, email.subject, rooms),
                )
                if cursor.rowcount:
                    added.append(StekkiesLink(c.execute("SELECT last_insert_rowid()").fetchone()[0], email.id, url, title, email.subject, rooms))
            c.execute("""UPDATE emails SET status = 'processed', processed_at = CURRENT_TIMESTAMP,
                       processing_error = NULL, lease_expires_at = NULL WHERE id = ? AND status = 'processing'""", (email.id,))
            return added
        return self._database.write(write)

    add_listings_and_mark_email_read = add_links_and_mark_email_processed

    def pending_stekkies_links(self, limit: int = 50) -> list[StekkiesLink]:
        return self._database.read(lambda c: [StekkiesLink(*row) for row in c.execute(
            "SELECT id, email_id, stekkies_url, title, source_subject, room_count FROM stekkies_links WHERE status = 'pending' ORDER BY id LIMIT ?", (limit,)
        ).fetchall()])

    unread_listings = pending_stekkies_links

    def claim_next_stekkies_link(self, *, lease_seconds: int = 1_800) -> StekkiesLink | None:
        """Atomically claim one pending Stekkies link; an abandoned claim becomes error."""
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        def claim(c: sqlite3.Connection) -> StekkiesLink | None:
            c.execute(
                """UPDATE stekkies_links SET status = 'error', processed_at = CURRENT_TIMESTAMP,
                   processing_error = 'Worker lease expired before link resolution completed', lease_expires_at = NULL
                   WHERE status = 'processing' AND lease_expires_at <= CURRENT_TIMESTAMP"""
            )
            row = c.execute("SELECT id, email_id, stekkies_url, title, source_subject, room_count FROM stekkies_links WHERE status = 'pending' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            c.execute(
                """UPDATE stekkies_links SET status = 'processing', attempt_count = attempt_count + 1,
                   lease_expires_at = datetime('now', ?) WHERE id = ? AND status = 'pending'""",
                (f"+{lease_seconds} seconds", row[0]),
            )
            return StekkiesLink(*row)
        return self._database.write(claim)

    def add_listing_if_new(self, signature: str, url: str, title: str | None, subject: str, room_count: int | None = None) -> bool:
        def write(c: sqlite3.Connection) -> bool:
            c.execute("INSERT OR IGNORE INTO emails (signature, sender, subject, raw_message, status, processed_at) VALUES (?, 'test@stekkies.test', ?, X'', 'processed', CURRENT_TIMESTAMP)", (signature, subject))
            email_id = c.execute("SELECT id FROM emails WHERE signature = ?", (signature,)).fetchone()[0]
            return c.execute(
                "INSERT OR IGNORE INTO stekkies_links (email_id, stekkies_url, title, source_subject, room_count) VALUES (?, ?, ?, ?, ?)",
                (email_id, url, title, subject, room_count),
            ).rowcount == 1
        return self._database.write(write)

    def add_provider_listing_and_mark_link_processed(self, link: StekkiesLink, provider_url: str, details: ListingDetails | None = None) -> bool:
        resolved_details = details or ListingDetails(room_count=link.room_count)

        def write(c: sqlite3.Connection) -> bool:
            if not c.execute(
                "SELECT 1 FROM stekkies_links WHERE id = ? AND status = 'processing'", (link.id,)
            ).fetchone():
                return False
            cursor = c.execute(
                """INSERT OR IGNORE INTO provider_listings
                   (provider_url, provider_title, location, monthly_rent_cents, area_m2, room_count)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (provider_url, resolved_details.provider_title or link.title, resolved_details.location, resolved_details.monthly_rent_cents, resolved_details.area_m2, resolved_details.room_count or link.room_count),
            )
            provider_id = c.execute("SELECT id FROM provider_listings WHERE provider_url = ?", (provider_url,)).fetchone()[0]
            c.execute("""UPDATE stekkies_links SET provider_listing_id = ?, status = 'processed', processed_at = CURRENT_TIMESTAMP,
                       processing_error = NULL, lease_expires_at = NULL WHERE id = ? AND status = 'processing'""", (provider_id, link.id))
            return cursor.rowcount == 1
        return self._database.write(write)

    add_resolved_listing_and_mark_source_read = add_provider_listing_and_mark_link_processed

    @staticmethod
    def _safe_error(error: str) -> str:
        return error.strip()[:2_000] or "Unspecified worker failure"

    def mark_link_failed(self, link_id: int, error: str) -> None:
        self._database.write(lambda c: c.execute(
            """UPDATE stekkies_links SET status = 'error', processed_at = CURRENT_TIMESTAMP, processing_error = ?,
               lease_expires_at = NULL WHERE id = ? AND status = 'processing'""", (self._safe_error(error), link_id)
        ))

    def mark_listing_read(self, signature: str, stekkies_url: str) -> None:
        """Test/replay helper: complete an already claimed Stekkies link."""
        self._database.write(lambda c: c.execute(
            """UPDATE stekkies_links SET status = 'processed', processed_at = CURRENT_TIMESTAMP,
               processing_error = NULL WHERE stekkies_url = ? AND email_id =
                 (SELECT id FROM emails WHERE signature = ?) AND status = 'processing'""", (stekkies_url, signature)
        ))

    def unresolved_provider_listings(self, limit: int = 50) -> list[ResolvedCandidate]:
        return self._database.read(lambda c: [ResolvedCandidate(*row) for row in c.execute(
            "SELECT id, provider_url FROM provider_listings ORDER BY id LIMIT ?", (limit,)
        ).fetchall()])

    resolved_candidates_for_form_test = unresolved_provider_listings

    def pending_provider_listings(self, limit: int = 50) -> list[ProviderListingWork]:
        """Provider rows that have reached the final pipeline entity."""
        return self._database.read(lambda c: [ProviderListingWork(*row) for row in c.execute(
            "SELECT id, provider_url, provider_title, room_count, attempt_count FROM provider_listings WHERE status = 'pending' ORDER BY id LIMIT ?", (limit,)
        ).fetchall()])

    unread_resolved_listings = pending_provider_listings

    def listing_details_for_provider_url(self, provider_url: str) -> StoredListingDetails | None:
        return self._database.read(lambda c: StoredListingDetails(*row) if (row := c.execute(
            "SELECT id, provider_title, location, monthly_rent_cents, area_m2, room_count FROM provider_listings WHERE provider_url = ?", (provider_url,)
        ).fetchone()) else None)

    listing_details_for_resolved_url = listing_details_for_provider_url

    def claim_next_provider_listing(self, *, lease_seconds: int = 1_800, max_attempts: int = 3) -> ProviderListingWork | None:
        if lease_seconds <= 0 or max_attempts <= 0:
            raise ValueError("lease_seconds and max_attempts must be positive")
        def claim(c: sqlite3.Connection) -> ProviderListingWork | None:
            c.execute("""UPDATE provider_listings SET status = 'error', processed_at = CURRENT_TIMESTAMP, lease_expires_at = NULL,
                       processing_error = COALESCE(processing_error, 'Worker lease expired before completion'), updated_at = CURRENT_TIMESTAMP
                       WHERE status = 'processing' AND lease_expires_at <= CURRENT_TIMESTAMP""")
            row = c.execute(
                "SELECT id, provider_url, provider_title, room_count, attempt_count FROM provider_listings WHERE status = 'pending' AND attempt_count < ? ORDER BY id LIMIT 1", (max_attempts,)
            ).fetchone()
            if row is None:
                return None
            c.execute("UPDATE provider_listings SET status = 'processing', attempt_count = attempt_count + 1, lease_expires_at = datetime('now', ?), processing_error = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'", (f"+{lease_seconds} seconds", row[0]))
            return ProviderListingWork(*row[:4], row[4] + 1)
        return self._database.write(claim)

    claim_next_application = claim_next_provider_listing

    @staticmethod
    def _nginx_path(screenshot_key: str) -> str:
        if not screenshot_key or "/" in screenshot_key or "\\" in screenshot_key:
            raise ValueError("screenshot_key must be a filename")
        return f"/screenshots/{screenshot_key}"

    def mark_provider_listing_processed(self, work: ProviderListingWork, screenshot_key: str, *, screenshot_stage: str = "capture") -> None:
        if screenshot_stage not in _SCREENSHOT_STAGES:
            raise ValueError("unsupported screenshot stage")
        def write(c: sqlite3.Connection) -> None:
            c.execute("""INSERT INTO screenshots (provider_listing_id, attempt_count, stage, nginx_path) VALUES (?, ?, ?, ?)
                       ON CONFLICT(provider_listing_id, attempt_count, stage) DO UPDATE SET nginx_path = excluded.nginx_path, captured_at = CURRENT_TIMESTAMP""", (work.id, work.attempt_count, screenshot_stage, self._nginx_path(screenshot_key)))
            screenshot_id = c.execute("SELECT id FROM screenshots WHERE provider_listing_id = ? AND attempt_count = ? AND stage = ?", (work.id, work.attempt_count, screenshot_stage)).fetchone()[0]
            c.execute("""UPDATE provider_listings SET status = 'awaiting_review', processed_at = CURRENT_TIMESTAMP,
                       processing_error = NULL, lease_expires_at = NULL, screenshot_id = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ? AND status = 'processing'""", (screenshot_id, work.id))
        self._database.write(write)

    mark_application_awaiting_review = mark_provider_listing_processed

    def mark_provider_listing_failed(self, work: ProviderListingWork, error: str) -> None:
        self._database.write(lambda c: c.execute(
            "UPDATE provider_listings SET status = 'error', processed_at = CURRENT_TIMESTAMP, processing_error = ?, lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'processing'", (self._safe_error(error), work.id)
        ))

    mark_application_failed = mark_provider_listing_failed

    def provider_listing_status(self, provider_url: str) -> str | None:
        return self._database.read(lambda c: row[0] if (row := c.execute("SELECT status FROM provider_listings WHERE provider_url = ?", (provider_url,)).fetchone()) else None)

    def provider_listing_error(self, provider_url: str) -> str | None:
        return self._database.read(lambda c: row[0] if (row := c.execute("SELECT processing_error FROM provider_listings WHERE provider_url = ?", (provider_url,)).fetchone()) else None)

    # Method aliases preserve the worker/test API; they do not reintroduce an
    # applications table or a duplicate application record.
    def application_status(self, _signature: str, _source_url: str, provider_url: str) -> str | None:
        return self.provider_listing_status(provider_url)

    def application_error(self, _signature: str, _source_url: str, provider_url: str) -> str | None:
        return self.provider_listing_error(provider_url)

    def application_count_for_resolved_url(self, provider_url: str) -> int:
        return self._database.read(lambda c: c.execute("SELECT COUNT(*) FROM provider_listings WHERE provider_url = ?", (provider_url,)).fetchone()[0])

    def screenshots_for_provider_url(self, provider_url: str) -> list[Screenshot]:
        return self._database.read(lambda c: [Screenshot(*row) for row in c.execute(
            """SELECT s.id, s.provider_listing_id, s.attempt_count, s.stage, s.nginx_path, s.captured_at FROM screenshots AS s
               JOIN provider_listings AS p ON p.id = s.provider_listing_id WHERE p.provider_url = ? ORDER BY s.captured_at, s.id""", (provider_url,)
        ).fetchall()])

    screenshots_for_resolved_url = screenshots_for_provider_url

    def refresh_error_summaries(self) -> int:
        def write(c: sqlite3.Connection) -> int:
            rows = []
            for table in ("emails", "stekkies_links", "provider_listings"):
                rows.extend((table, *row) for row in c.execute(
                    f"SELECT processing_error, COUNT(*), MIN(processed_at), MAX(processed_at) FROM {table} WHERE status = 'error' AND processing_error IS NOT NULL GROUP BY processing_error"
                ))
            c.execute("DELETE FROM error_summaries")
            for source, message, count, first, last in rows:
                c.execute("INSERT INTO error_summaries (source_table, error_fingerprint, error_message, error_count, first_failed_at, last_failed_at) VALUES (?, ?, ?, ?, ?, ?)", (source, sha256(message.encode()).hexdigest(), message, count, first, last))
            return len(rows)
        return self._database.write(write)

    def error_summaries(self, limit: int = 50) -> list[ErrorSummary]:
        return self._database.read(lambda c: [ErrorSummary(*row) for row in c.execute(
            "SELECT source_table, error_fingerprint, error_message, error_count, first_failed_at, last_failed_at, refreshed_at FROM error_summaries ORDER BY last_failed_at DESC, source_table, error_fingerprint LIMIT ?", (limit,)
        ).fetchall()])

    def record_form_test_evidence(self, provider_listing_id: int, before: str | None, after: str | None) -> None:
        """Attach dry-run evidence only when it is in the Nginx screenshot tree."""
        def write(c: sqlite3.Connection) -> None:
            for stage, path in (("before_fill", before), ("after_fill", after)):
                if path and path.startswith("/screenshots/"):
                    c.execute(
                        """INSERT OR REPLACE INTO screenshots
                           (provider_listing_id, attempt_count, stage, nginx_path)
                           VALUES (?, 1, ?, ?)""", (provider_listing_id, stage, path)
                    )
        self._database.write(write)

    def clear_derived_queues_for_test_replay(self) -> None:
        self._database.write(lambda c: c.executescript("DELETE FROM screenshots; DELETE FROM stekkies_links; DELETE FROM provider_listings; DELETE FROM error_summaries;"))

    def requeue_all_emails_for_test_replay(self) -> None:
        self._database.write(lambda c: c.execute("UPDATE emails SET status = 'pending', processed_at = NULL, processing_error = NULL"))
