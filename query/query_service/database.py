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
        def create_schema(connection: sqlite3.Connection) -> None:
            connection.execute("PRAGMA journal_mode = WAL")
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
                   ORDER BY first_read_at, signature LIMIT ?""",
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
                       WHERE is_read = 0 ORDER BY created_at, url LIMIT ?""",
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
            PRIMARY KEY (source_signature, source_url, resolved_url)
        );
    """

    def __init__(self, database_path: str | Path) -> None:
        self._database = SQLiteDatabase(database_path, self._SCHEMA)

    def add_if_new(self, listing: QueuedListing, resolved_url: str) -> bool:
        def insert(connection: sqlite3.Connection) -> bool:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO resolved_listings
                   (source_signature, source_url, resolved_url, title, source_subject, is_read)
                   VALUES (?, ?, ?, ?, ?, 0)""",
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
                       ORDER BY created_at, resolved_url LIMIT ?""",
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
