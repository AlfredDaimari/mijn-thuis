"""Provider applications with explicit submit mode and confirmed outcomes."""

from __future__ import annotations

import argparse
import hashlib
import logging
import time
from pathlib import Path
from urllib.parse import urlparse

from .__main__ import DEFAULT_POLL_SECONDS, configure_logging
from .config import (
    AccountCredentials,
    ApplicantProfile,
    load_accounts,
    load_settings,
    submission_profile,
    submission_problems,
)
from .database import ApplicationWork, PipelineDatabase
from .form_automation import (
    FormAutomationError,
    apply_navigation_plan,
    dismiss_cookie_banner,
    fill_generic_form,
    login_to_provider,
    visible_navigation_candidates,
)
from .openrouter_form_planner import OpenRouterFormPlanner, OpenRouterPlanningError
from .provider_adapters import flow_for
from .provider_forms import (
    ProviderFormError,
    find_contact_form,
    authorize_contact_consent,
    validate_form,
    submit_control,
    confirmation_state,
)


DEFAULT_TIMEOUT_MS = 30_000
MAX_MODEL_STEPS = 6


def _settle_page(page) -> None:
    """Wait for client rendering before deciding that a form is absent."""
    try:
        page.wait_for_load_state("networkidle", timeout=4000)
    except Exception:
        pass
    page.wait_for_timeout(500)


def _diagnostic(error: Exception) -> str:
    # Playwright errors embed fill values; never persist/log their call trace.
    if isinstance(error, (ProviderFormError, FormAutomationError)):
        return str(error)[:800]
    if "Timeout" in type(error).__name__:
        return "provider_unavailable: browser action timed out"
    return f"worker_error: {type(error).__name__}"


def _submit_prepared_form(
    page, work, database, directory, screenshot_key, *, allow_credit_check
):
    """Persist intent before the single final click and evidence after it."""
    form = find_contact_form(page)
    # The user authorized the Schep credit-check flow explicitly. Other
    # providers' credit checks remain separate consent decisions.
    credit_allowed = (
        allow_credit_check
        and urlparse(work.resolved_url).hostname == "zoeken.schepvastgoedmanagers.nl"
    )
    authorize_contact_consent(form, allow_credit_check=credit_allowed)
    validate_form(form)
    button = submit_control(form)
    if confirmation_state(page, form)[0] == "submitted":
        raise ProviderFormError(
            "submission_unknown: confirmation existed before clicking"
        )
    before_key = "before-" + screenshot_key
    page.screenshot(path=str(directory / before_key), full_page=True)
    database.record_provider_evidence(work, before_key, "before_submit")
    database.start_submission(work)
    button.click(timeout=10000)
    state, detail = "unknown", ""
    for _ in range(20):
        page.wait_for_timeout(500)
        state, detail = confirmation_state(page, form)
        if state != "unknown":
            break
    page.screenshot(path=str(directory / screenshot_key), full_page=True)
    database.record_provider_evidence(work, screenshot_key, "after_submit")
    if state != "submitted":
        raise ProviderFormError("submission_" + state + ": " + detail)
    database.confirm_submission(work)


def _screenshot_key(work: ApplicationWork) -> str:
    # The canonical provider-listing ID and attempt make evidence stable and
    # avoid depending on discarded source-link fields.
    identity = f"{work.id}\n{work.attempt_count}\n{work.resolved_url}"
    return f"{hashlib.sha256(identity.encode('utf-8')).hexdigest()}.png"


