"""SQLite data-access layer for the Stekkies pipeline.

The service modules deliberately contain no SQL and never retain a SQLite
connection.  Every repository operation opens, uses, and closes its own
connection.  That means the poller, processor, and resolver can run in
separate threads or processes without accidentally sharing a thread-bound
``sqlite3.Connection``.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlparse


Result = TypeVar("Result")
_LOCK_RETRIES = 6


class SQLiteDatabase:
    """Connection factory with WAL, a busy timeout, and short transactions."""

    def __init__(self, database_path: str | Path, schema: str, migrate: Callable[[sqlite3.Connection], None] | None = None) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._schema = schema
        self._migrate = migrate
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        # ``check_same_thread`` remains enabled.  Safety comes from creating a
        # connection inside the calling thread for each repository operation.
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @staticmethod
    def _is_locked(error: sqlite3.OperationalError) -> bool:
        return "locked" in str(error).lower() or "busy" in str(error).lower()

    def _retry(self, operation: Callable[[sqlite3.Connection], Result], *, write: bool) -> Result:
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
                if not self._is_locked(error) or attempt == _LOCK_RETRIES - 1:
                    raise
                time.sleep(0.02 * (2**attempt))
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        raise RuntimeError("SQLite retry loop ended unexpectedly")

    def _initialize(self) -> None:
        # SQLite cannot switch an existing database into WAL mode from inside
        # ``BEGIN IMMEDIATE``. Set the journal mode first, then use the normal
        # short write transaction for schema creation and migrations.
        for attempt in range(_LOCK_RETRIES):
            connection = self._connect()
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                break
            except sqlite3.OperationalError as error:
                if not self._is_locked(error) or attempt == _LOCK_RETRIES - 1:
                    raise
                time.sleep(0.02 * (2**attempt))
            finally:
                connection.close()

        def create_schema(connection: sqlite3.Connection) -> None:
            connection.executescript(self._schema)
            if self._migrate:
                self._migrate(connection)

        self._retry(create_schema, write=True)

    def read(self, operation: Callable[[sqlite3.Connection], Result]) -> Result:
        return self._retry(operation, write=False)

    def write(self, operation: Callable[[sqlite3.Connection], Result]) -> Result:
        return self._retry(operation, write=True)


@dataclass(frozen=True)
class PendingEmail:
    signature: str
    message_id: str
    sender: str
    subject: str
    raw_message: bytes


def _migrate_seen_emails(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(seen_emails)")}
    additions = {
        "raw_message": "BLOB",
        "is_read": "INTEGER NOT NULL DEFAULT 0",
        "read_at": "TEXT",
        "extraction_error": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE seen_emails ADD COLUMN {name} {definition}")
    connection.execute(
        """
        UPDATE seen_emails
        SET is_read = 1,
            read_at = COALESCE(read_at, CURRENT_TIMESTAMP),
            extraction_error = COALESCE(extraction_error, 'Raw email unavailable from earlier service version')
        WHERE raw_message IS NULL AND is_read = 0
        """
    )
    resolved_table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'resolved_listings'"
    ).fetchone()
    if not resolved_table_exists:
        return
    resolved_columns = {row[1] for row in connection.execute("PRAGMA table_info(resolved_listings)")}
    for name, definition in {
        "id": "INTEGER",
        "before_fill_screenshot_key": "TEXT",
        "after_fill_screenshot_key": "TEXT",
    }.items():
        if name not in resolved_columns:
            connection.execute(f"ALTER TABLE resolved_listings ADD COLUMN {name} {definition}")
    connection.execute("UPDATE resolved_listings SET id = rowid WHERE id IS NULL")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS resolved_listings_id_idx ON resolved_listings (id)")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS resolved_listings_url_idx ON resolved_listings (resolved_url)")


class EmailDatabase:
    """Data access for the raw-email queue shared by poller and processor."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS seen_emails (
            signature TEXT PRIMARY KEY,
            message_id TEXT,
            sender TEXT NOT NULL,
            subject TEXT NOT NULL,
            raw_message BLOB,
            is_read INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0, 1)),
            first_read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            read_at TEXT,
            extraction_error TEXT
        );
        -- Queue reads first filter unread work, then use the stable email ID.
        CREATE INDEX IF NOT EXISTS seen_emails_unread_signature_idx
            ON seen_emails (is_read, signature);
    """

    def __init__(self, database_path: str | Path) -> None:
        self._database = SQLiteDatabase(database_path, self._SCHEMA, _migrate_seen_emails)

    def remember_if_new(self, signature: str, message_id: str, sender: str, subject: str, raw_message: bytes) -> bool:
        def insert(connection: sqlite3.Connection) -> bool:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO seen_emails
                   (signature, message_id, sender, subject, raw_message, is_read)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (signature, message_id, sender, subject, raw_message),
            )
            return cursor.rowcount == 1

        return self._database.write(insert)

    def unread_emails(self, limit: int = 50) -> list[PendingEmail]:
        def select(connection: sqlite3.Connection) -> list[PendingEmail]:
            rows = connection.execute(
                """SELECT signature, message_id, sender, subject, raw_message
                   FROM seen_emails WHERE is_read = 0
                   ORDER BY signature LIMIT ?""",
                (limit,),
            ).fetchall()
            return [PendingEmail(*row) for row in rows if row[4] is not None]

        return self._database.read(select)

    def mark_read(self, signature: str, extraction_error: str | None = None) -> None:
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE seen_emails
                   SET is_read = 1, read_at = CURRENT_TIMESTAMP, extraction_error = ?
                   WHERE signature = ?""",
                (extraction_error, signature),
            )
        )

    def mark_unread(self, signature: str) -> None:
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE seen_emails
                   SET is_read = 0, read_at = NULL, extraction_error = NULL
                   WHERE signature = ?""",
                (signature,),
            )
        )


@dataclass(frozen=True)
class QueuedListing:
    source_signature: str
    url: str
    title: str | None
    source_subject: str


class ListingDatabase:
    """Data access for extracted listings awaiting browser resolution."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS listings (
            source_signature TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT,
            source_subject TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            read_at TEXT,
            PRIMARY KEY (source_signature, url)
        );
        -- Queue reads first filter unread work, then use the listing ID.
        CREATE INDEX IF NOT EXISTS listings_unread_source_url_idx
            ON listings (is_read, source_signature, url);
    """

    def __init__(self, database_path: str | Path) -> None:
        self._database = SQLiteDatabase(database_path, self._SCHEMA)

    def add_if_new(self, source_signature: str, url: str, title: str | None, source_subject: str) -> bool:
        def insert(connection: sqlite3.Connection) -> bool:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO listings
                   (source_signature, url, title, source_subject, is_read)
                   VALUES (?, ?, ?, ?, 0)""",
                (source_signature, url, title, source_subject),
            )
            return cursor.rowcount == 1

        return self._database.write(insert)

    def unread_listings(self, limit: int = 50) -> list[QueuedListing]:
        return self._database.read(
            lambda connection: [
                QueuedListing(*row)
                for row in connection.execute(
                    """SELECT source_signature, url, title, source_subject FROM listings
                       WHERE is_read = 0 ORDER BY source_signature, url LIMIT ?""",
                    (limit,),
                ).fetchall()
            ]
        )

    def mark_read(self, source_signature: str, url: str) -> None:
        self._set_read(source_signature, url, True)

    def mark_unread(self, source_signature: str, url: str) -> None:
        self._set_read(source_signature, url, False)

    def _set_read(self, source_signature: str, url: str, is_read: bool) -> None:
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE listings
                   SET is_read = ?, read_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END
                   WHERE source_signature = ? AND url = ?""",
                (int(is_read), int(is_read), source_signature, url),
            )
        )


@dataclass(frozen=True)
class ResolvedListing:
    source_signature: str
    source_url: str
    resolved_url: str
    title: str | None
    source_subject: str


@dataclass(frozen=True)
class ResolvedCandidate:
    id: int
    resolved_url: str


@dataclass(frozen=True)
class ProviderSource:
    host: str
    resolved_count: int
    first_resolved_at: str
    last_resolved_at: str


@dataclass(frozen=True)
class ApplicationWork:
    """A leased provider-listing visit that awaits human review after capture."""

    source_signature: str
    source_url: str
    resolved_url: str
    title: str | None
    source_subject: str
    attempt_count: int


class ResolvedListingDatabase:
    """Data access for resolved provider URLs awaiting their next consumer."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS resolved_listings (
            source_signature TEXT NOT NULL,
            source_url TEXT NOT NULL,
            resolved_url TEXT NOT NULL,
            title TEXT,
            source_subject TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            read_at TEXT,
            PRIMARY KEY (source_signature, source_url, resolved_url),
            UNIQUE (resolved_url)
        );
        -- Queue reads first filter unread work, then use the resolved listing ID.
        CREATE INDEX IF NOT EXISTS resolved_listings_unread_source_url_idx
            ON resolved_listings (is_read, source_signature, source_url, resolved_url);
    """

    def __init__(self, database_path: str | Path) -> None:
        self._database = SQLiteDatabase(database_path, self._SCHEMA)

    def add_if_new(self, listing: QueuedListing, resolved_url: str) -> bool:
        def insert(connection: sqlite3.Connection) -> bool:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO resolved_listings
                   (id, source_signature, source_url, resolved_url, title, source_subject, is_read)
                   VALUES ((SELECT COALESCE(MAX(id), 0) + 1 FROM resolved_listings), ?, ?, ?, ?, ?, 0)""",
                (listing.source_signature, listing.url, resolved_url, listing.title, listing.source_subject),
            )
            return cursor.rowcount == 1

        return self._database.write(insert)

    def unread_listings(self, limit: int = 50) -> list[ResolvedListing]:
        return self._database.read(
            lambda connection: [
                ResolvedListing(*row)
                for row in connection.execute(
                    """SELECT source_signature, source_url, resolved_url, title, source_subject
                       FROM resolved_listings WHERE is_read = 0
                       ORDER BY source_signature, source_url, resolved_url LIMIT ?""",
                    (limit,),
                ).fetchall()
            ]
        )

    def mark_read(self, source_signature: str, source_url: str, resolved_url: str) -> None:
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE resolved_listings SET is_read = 1, read_at = CURRENT_TIMESTAMP
                   WHERE source_signature = ? AND source_url = ? AND resolved_url = ?""",
                (source_signature, source_url, resolved_url),
            )
        )


class PipelineDatabase:
    """One SQLite database containing all durable pipeline queues.

    Queue handoffs use one write transaction, so an output record and its
    source's read state change together.  The service modules only call these
    methods; SQL remains confined to this data-access layer.
    """

    _APPLICATION_SCHEMA = """
        CREATE TABLE IF NOT EXISTS provider_sources (
            host TEXT PRIMARY KEY,
            resolved_count INTEGER NOT NULL DEFAULT 0,
            first_resolved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_resolved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS applications (
            source_signature TEXT NOT NULL,
            source_url TEXT NOT NULL,
            resolved_url TEXT NOT NULL,
            title TEXT,
            source_subject TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN (
                'pending', 'processing', 'awaiting_review', 'submitted', 'failed'
            )),
            lease_expires_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            screenshot_key TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            claimed_at TEXT,
            completed_at TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source_signature, source_url, resolved_url)
        );
        CREATE INDEX IF NOT EXISTS applications_status_lease_idx
            ON applications (status, lease_expires_at, source_signature, source_url, resolved_url);
    """

    _SCHEMA = (
        EmailDatabase._SCHEMA
        + ListingDatabase._SCHEMA
        + ResolvedListingDatabase._SCHEMA
        + _APPLICATION_SCHEMA
    )

    def __init__(self, database_path: str | Path) -> None:
        self._database = SQLiteDatabase(database_path, self._SCHEMA, _migrate_seen_emails)

    def remember_if_new(self, signature: str, message_id: str, sender: str, subject: str, raw_message: bytes) -> bool:
        def insert(connection: sqlite3.Connection) -> bool:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO seen_emails
                   (signature, message_id, sender, subject, raw_message, is_read)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (signature, message_id, sender, subject, raw_message),
            )
            return cursor.rowcount == 1

        return self._database.write(insert)

    def unread_emails(self, limit: int = 50) -> list[PendingEmail]:
        return self._database.read(
            lambda connection: [
                PendingEmail(*row)
                for row in connection.execute(
                    """SELECT signature, message_id, sender, subject, raw_message
                       FROM seen_emails WHERE is_read = 0
                       ORDER BY signature LIMIT ?""",
                    (limit,),
                ).fetchall()
                if row[4] is not None
            ]
        )

    def mark_email_read(self, signature: str, extraction_error: str | None = None) -> None:
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE seen_emails
                   SET is_read = 1, read_at = CURRENT_TIMESTAMP, extraction_error = ?
                   WHERE signature = ?""",
                (extraction_error, signature),
            )
        )

    def mark_email_unread(self, signature: str) -> None:
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE seen_emails
                   SET is_read = 0, read_at = NULL, extraction_error = NULL
                   WHERE signature = ?""",
                (signature,),
            )
        )

    def add_listings_and_mark_email_read(
        self, email: PendingEmail, listings: list[tuple[str, str | None]]
    ) -> list[QueuedListing]:
        """Atomically queue extracted listings and acknowledge their email."""
        def write(connection: sqlite3.Connection) -> list[QueuedListing]:
            added: list[QueuedListing] = []
            for url, title in listings:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO listings
                       (source_signature, url, title, source_subject, is_read)
                       VALUES (?, ?, ?, ?, 0)""",
                    (email.signature, url, title, email.subject),
                )
                if cursor.rowcount == 1:
                    added.append(QueuedListing(email.signature, url, title, email.subject))
            connection.execute(
                """UPDATE seen_emails
                   SET is_read = 1, read_at = CURRENT_TIMESTAMP, extraction_error = NULL
                   WHERE signature = ?""",
                (email.signature,),
            )
            return added

        return self._database.write(write)

    def add_listing_if_new(self, source_signature: str, url: str, title: str | None, source_subject: str) -> bool:
        def insert(connection: sqlite3.Connection) -> bool:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO listings
                   (source_signature, url, title, source_subject, is_read)
                   VALUES (?, ?, ?, ?, 0)""",
                (source_signature, url, title, source_subject),
            )
            return cursor.rowcount == 1

        return self._database.write(insert)

    def unread_listings(self, limit: int = 50) -> list[QueuedListing]:
        return self._database.read(
            lambda connection: [
                QueuedListing(*row)
                for row in connection.execute(
                    """SELECT source_signature, url, title, source_subject FROM listings
                       WHERE is_read = 0 ORDER BY source_signature, url LIMIT ?""",
                    (limit,),
                ).fetchall()
            ]
        )

    def mark_listing_read(self, source_signature: str, url: str) -> None:
        self._set_listing_read(source_signature, url, True)

    def mark_listing_unread(self, source_signature: str, url: str) -> None:
        self._set_listing_read(source_signature, url, False)

    def _set_listing_read(self, source_signature: str, url: str, is_read: bool) -> None:
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE listings
                   SET is_read = ?, read_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END
                   WHERE source_signature = ? AND url = ?""",
                (int(is_read), int(is_read), source_signature, url),
            )
        )

    def add_resolved_listing_and_mark_source_read(self, listing: QueuedListing, resolved_url: str) -> bool:
        """Atomically persist a resolved URL and acknowledge its source listing."""
        def write(connection: sqlite3.Connection) -> bool:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO resolved_listings
                   (source_signature, source_url, resolved_url, title, source_subject, is_read)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (listing.source_signature, listing.url, resolved_url, listing.title, listing.source_subject),
            )
            connection.execute(
                """UPDATE listings SET is_read = 1, read_at = CURRENT_TIMESTAMP
                   WHERE source_signature = ? AND url = ?""",
                (listing.source_signature, listing.url),
            )
            if cursor.rowcount == 1:
                host = (urlparse(resolved_url).hostname or "unknown").lower()
                connection.execute(
                    """INSERT INTO provider_sources (host, resolved_count)
                       VALUES (?, 1)
                       ON CONFLICT(host) DO UPDATE SET resolved_count = resolved_count + 1,
                         last_resolved_at = CURRENT_TIMESTAMP""",
                    (host,),
                )
            return cursor.rowcount == 1

        return self._database.write(write)

    def provider_source_tally(self) -> list[ProviderSource]:
        """Rank provider domains to decide which adapters deserve automation."""
        return self._database.read(lambda connection: [ProviderSource(*row) for row in connection.execute(
            "SELECT host, resolved_count, first_resolved_at, last_resolved_at FROM provider_sources ORDER BY resolved_count DESC, host"
        ).fetchall()])

    def unread_resolved_listings(self, limit: int = 50) -> list[ResolvedListing]:
        return self._database.read(
            lambda connection: [
                ResolvedListing(*row)
                for row in connection.execute(
                    """SELECT source_signature, source_url, resolved_url, title, source_subject
                       FROM resolved_listings WHERE is_read = 0
                       ORDER BY source_signature, source_url, resolved_url LIMIT ?""",
                    (limit,),
                ).fetchall()
            ]
        )

    def resolved_candidates_for_form_test(self, limit: int = 7) -> list[ResolvedCandidate]:
        return self._database.read(lambda connection: [ResolvedCandidate(*row) for row in connection.execute(
            "SELECT id, resolved_url FROM resolved_listings ORDER BY id LIMIT ?", (limit,)
        ).fetchall()])

    def record_form_test_evidence(self, resolved_id: int, before: str | None, after: str | None) -> None:
        self._database.write(lambda connection: connection.execute(
            "UPDATE resolved_listings SET before_fill_screenshot_key = ?, after_fill_screenshot_key = ? WHERE id = ?",
            (before, after, resolved_id),
        ))

    def clear_derived_queues_for_test_replay(self) -> None:
        """Clear disposable output queues while preserving captured raw emails."""
        self._database.write(
            lambda connection: connection.executescript(
                "DELETE FROM resolved_listings; DELETE FROM listings;"
            )
        )

    def requeue_all_emails_for_test_replay(self) -> None:
        """Make saved test emails available again without reinserting them."""
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE seen_emails
                   SET is_read = 0, read_at = NULL, extraction_error = NULL"""
            )
        )

    def mark_resolved_listing_read(self, source_signature: str, source_url: str, resolved_url: str) -> None:
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE resolved_listings SET is_read = 1, read_at = CURRENT_TIMESTAMP
                   WHERE source_signature = ? AND source_url = ? AND resolved_url = ?""",
                (source_signature, source_url, resolved_url),
            )
        )

    def claim_next_application(
        self, *, lease_seconds: int = 1_800, max_attempts: int = 3
    ) -> ApplicationWork | None:
        """Atomically claim one provider visit and lease it to this worker.

        A newly claimed resolved listing is acknowledged in the same
        transaction that creates its durable application record. Expired
        leases become retryable failures before another item is claimed.
        """
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        lease_modifier = f"+{lease_seconds} seconds"

        def claim(connection: sqlite3.Connection) -> ApplicationWork | None:
            connection.execute(
                """UPDATE applications
                   SET status = 'failed', lease_expires_at = NULL,
                       error = COALESCE(error, 'Worker lease expired before completion'),
                       updated_at = CURRENT_TIMESTAMP
                   WHERE status = 'processing'
                     AND lease_expires_at <= CURRENT_TIMESTAMP"""
            )
            existing = connection.execute(
                """SELECT source_signature, source_url, resolved_url, title, source_subject,
                          attempt_count
                   FROM applications
                   WHERE status IN ('pending', 'failed') AND attempt_count < ?
                   ORDER BY source_signature, source_url, resolved_url
                   LIMIT 1""",
                (max_attempts,),
            ).fetchone()
            if existing is not None:
                connection.execute(
                    """UPDATE applications
                       SET status = 'processing', attempt_count = attempt_count + 1,
                           lease_expires_at = datetime('now', ?), claimed_at = CURRENT_TIMESTAMP,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE source_signature = ? AND source_url = ? AND resolved_url = ?""",
                    (lease_modifier, existing[0], existing[1], existing[2]),
                )
                return ApplicationWork(*existing[:5], existing[5] + 1)

            resolved = connection.execute(
                """SELECT source_signature, source_url, resolved_url, title, source_subject
                   FROM resolved_listings WHERE is_read = 0
                   ORDER BY source_signature, source_url, resolved_url LIMIT 1"""
            ).fetchone()
            if resolved is None:
                return None
            connection.execute(
                """INSERT INTO applications
                   (source_signature, source_url, resolved_url, title, source_subject,
                    status, lease_expires_at, attempt_count, claimed_at)
                   VALUES (?, ?, ?, ?, ?, 'processing', datetime('now', ?), 1, CURRENT_TIMESTAMP)""",
                (*resolved, lease_modifier),
            )
            connection.execute(
                """UPDATE resolved_listings SET is_read = 1, read_at = CURRENT_TIMESTAMP
                   WHERE source_signature = ? AND source_url = ? AND resolved_url = ?""",
                resolved[:3],
            )
            return ApplicationWork(*resolved, 1)

        return self._database.write(claim)

    def mark_application_awaiting_review(self, work: ApplicationWork, screenshot_key: str) -> None:
        """Record browser evidence and stop before any irreversible submission."""
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE applications
                   SET status = 'awaiting_review', screenshot_key = ?, error = NULL,
                       lease_expires_at = NULL, completed_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE source_signature = ? AND source_url = ? AND resolved_url = ?
                     AND status = 'processing'""",
                (screenshot_key, work.source_signature, work.source_url, work.resolved_url),
            )
        )

    def mark_application_failed(self, work: ApplicationWork, error: str) -> None:
        """Persist a retryable browser failure and release its lease."""
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE applications
                   SET status = 'failed', error = ?, lease_expires_at = NULL,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE source_signature = ? AND source_url = ? AND resolved_url = ?
                     AND status = 'processing'""",
                (error, work.source_signature, work.source_url, work.resolved_url),
            )
        )

    def application_status(
        self, source_signature: str, source_url: str, resolved_url: str
    ) -> str | None:
        """Return a durable application state for an API or worker test."""
        return self._database.read(
            lambda connection: (
                row[0]
                if (
                    row := connection.execute(
                        """SELECT status FROM applications
                           WHERE source_signature = ? AND source_url = ? AND resolved_url = ?""",
                        (source_signature, source_url, resolved_url),
                    ).fetchone()
                )
                else None
            )
        )

    def application_error(
        self, source_signature: str, source_url: str, resolved_url: str
    ) -> str | None:
        """Return the latest safe failure reason recorded by the application worker."""
        return self._database.read(
            lambda connection: (
                row[0]
                if (
                    row := connection.execute(
                        """SELECT error FROM applications
                           WHERE source_signature = ? AND source_url = ? AND resolved_url = ?""",
                        (source_signature, source_url, resolved_url),
                    ).fetchone()
                )
                else None
            )
        )
