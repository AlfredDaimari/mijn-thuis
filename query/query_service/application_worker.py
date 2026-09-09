"""Review-only provider worker for resolved housing listings.

This worker deliberately stops after capturing page evidence. It does not fill
forms, accept terms, or submit a provider application.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import time
from pathlib import Path

from .__main__ import DEFAULT_POLL_SECONDS, configure_logging
from .config import load_settings
from .database import ApplicationWork, PipelineDatabase


DEFAULT_TIMEOUT_MS = 30_000


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


def process_once(
    database: PipelineDatabase,
    screenshot_directory: Path,
    *,
    lease_seconds: int = 1_800,
    max_attempts: int = 3,
) -> int:
    """Capture one pending provider page and leave it awaiting human review."""
    work = database.claim_next_application(
        lease_seconds=lease_seconds, max_attempts=max_attempts
    )
    if work is None:
        return 0
    try:
        screenshot_key = _capture_for_review(work, screenshot_directory)
        database.mark_application_awaiting_review(work, screenshot_key)
        logging.info(
            "Provider listing captured for review | title=%r | provider_url=%s | screenshot=%s",
            work.title or "(no title)",
            work.resolved_url,
            screenshot_key,
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
    parser = argparse.ArgumentParser(
        description="Capture provider listing evidence for human application review"
    )
    parser.add_argument("--config", default="values.yaml")
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
    try:
        while True:
            handled = process_once(
                database,
                screenshot_directory,
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
