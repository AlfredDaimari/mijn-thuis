import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from query_service.database import EmailDatabase


class DatabaseConcurrencyTests(unittest.TestCase):
    def test_concurrent_duplicate_inserts_create_exactly_one_email(self) -> None:
        """Each repository operation must use its own thread-local connection."""
        with tempfile.TemporaryDirectory() as directory:
            database = EmailDatabase(Path(directory) / "staging.sqlite3")

            def insert() -> bool:
                return database.remember_if_new(
                    "same-signature", "<same@example.test>", "alerts@stekkies.com",
                    "Listings", b"raw email",
                )

            with ThreadPoolExecutor(max_workers=8) as workers:
                inserted = list(workers.map(lambda _number: insert(), range(32)))

            self.assertEqual(sum(inserted), 1)
            self.assertEqual(len(database.unread_emails()), 1)

    def test_poller_write_and_processor_read_can_run_concurrently(self) -> None:
        """WAL writes and reads do not lose queue entries while both run."""
        with tempfile.TemporaryDirectory() as directory:
            database = EmailDatabase(Path(directory) / "staging.sqlite3")
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

            self.assertFalse(writer_thread.is_alive(), "writer did not finish")
            self.assertFalse(reader_thread.is_alive(), "reader did not finish")
            self.assertEqual(errors, [])
            self.assertEqual(handled, {f"signature-{number}" for number in range(40)})
            self.assertEqual(database.unread_emails(), [])
