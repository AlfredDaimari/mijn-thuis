"""Opt-in integration test using one real Yahoo/Stekkies email.

This is intentionally skipped during normal tests. It copies one live message
to query/data/test.sqlite3 and writes its extracted listings to a separate
test-only database. It never writes to Yahoo Mail or production databases.
"""

import os
import unittest
from pathlib import Path

from query_service.config import load_settings
from query_service.extractor import extract_listings
from query_service.processor import process_once
from query_service.store import ListingStore, SeenEmailStore
from query_service.yahoo import YahooMailbox

QUERY_DIRECTORY = Path(__file__).resolve().parents[1]
TEST_STAGING_DATABASE = QUERY_DIRECTORY / "data" / "test.sqlite3"
TEST_LISTINGS_DATABASE = QUERY_DIRECTORY / "data" / "test-listings.sqlite3"


@unittest.skipUnless(
    os.environ.get("RUN_LIVE_YAHOO_TEST") == "1",
    "Set RUN_LIVE_YAHOO_TEST=1 to run the read-only Yahoo integration test",
)
class LiveEmailFlowTests(unittest.TestCase):
    def test_copies_live_listing_to_test_database_then_logs_its_urls(self) -> None:
        settings = load_settings(QUERY_DIRECTORY / "values.yaml")
        mailbox = YahooMailbox(settings.email, settings.password)
        try:
            live_messages = mailbox.fetch_stekkies_messages()
        finally:
            mailbox.close()

        # The highest IMAP UID is normally returned last. Choose a message that
        # has a URL because this test demonstrates the successful extraction log.
        live_email = next((email for email in reversed(live_messages) if email.links), None)
        self.assertIsNotNone(live_email, "No Stekkies email containing a URL was found")
        assert live_email is not None

        TEST_STAGING_DATABASE.parent.mkdir(exist_ok=True)
        for database in (TEST_STAGING_DATABASE, TEST_LISTINGS_DATABASE):
            if database.exists():
                database.unlink()
        store = SeenEmailStore(TEST_STAGING_DATABASE)
        listing_store = ListingStore(TEST_LISTINGS_DATABASE)
        try:
            self.assertTrue(
                store.remember_if_new(
                    live_email.signature,
                    live_email.message_id,
                    live_email.sender,
                    live_email.subject,
                    live_email.raw_message,
                )
            )
            # Deliberately demonstrate the queue transition in the test database.
            store.mark_read(live_email.signature)
            store.mark_unread(live_email.signature)
            self.assertEqual(len(store.unread_emails()), 1)

            with self.assertLogs(level="INFO") as logs:
                self.assertEqual(process_once(store, listing_store), 1)

            extracted_urls = [listing.url for listing in extract_listings(live_email.raw_message)]
            self.assertTrue(extracted_urls, "No listing URLs were extracted from the selected email")
            for url in extracted_urls:
                self.assertTrue(
                    any(url in line for line in logs.output),
                    f"Expected extracted listing URL in logs: {url}",
                )
            self.assertEqual(store.unread_emails(), [])
            self.assertEqual(len(listing_store.unread_listings()), len(extracted_urls))
        finally:
            store.close()
            listing_store.close()
