import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from query_service.extractor import Listing, extract_listings
from query_service.processor import process_once
from query_service.store import ListingStore, SeenEmailStore


def raw_email(body: str) -> bytes:
    message = EmailMessage()
    message["From"] = "Stekkies <alerts@stekkies.com>"
    message["Subject"] = "Listings"
    message["Message-ID"] = "<processor@example.test>"
    message.set_content(body, subtype="html")
    return message.as_bytes()


class ListingProcessorTests(unittest.TestCase):
    def test_extracts_html_link_and_anchor_title(self) -> None:
        listings = extract_listings(raw_email('<a href="https://houses.test/42">View match: Two-bedroom home</a>'))
        self.assertEqual(listings[0].url, "https://houses.test/42")
        self.assertEqual(listings[0].title, "View match: Two-bedroom home")

    def test_excludes_tracking_assets_and_unsubscribe_urls(self) -> None:
        listings = extract_listings(
            raw_email(
                """
                <a href="https://email.stekkies.com/e/c/tracker">tracking</a>
                <a href="https://www.stekkies.com/static/banner.png">asset</a>
                <a href="https://agency.test/images/property-42">asset without suffix</a>
                <a href="https://www.stekkies.com/unsubscribe/token">unsubscribe</a>
                <a href="https://www.stekkies.com/nl/api/v1/redirect/listing-id">View match</a>
                """
            )
        )
        self.assertEqual(
            listings,
            [Listing("https://www.stekkies.com/nl/api/v1/redirect/listing-id", "View match")],
        )

    def test_processor_marks_successful_email_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging_store = SeenEmailStore(Path(directory) / "staging.sqlite3")
            listing_store = ListingStore(Path(directory) / "listings.sqlite3")
            staging_store.remember_if_new("signature", "<id>", "alerts@stekkies.com", "Listings", raw_email('<a href="https://houses.test/42">View match</a>'))
            self.assertEqual(process_once(staging_store, listing_store), 1)
            self.assertEqual(staging_store.unread_emails(), [])
            queued_listings = listing_store.unread_listings()
            self.assertEqual(len(queued_listings), 1)
            self.assertEqual(queued_listings[0].url, "https://houses.test/42")
            listing_store.mark_read("signature", "https://houses.test/42")
            self.assertEqual(listing_store.unread_listings(), [])
            staging_store.close()
            listing_store.close()

    def test_a_read_email_can_be_requeued_for_a_controlled_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SeenEmailStore(Path(directory) / "queue.sqlite3")
            store.remember_if_new("signature", "<id>", "alerts@stekkies.com", "Listings", raw_email("https://houses.test/42"))
            store.mark_read("signature")
            self.assertEqual(store.unread_emails(), [])
            store.mark_unread("signature")
            self.assertEqual(len(store.unread_emails()), 1)
            store.close()

    def test_processor_logs_and_marks_read_when_no_listing_can_be_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SeenEmailStore(Path(directory) / "staging.sqlite3")
            listing_store = ListingStore(Path(directory) / "listings.sqlite3")
            store.remember_if_new("signature", "<id>", "alerts@stekkies.com", "Newsletter", raw_email("No listings"))
            with self.assertLogs(level="ERROR") as logs:
                self.assertEqual(process_once(store, listing_store), 1)
            self.assertIn("No listing links could be extracted", logs.output[0])
            self.assertEqual(store.unread_emails(), [])
            self.assertEqual(listing_store.unread_listings(), [])
            store.close()
            listing_store.close()
