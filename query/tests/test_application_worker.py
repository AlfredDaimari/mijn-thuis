"""Database and workflow tests for the human-review provider worker."""

import pytest

from query_service.application_worker import process_once
from query_service.config import ApplicantProfile
from query_service.database import PipelineDatabase
from query_service.form_automation import FormAutomationError
from query_service.gemini_form_planner import GeminiFormPlanner


def resolved_listing(database: PipelineDatabase) -> None:
    """Seed the resolver output queue through the database layer."""
    database.add_listing_if_new(
        "email-1", "https://www.stekkies.com/redirect/1", "View match", "Listings"
    )
    listing = database.unread_listings()[0]
    database.add_resolved_listing_and_mark_source_read(
        listing, "https://provider.example/listing/1"
    )


@pytest.mark.database
def test_application_claim_acknowledges_resolved_item_and_retries_failure(tmp_path) -> None:
    """One durable application claim consumes one resolved queue item exactly once."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    resolved_listing(database)

    first_claim = database.claim_next_application()

    assert first_claim is not None
    assert first_claim.attempt_count == 1
    assert database.unread_resolved_listings() == []
    database.mark_application_failed(first_claim, "temporary provider outage")

    second_claim = database.claim_next_application()

    assert second_claim is not None
    assert second_claim.attempt_count == 2
    assert second_claim.resolved_url == first_claim.resolved_url


@pytest.mark.service
def test_provider_worker_captures_evidence_then_stops_for_review(tmp_path, monkeypatch) -> None:
    """The fourth worker saves evidence and never progresses to submission."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    resolved_listing(database)
    screenshot_directory = tmp_path / "screenshots"

    monkeypatch.setattr(
        "query_service.application_worker._capture_for_review",
        lambda _work, _directory: "provider-listing.png",
    )

    assert process_once(database, screenshot_directory) == 1
    assert database.claim_next_application() is None
    assert database.application_status(
        "email-1",
        "https://www.stekkies.com/redirect/1",
        "https://provider.example/listing/1",
    ) == "awaiting_review"


@pytest.mark.service
def test_form_failure_persists_generic_and_gemini_reason_in_application_error(tmp_path, monkeypatch) -> None:
    """A failed Gemini fallback keeps the original reason observable in SQLite."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    resolved_listing(database)
    profile = ApplicantProfile("Ada", "Lovelace", "+31600000000", "ada@example.com", "Interested")

    monkeypatch.setattr(
        "query_service.application_worker._fill_for_review",
        lambda *_args: (_ for _ in ()).throw(
            FormAutomationError(
                "generic form matching failed: no visible contact form; "
                "Gemini fallback failed: Gemini found no safe navigation action: only login"
            )
        ),
    )

    assert process_once(
        database,
        tmp_path / "screenshots",
        profile=profile,
        planner=GeminiFormPlanner("test-key"),
    ) == 0

    error = database.application_error(
        "email-1", "https://www.stekkies.com/redirect/1", "https://provider.example/listing/1"
    )
    assert error is not None
    assert "no visible contact form" in error
    assert "Gemini fallback failed" in error
