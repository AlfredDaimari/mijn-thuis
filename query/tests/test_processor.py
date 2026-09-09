import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from query_service.extractor import Listing, extract_listings
from query_service.processor import process_once
from query_service.database import EmailDatabase, ListingDatabase


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
            staging_database = EmailDatabase(Path(directory) / "staging.sqlite3")
            listings_database = ListingDatabase(Path(directory) / "listings.sqlite3")
            staging_database.remember_if_new("signature", "<id>", "alerts@stekkies.com", "Listings", raw_email('<a href="https://houses.test/42">View match</a>'))
            self.assertEqual(process_once(staging_database, listings_database), 1)
            self.assertEqual(staging_database.unread_emails(), [])
            queued_listings = listings_database.unread_listings()
            self.assertEqual(len(queued_listings), 1)
            self.assertEqual(queued_listings[0].url, "https://houses.test/42")
            listings_database.mark_read("signature", "https://houses.test/42")
            self.assertEqual(listings_database.unread_listings(), [])

    def test_a_read_email_can_be_requeued_for_a_controlled_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = EmailDatabase(Path(directory) / "queue.sqlite3")
            database.remember_if_new("signature", "<id>", "alerts@stekkies.com", "Listings", raw_email("https://houses.test/42"))
            database.mark_read("signature")
            self.assertEqual(database.unread_emails(), [])
            database.mark_unread("signature")
            self.assertEqual(len(database.unread_emails()), 1)

    def test_processor_logs_and_marks_read_when_no_listing_can_be_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = EmailDatabase(Path(directory) / "staging.sqlite3")
            listings_database = ListingDatabase(Path(directory) / "listings.sqlite3")
            database.remember_if_new("signature", "<id>", "alerts@stekkies.com", "Newsletter", raw_email("No listings"))
            with self.assertLogs(level="ERROR") as logs:
                self.assertEqual(process_once(database, listings_database), 1)
            self.assertIn("No listing links could be extracted", logs.output[0])
            self.assertEqual(database.unread_emails(), [])
            self.assertEqual(listings_database.unread_listings(), [])
