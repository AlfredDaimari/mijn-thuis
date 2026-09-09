"""No-submit provider form worker with a constrained Gemini fallback.

The worker may fill known contact fields and use a reviewed, allow-listed Gemini
navigation suggestion to reveal a form. It never accepts consent or submits a
provider application.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import time
from pathlib import Path
from urllib.parse import urlparse

from .__main__ import DEFAULT_POLL_SECONDS, configure_logging
from .config import AccountCredentials, ApplicantProfile, load_accounts, load_settings
from .database import ApplicationWork, PipelineDatabase
from .form_automation import (
    FormAutomationError,
    apply_navigation_plan,
    fill_generic_form,
    login_to_provider,
    visible_navigation_candidates,
)
from .gemini_form_planner import GeminiFormPlanner, GeminiPlanningError
from .provider_adapters import flow_for


DEFAULT_TIMEOUT_MS = 30_000
MAX_GEMINI_STEPS = 3


def _screenshot_key(work: ApplicationWork) -> str:
    identity = "\n".join((work.source_signature, work.source_url, work.resolved_url))
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
            page.goto(work.resolved_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
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
    planner: GeminiFormPlanner,
    accounts: dict[str, AccountCredentials],
) -> tuple[str, list[str], str | None]:
    """Fill recognised fields, asking Gemini only after generic matching fails.

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
    gemini_reasons: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(work.resolved_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
            provider_flow = flow_for(work.resolved_url)
            if provider_flow is not None:
                host = (urlparse(page.url).hostname or "").lower()
                credentials = accounts.get(host) or accounts.get(host.removeprefix("www."))
                filled = provider_flow(page, profile, credentials)
                gemini_reasons.append("provider-specific Playwright flow")
            else:
                for step in range(MAX_GEMINI_STEPS + 1):
                    try:
                        filled = fill_generic_form(page, profile)
                        break
                    except FormAutomationError as generic_error:
                        generic_reason = str(generic_error)
                    if step == MAX_GEMINI_STEPS:
                        raise FormAutomationError(
                            f"generic form matching failed: {generic_reason}; "
                            f"Gemini fallback exhausted after {MAX_GEMINI_STEPS} steps"
                        )
                    try:
                        plan = planner.plan(
                            work.resolved_url,
                            generic_reason,
                            visible_navigation_candidates(page),
                        )
                        if not plan.actions:
                            raise FormAutomationError(
                                f"Gemini found no safe navigation action: {plan.reason}"
                            )
                        action = plan.actions[0]
                        if action.action == "login":
                            host = (urlparse(page.url).hostname or "").lower()
                            credentials = accounts.get(host) or accounts.get(host.removeprefix("www."))
                            if credentials is None:
                                raise FormAutomationError(
                                    f"Gemini identified a required provider login, but no credentials "
                                    f"exist in accounts.yaml for {host or 'the current provider'}"
                                )
                            login_to_provider(page, credentials)
                        else:
                            apply_navigation_plan(page, plan)
                        gemini_reasons.append(plan.reason)
                    except (GeminiPlanningError, FormAutomationError) as gemini_error:
                        raise FormAutomationError(
                            f"generic form matching failed: {generic_reason}; "
                            f"Gemini fallback failed: {gemini_error}"
                        ) from gemini_error
            page.screenshot(path=str(temporary_path), full_page=True)
            temporary_path.replace(screenshot_path)
            return screenshot_key, filled, "; ".join(gemini_reasons) or None
        finally:
            page.close()
            context.close()
            browser.close()


def process_once(
    database: PipelineDatabase,
    screenshot_directory: Path,
    *,
    profile: ApplicantProfile | None = None,
    planner: GeminiFormPlanner | None = None,
    accounts: dict[str, AccountCredentials] | None = None,
    lease_seconds: int = 1_800,
    max_attempts: int = 3,
) -> int:
    """Process one provider page and stop before any irreversible submission."""
    work = database.claim_next_application(
        lease_seconds=lease_seconds, max_attempts=max_attempts
    )
    if work is None:
        return 0
    try:
        if profile is None:
            screenshot_key = _capture_for_review(work, screenshot_directory)
            fields: list[str] = []
            gemini_reason = None
        else:
            if planner is None:
                raise RuntimeError("Gemini planner was not configured for form processing")
            screenshot_key, fields, gemini_reason = _fill_for_review(
                work, screenshot_directory, profile, planner, accounts or {}
            )
        database.mark_application_awaiting_review(work, screenshot_key)
        logging.info(
            "Provider listing ready for review | title=%r | provider_url=%s | screenshot=%s "
            "| fields=%s | gemini_navigation_reasons=%r",
            work.title or "(no title)", work.resolved_url, screenshot_key,
            ",".join(fields) or "none", gemini_reason,
        )
        return 1
    except Exception as error:
        database.mark_application_failed(work, str(error))
        logging.exception(
            "Provider listing capture failed; marked retryable failure "
            "| title=%r | provider_url=%s | attempt=%d | error=%s",
            work.title or "(no title)", work.resolved_url, work.attempt_count, error,
        )
        return 0


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Fill provider forms without submitting them")
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
    database = PipelineDatabase(args.database or settings.database)
    screenshot_directory = Path(args.screenshots_dir)
    accounts_path = Path(args.accounts) if args.accounts else Path(args.config).with_name("accounts.yaml")
    accounts = load_accounts(accounts_path)
    planner = GeminiFormPlanner(settings.gemini_api_key, settings.gemini_model)
    try:
        while True:
            handled = process_once(
                database,
                screenshot_directory,
                profile=settings.applicant,
                planner=planner,
                accounts=accounts,
                lease_seconds=args.lease_seconds,
                max_attempts=args.max_attempts,
            )
            logging.info("Provider review worker complete | captured_listings=%d", handled)
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
