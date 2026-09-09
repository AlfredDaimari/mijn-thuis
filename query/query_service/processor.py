from __future__ import annotations

import argparse
import logging
import time

from .__main__ import DEFAULT_POLL_SECONDS, configure_logging
from .config import load_settings
from .extractor import extract_listings
from .database import PipelineDatabase


def process_once(database: PipelineDatabase) -> int:
    """Process the unread queue in one process; return the number handled."""
    handled = 0
    for email in database.unread_emails():
        listings = extract_listings(email.raw_message)
        if not listings:
            error = "No listing links could be extracted from a Stekkies email"
            logging.error(
                "%s | message_id=%s | subject=%r",
                error,
                email.message_id or "unknown",
                email.subject or "(no subject)",
            )
            database.mark_email_read(email.signature, extraction_error=error)
            handled += 1
            continue

        queued = database.add_listings_and_mark_email_read(
            email, [(listing.url, listing.title) for listing in listings]
        )
        for listing in queued:
            logging.info(
                "Queued Stekkies listing | message_id=%s | subject=%r | title=%r | url=%s",
                email.message_id or "unknown",
                email.subject or "(no subject)",
                listing.title,
                listing.url,
            )
        handled += 1
    return handled


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Extract listings from queued Stekkies emails")
    parser.add_argument("--config", default="values.yaml", help="Path to values.yaml")
    parser.add_argument("--database", default=None, help="Pipeline SQLite path; overrides values.yaml")
    parser.add_argument("--once", action="store_true", help="Process queued emails once and exit")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    args = parser.parse_args()
    if args.poll_seconds < DEFAULT_POLL_SECONDS:
        parser.error(f"--poll-seconds must be at least {DEFAULT_POLL_SECONDS}")

    settings = load_settings(args.config)
    database = PipelineDatabase(args.database or settings.database)
    try:
        while True:
            handled = process_once(database)
            logging.info("Stekkies listing processor complete | handled_emails=%d", handled)
            if args.once:
                return 0
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        logging.info("SIGINT received; shutting down Stekkies listing processor")
        return 130
    except Exception:
        logging.exception("Stekkies listing processor failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
