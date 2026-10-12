"""Database and workflow tests for the duplicate-safe provider worker."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from query_service.application_worker import process_once
from query_service.config import ApplicantProfile
from query_service.database import PipelineDatabase
from query_service.form_automation import FormAutomationError
from query_service.openrouter_form_planner import OpenRouterFormPlanner


def resolved_listing(database: PipelineDatabase) -> None:
    """Seed the resolver output queue through the database layer."""
    database.add_listing_if_new(
        "email-1", "https://www.stekkies.com/redirect/1", "View match", "Listings"
    )
    listing = database.claim_next_stekkies_link()
    assert listing is not None
    database.add_resolved_listing_and_mark_source_read(
        listing, "https://provider.example/listing/1"
    )


@pytest.mark.database
def test_submission_intent_survives_failure_and_prevents_duplicate_send(tmp_path):
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    resolved_listing(database)
    work = database.claim_next_provider_listing()
    database.start_submission(work)
    database.mark_provider_listing_failed(
        work, "submission_unknown: connection dropped"
    )
    with database._database._connect() as connection:
        connection.execute(
            "UPDATE provider_listings SET status='pending',processing_error=NULL"
        )
    assert database.claim_next_provider_listing() is None
    assert not database.claimable_provider_listings_exist()


@pytest.mark.database
def test_confirmed_submission_transitions_to_submitted(tmp_path):
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    resolved_listing(database)
    work = database.claim_next_provider_listing()
    database.start_submission(work)
    database.record_provider_evidence(work, "before.png", "before_submit")
    database.record_provider_evidence(work, "after.png", "after_submit")
    database.confirm_submission(work)
    assert database.provider_listing_status(work.resolved_url) == "submitted"


@pytest.mark.database
def test_application_claim_marks_failed_work_terminally(tmp_path) -> None:
    """One durable application claim does not silently retry a provider failure."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    resolved_listing(database)

    first_claim = database.claim_next_application()

    assert first_claim is not None
    assert first_claim.attempt_count == 1
    assert database.unread_resolved_listings() == []
    database.mark_application_failed(first_claim, "temporary provider outage")

    second_claim = database.claim_next_application()

    assert second_claim is None
    assert database.application_count_for_resolved_url(first_claim.resolved_url) == 1


@pytest.mark.database
def test_concurrent_application_claims_create_only_one_record_per_house(
    tmp_path,
) -> None:
    """Two applier workers cannot claim or create duplicate work for one provider URL."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    resolved_listing(database)

    with ThreadPoolExecutor(max_workers=2) as workers:
        claims = list(
            workers.map(lambda _number: database.claim_next_application(), range(2))
        )

    assert len([claim for claim in claims if claim is not None]) == 1
    assert (
        database.application_count_for_resolved_url(
            "https://provider.example/listing/1"
        )
        == 1
    )


@pytest.mark.service
def test_dry_run_is_explicitly_unsent_and_preserves_evidence(
    tmp_path, monkeypatch
) -> None:
    """Dry-run evidence must never be marked submitted or awaiting review."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    resolved_listing(database)
    screenshot_directory = tmp_path / "screenshots"

    monkeypatch.setattr(
        "query_service.application_worker._capture_for_review",
        lambda _work, _directory: "provider-listing.png",
    )

    assert process_once(database, screenshot_directory) == 1
    assert database.claim_next_application() is None
    assert (
        database.application_status(
            "email-1",
            "https://www.stekkies.com/redirect/1",
            "https://provider.example/listing/1",
        )
        == "error"
    )
    screenshots = database.screenshots_for_resolved_url(
        "https://provider.example/listing/1"
    )
    assert [(item.stage, item.nginx_path) for item in screenshots] == [
        ("capture", "/screenshots/provider-listing.png")
    ]


@pytest.mark.service
def test_form_failure_persists_generic_and_openrouter_reason_in_application_error(
    tmp_path, monkeypatch
) -> None:
    """A failed OpenRouter fallback keeps the original reason observable in SQLite."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    resolved_listing(database)
    profile = ApplicantProfile(
        "Ada",
        "Lovelace",
        "+31600000000",
        "ada@example.com",
        "Interested",
        "Interested together",
    )

    monkeypatch.setattr(
        "query_service.application_worker._fill_for_review",
        lambda *_args: (_ for _ in ()).throw(
            FormAutomationError(
                "generic form matching failed: no visible contact form; "
                "OpenRouter fallback failed: OpenRouter found no safe navigation action: only login"
            )
        ),
    )

    assert (
        process_once(
            database,
            tmp_path / "screenshots",
            profile=profile,
            planner=OpenRouterFormPlanner("test-key"),
        )
        == 0
    )

    error = database.application_error(
        "email-1",
        "https://www.stekkies.com/redirect/1",
        "https://provider.example/listing/1",
    )
    assert error is not None
    assert "no visible contact form" in error
    assert "OpenRouter fallback failed" in error
