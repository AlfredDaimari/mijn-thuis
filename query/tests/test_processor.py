"""Service and unit tests for extracting listings from raw Stekkies emails."""

from email.message import EmailMessage

import pytest

from query_service.database import PipelineDatabase
from query_service.extractor import Listing, extract_listings
from query_service.processor import process_once


def raw_email(body: str) -> bytes:
    message = EmailMessage()
    message["From"] = "Stekkies <alerts@stekkies.com>"
    message["Subject"] = "Listings"
    message["Message-ID"] = "<processor@example.test>"
    message.set_content(body, subtype="html")
    return message.as_bytes()


@pytest.mark.unit
def test_extract_listings_keeps_the_view_match_anchor_and_title() -> None:
    """An actionable HTML View match link retains its URL and visible title."""
    listings = extract_listings(
        raw_email('<a href="https://houses.test/42">View match: Two-bedroom home</a>')
    )

    assert listings[0].url == "https://houses.test/42"
    assert listings[0].title == "View match: Two-bedroom home"


@pytest.mark.unit
def test_extract_listings_excludes_tracking_assets_and_unsubscribe_urls() -> None:
    """Only the actionable Stekkies redirect is allowed into the listing queue."""
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

    assert listings == [
        Listing("https://www.stekkies.com/nl/api/v1/redirect/listing-id", "View match")
    ]


@pytest.mark.service
def test_processor_queues_extracted_listing_then_marks_email_read(tmp_path) -> None:
    """A successful extraction advances the raw email and creates a queue item."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    database.remember_if_new(
        "signature", "<id>", "alerts@stekkies.com", "Listings",
        raw_email('<a href="https://houses.test/42">View match</a>'),
    )

    assert process_once(database) == 1
    assert database.unread_emails() == []
    queued_listings = database.unread_listings()
    assert len(queued_listings) == 1
    assert queued_listings[0].url == "https://houses.test/42"

    database.mark_listing_read("signature", "https://houses.test/42")
    assert database.unread_listings() == []


@pytest.mark.database
def test_database_can_requeue_a_read_email_for_controlled_replay(tmp_path) -> None:
    """Replaying the saved test email does not require inserting it again."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    database.remember_if_new(
        "signature", "<id>", "alerts@stekkies.com", "Listings", raw_email("https://houses.test/42")
    )
    database.mark_email_read("signature")

    assert database.unread_emails() == []
    database.mark_email_unread("signature")
    assert len(database.unread_emails()) == 1


@pytest.mark.service
def test_processor_logs_and_marks_read_when_email_has_no_listing(caplog, tmp_path) -> None:
    """An unextractable Stekkies email produces an observable terminal error."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    database.remember_if_new(
        "signature", "<id>", "alerts@stekkies.com", "Newsletter", raw_email("No listings")
    )

    with caplog.at_level("ERROR"):
        assert process_once(database) == 1

    assert "No listing links could be extracted" in caplog.text
    assert database.unread_emails() == []
    assert database.unread_listings() == []
