"""Opt-in integration test using one real Yahoo/Stekkies email.

It reads Yahoo without changing mailbox state and keeps the copied raw email in
the test pipeline database. Only its derived queues are reset.
"""

import os
from pathlib import Path

import pytest

from query_service.config import load_settings
from query_service.database import PipelineDatabase
from query_service.extractor import extract_listings
from query_service.processor import process_once
from query_service.yahoo import YahooMailbox


QUERY_DIRECTORY = Path(__file__).resolve().parents[1]
TEST_DATABASE = QUERY_DIRECTORY / "data" / "test.sqlite3"


pytestmark = pytest.mark.integration


def test_live_yahoo_email_is_queued_extracted_and_logged(caplog) -> None:
    """A real saved email flows through one test database with visible URL logs."""
    if os.environ.get("RUN_LIVE_YAHOO_TEST") != "1":
        pytest.skip("Set RUN_LIVE_YAHOO_TEST=1 to run the read-only Yahoo integration test")

    settings = load_settings(QUERY_DIRECTORY / "values.yaml")
    mailbox = YahooMailbox(settings.email, settings.password)
    try:
        live_messages = mailbox.fetch_stekkies_messages()
    finally:
        mailbox.close()

    live_email = next((email for email in reversed(live_messages) if email.links), None)
    assert live_email is not None, "No Stekkies email containing a URL was found"

    TEST_DATABASE.parent.mkdir(exist_ok=True)
    database = PipelineDatabase(TEST_DATABASE)
    database.clear_derived_queues_for_test_replay()
    database.remember_if_new(
        live_email.signature,
        live_email.message_id,
        live_email.sender,
        live_email.subject,
        live_email.raw_message,
    )
    database.mark_email_read(live_email.signature)
    database.mark_email_unread(live_email.signature)
    assert len(database.unread_emails()) == 1

    with caplog.at_level("INFO"):
        assert process_once(database) == 1

    extracted_urls = [listing.url for listing in extract_listings(live_email.raw_message)]
    assert extracted_urls, "No listing URLs were extracted from the selected email"
    for url in extracted_urls:
        assert url in caplog.text, f"Expected extracted listing URL in logs: {url}"
    assert database.unread_emails() == []
    assert len(database.unread_listings()) == len(extracted_urls)