def _capture_for_review(work: ApplicationWork, screenshot_directory: Path) -> str:
    """Open one provider URL and atomically save evidence to the mounted volume."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError(
            "Playwright is not installed. Run: python3 -m pip install -r requirements.txt "
            "and then: python3 -m playwright install chromium"
        ) from error

    screenshot_directory.mkdir(parents=True, exist_ok=True)
    screenshot_key = _screenshot_key(work)
    screenshot_path = screenshot_directory / screenshot_key
    temporary_path = screenshot_directory / f".{screenshot_key}.tmp.png"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(
                work.resolved_url,
                wait_until="domcontentloaded",
                timeout=DEFAULT_TIMEOUT_MS,
            )
            dismiss_cookie_banner(page)
            page.screenshot(path=str(temporary_path), full_page=True)
            temporary_path.replace(screenshot_path)
        finally:
            page.close()
            context.close()
            browser.close()
    return screenshot_key


def _fill_for_review(
    work: ApplicationWork,
    screenshot_directory: Path,
    profile: ApplicantProfile,
    planner: OpenRouterFormPlanner,
    accounts: dict[str, AccountCredentials],
    *,
    submit: bool = False,
    database: PipelineDatabase | None = None,
    allow_credit_check: bool = False,
) -> tuple[str, list[str], str | None]:
    """Fill recognised fields, asking OpenRouter only after generic matching fails.

    The planner is deliberately not given applicant values. Its click plan is
    validated again before Playwright can execute it.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError(
            "Playwright is not installed. Run: python3 -m pip install -r requirements.txt "
            "and then: python3 -m playwright install chromium"
        ) from error

    screenshot_directory.mkdir(parents=True, exist_ok=True)
    screenshot_key = _screenshot_key(work)
    screenshot_path = screenshot_directory / screenshot_key
    temporary_path = screenshot_directory / f".{screenshot_key}.tmp.png"
    model_reasons: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            locale="nl-NL", viewport={"width": 1440, "height": 1000}
        )
        page = context.new_page()
        try:
            response = page.goto(
                work.resolved_url,
                wait_until="domcontentloaded",
                timeout=DEFAULT_TIMEOUT_MS,
            )
            if response and response.status >= 400:
                raise ProviderFormError(f"provider_unavailable: HTTP {response.status}")
            _settle_page(page)
            title = page.title().casefold()
            if any(
                word in title
                for word in (
                    "security checkpoint",
                    "just a moment",
                    "je bent bijna op de pagina",
                )
            ):
                raise ProviderFormError(
                    "security_check: provider served a browser verification page"
                )
            dismiss_cookie_banner(page)
            _settle_page(page)
            provider_flow = flow_for(work.resolved_url)
            if provider_flow is not None:
                host = (urlparse(page.url).hostname or "").lower()
                credentials = accounts.get(host) or accounts.get(
                    host.removeprefix("www.")
                )
                filled = provider_flow(page, profile, work.room_count, credentials)
                model_reasons.append("provider-specific Playwright flow")
            else:
                for step in range(MAX_MODEL_STEPS + 1):
                    try:
                        filled = fill_generic_form(
                            page, profile, profile.message_for_rooms(work.room_count)
                        )
                        break
                    except FormAutomationError as generic_error:
                        generic_reason = str(generic_error)
                        if generic_reason.startswith(
                            ("missing_profile_field:", "form_validation:")
                        ):
                            raise
                    candidates = visible_navigation_candidates(page)
                    # A registration link in site chrome is not evidence that
                    # this listing is gated. Let the planner inspect page state.
                    if step == MAX_MODEL_STEPS:
                        raise FormAutomationError(
                            f"generic form matching failed: {generic_reason}; "
                            f"OpenRouter fallback exhausted after {MAX_MODEL_STEPS} steps"
                        )
                    try:
                        plan = planner.plan(
                            work.resolved_url,
                            generic_reason,
                            candidates,
                            applicant_context=profile.navigation_context(),
                            page_state={
                                "title": page.title(),
                                "forms": page.locator("form").evaluate_all(
                                    "els=>els.map(e=>({visible:!!e.getClientRects().length,fields:[...e.querySelectorAll('input,textarea,select')].map(f=>({name:f.name,type:f.type,label:f.getAttribute('aria-label')||f.labels?.[0]?.innerText||''}))}))"
                                ),
                                "previous_actions": model_reasons,
                            },
                        )
                        if not plan.actions:
                            raise FormAutomationError(
                                f"OpenRouter found no safe navigation action: {plan.reason}"
                            )
                        action = plan.actions[0]
                        if action.action == "login":
                            host = (urlparse(page.url).hostname or "").lower()
                            credentials = accounts.get(host) or accounts.get(
                                host.removeprefix("www.")
                            )
                            if credentials is None:
                                raise FormAutomationError(
                                    f"OpenRouter identified a required provider login, but no credentials "
                                    f"exist in accounts.yaml for {host or 'the current provider'}"
                                )
                            login_to_provider(page, credentials)
                        else:
                            apply_navigation_plan(page, plan)
                            _settle_page(page)
                            dismiss_cookie_banner(page)
                        model_reasons.append(plan.reason)
                    except (
                        OpenRouterPlanningError,
                        FormAutomationError,
                    ) as model_error:
                        raise FormAutomationError(
                            f"generic form matching failed: {generic_reason}; "
                            f"OpenRouter fallback failed: {model_error}"
                        ) from model_error
            if submit:
                if database is None:
                    raise RuntimeError("Submit mode requires durable database intent")
                _submit_prepared_form(
                    page,
                    work,
                    database,
                    screenshot_directory,
                    screenshot_key,
                    allow_credit_check=allow_credit_check,
                )
                return screenshot_key, filled, "provider confirmation observed"
            page.screenshot(path=str(temporary_path), full_page=True)
            temporary_path.replace(screenshot_path)
            return screenshot_key, filled, "; ".join(model_reasons) or None
        except Exception:
            try:
                failure_key = "failure-" + screenshot_key
                page.screenshot(
                    path=str(screenshot_directory / failure_key),
                    full_page=True,
                    timeout=5000,
                )
                if database:
                    database.record_provider_evidence(work, failure_key, "failure")
            except Exception:
                pass
            raise
        finally:
            page.close()
            context.close()
            browser.close()


