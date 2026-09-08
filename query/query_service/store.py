from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PendingEmail:
    signature: str
    message_id: str
    sender: str
    subject: str
    raw_message: bytes


class SeenEmailStore:
    """SQLite queue shared by one poller process and one processor process."""

    def __init__(self, database_path: str | Path) -> None:
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path, timeout=10)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute(
            """
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
            )
            """
        )
        self._migrate_existing_table()
        # The earlier service version did not retain raw email content. Such
        # records cannot be extracted by the processor, so do not leave them
        # permanently queued as unread after upgrading.
        self.connection.execute(
            """
            UPDATE seen_emails
            SET is_read = 1,
                read_at = COALESCE(read_at, CURRENT_TIMESTAMP),
                extraction_error = COALESCE(extraction_error, 'Raw email unavailable from earlier service version')
            WHERE raw_message IS NULL AND is_read = 0
            """
        )
        self.connection.commit()

    def _migrate_existing_table(self) -> None:
        """Add queue fields when opening a database created by the first version."""
        columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(seen_emails)")
        }
        additions = {
            "raw_message": "BLOB",
            "is_read": "INTEGER NOT NULL DEFAULT 0",
            "read_at": "TEXT",
            "extraction_error": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                self.connection.execute(f"ALTER TABLE seen_emails ADD COLUMN {name} {definition}")

    def remember_if_new(
        self,
        signature: str,
        message_id: str,
        sender: str,
        subject: str,
        raw_message: bytes,
    ) -> bool:
        """Return True only the first time this service reads the email."""
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO seen_emails
                (signature, message_id, sender, subject, raw_message, is_read)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (signature, message_id, sender, subject, raw_message),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def has_seen(self, signature: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM seen_emails WHERE signature = ?", (signature,)
        ).fetchone()
        return row is not None

    def unread_emails(self, limit: int = 50) -> list[PendingEmail]:
        """Return unread messages for the one listing-processor process."""
        rows = self.connection.execute(
            """
            SELECT signature, message_id, sender, subject, raw_message
            FROM seen_emails
            WHERE is_read = 0
            ORDER BY first_read_at, signature
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [PendingEmail(*row) for row in rows if row[4] is not None]

    def mark_read(self, signature: str, extraction_error: str | None = None) -> None:
        """Record that the listing processor has handled this one queue item."""
        self.connection.execute(
            """
            UPDATE seen_emails
            SET is_read = 1, read_at = CURRENT_TIMESTAMP, extraction_error = ?
            WHERE signature = ?
            """,
            (extraction_error, signature),
        )
        self.connection.commit()

    def mark_unread(self, signature: str) -> None:
        """Requeue one stored message, primarily for controlled replay/testing."""
        self.connection.execute(
            """
            UPDATE seen_emails
            SET is_read = 0, read_at = NULL, extraction_error = NULL
            WHERE signature = ?
            """,
            (signature,),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


@dataclass(frozen=True)
class QueuedListing:
    source_signature: str
    url: str
    title: str | None
    source_subject: str


class ListingStore:
    """SQLite queue of extracted listings for the next pipeline consumer."""

    def __init__(self, database_path: str | Path) -> None:
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path, timeout=10)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS listings (
                source_signature TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT,
                source_subject TEXT NOT NULL,
                is_read INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                read_at TEXT,
                PRIMARY KEY (source_signature, url)
            )
            """
        )
        self.connection.commit()

    def add_if_new(
        self, source_signature: str, url: str, title: str | None, source_subject: str
    ) -> bool:
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO listings (source_signature, url, title, source_subject, is_read)
            VALUES (?, ?, ?, ?, 0)
            """,
            (source_signature, url, title, source_subject),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def unread_listings(self, limit: int = 50) -> list[QueuedListing]:
        rows = self.connection.execute(
            """
            SELECT source_signature, url, title, source_subject
            FROM listings WHERE is_read = 0
            ORDER BY created_at, url LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [QueuedListing(*row) for row in rows]

    def mark_read(self, source_signature: str, url: str) -> None:
        self.connection.execute(
            """
            UPDATE listings SET is_read = 1, read_at = CURRENT_TIMESTAMP
            WHERE source_signature = ? AND url = ?
            """,
            (source_signature, url),
        )
        self.connection.commit()

    def mark_unread(self, source_signature: str, url: str) -> None:
        self.connection.execute(
            """
            UPDATE listings SET is_read = 0, read_at = NULL
            WHERE source_signature = ? AND url = ?
            """,
            (source_signature, url),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