def process_once(
    database: PipelineDatabase,
    screenshot_directory: Path,
    *,
    profile: ApplicantProfile | None = None,
    planner: OpenRouterFormPlanner | None = None,
    accounts: dict[str, AccountCredentials] | None = None,
    lease_seconds: int = 1_800,
    max_attempts: int = 3,
    submit: bool = False,
    allow_credit_check: bool = False,
) -> int:
    """Process one provider page; submit mode persists intent and confirmation."""
    work = database.claim_next_provider_listing(
        lease_seconds=lease_seconds, max_attempts=max_attempts
    )
    if work is None:
        return 0
    try:
        if profile is None:
            screenshot_key = _capture_for_review(work, screenshot_directory)
            fields: list[str] = []
            model_reason = None
            screenshot_stage = "capture"
        else:
            if planner is None:
                raise RuntimeError(
                    "OpenRouter planner was not configured for form processing"
                )
            screenshot_key, fields, model_reason = _fill_for_review(
                work,
                screenshot_directory,
                profile,
                planner,
                accounts or {},
                **(
                    {
                        "submit": True,
                        "database": database,
                        "allow_credit_check": allow_credit_check,
                    }
                    if submit
                    else {}
                ),
            )
            screenshot_stage = "after_fill"
        if not submit:
            database.record_provider_evidence(work, screenshot_key, screenshot_stage)
            database.mark_provider_listing_failed(
                work, "dry_run: evidence captured; no application was sent"
            )
        logging.info(
            "Provider listing complete | title=%r | provider_url=%s | screenshot=%s "
            "| rooms=%s | fields=%s | model_navigation_reasons=%r",
            work.title or "(no title)",
            work.resolved_url,
            screenshot_key,
            work.room_count if work.room_count is not None else "unknown",
            ",".join(fields) or "none",
            model_reason,
        )
        return 1
    except Exception as error:
        reason = _diagnostic(error)
        database.mark_provider_listing_failed(work, reason)
        logging.error(
            "Provider listing failed "
            "| title=%r | provider_url=%s | attempt=%d | error=%s",
            work.title or "(no title)",
            work.resolved_url,
            work.attempt_count,
            reason,
        )
        return 0


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Prepare or submit provider applications with confirmation evidence"
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Send contact requests; require provider confirmation",
    )
    parser.add_argument(
        "--allow-credit-check",
        action="store_true",
        help="Authorize provider credit-check consent",
    )
    parser.add_argument("--config", default="values.yaml")
    parser.add_argument(
        "--accounts",
        default=None,
        help="Ignored provider-login accounts.yaml; defaults beside --config",
    )
    parser.add_argument("--database", default=None)
    parser.add_argument("--screenshots-dir", default="/app/screenshots")
    parser.add_argument("--lease-seconds", type=int, default=1_800)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    args = parser.parse_args()
    if args.lease_seconds <= 0:
        parser.error("--lease-seconds must be positive")
    if args.max_attempts <= 0:
        parser.error("--max-attempts must be positive")
    if args.poll_seconds < DEFAULT_POLL_SECONDS:
        parser.error(f"--poll-seconds must be at least {DEFAULT_POLL_SECONDS}")

    settings = load_settings(args.config)
    profile = submission_profile(settings)
    database = PipelineDatabase(args.database or settings.database)
    screenshot_directory = Path(args.screenshots_dir)
    accounts_path = (
        Path(args.accounts)
        if args.accounts
        else Path(args.config).with_name("accounts.yaml")
    )
    accounts = load_accounts(accounts_path)
    planner = OpenRouterFormPlanner(
        settings.openrouter_api_key, settings.openrouter_model
    )
    try:
        while True:
            profile = submission_profile(load_settings(args.config))
            problems = submission_problems(profile) if args.submit else []
            if problems:
                logging.error(
                    "Application submissions paused | %s", "; ".join(problems)
                )
                if args.once:
                    return 1
                time.sleep(args.poll_seconds)
                continue
            handled_total = 0
            # Drain the currently pending queue in this cycle. A failed item
            # is terminal, so process_once() returns 0 for both "no work" and
            # "one item failed"; checking the pending queue distinguishes the
            # two and prevents a five-minute delay between every listing.
            while True:
                handled_total += process_once(
                    database,
                    screenshot_directory,
                    profile=profile,
                    planner=planner,
                    accounts=accounts,
                    lease_seconds=args.lease_seconds,
                    max_attempts=args.max_attempts,
                    submit=args.submit,
                    allow_credit_check=args.allow_credit_check,
                )
                if not database.claimable_provider_listings_exist(
                    max_attempts=args.max_attempts
                ):
                    break
            logging.info(
                "Provider application cycle complete | completed_listings=%d",
                handled_total,
            )
            if args.once:
                return 0
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        logging.info("SIGINT received; shutting down provider review worker")
        return 130
    except Exception:
        logging.exception("Provider review worker failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
