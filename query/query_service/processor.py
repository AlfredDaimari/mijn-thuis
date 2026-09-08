from __future__ import annotations

import argparse
import logging
import time

from .__main__ import DEFAULT_POLL_SECONDS, configure_logging
from .config import load_settings
from .extractor import extract_listings
from .store import ListingStore, SeenEmailStore


def process_once(staging_store: SeenEmailStore, listing_store: ListingStore) -> int:
    """Process the unread queue in one process; return the number handled."""
    handled = 0
    for email in staging_store.unread_emails():
        listings = extract_listings(email.raw_message)
        if not listings:
            error = "No listing links could be extracted from a Stekkies email"
            logging.error(
                "%s | message_id=%s | subject=%r",
                error,
                email.message_id or "unknown",
                email.subject or "(no subject)",
            )
            staging_store.mark_read(email.signature, extraction_error=error)
            handled += 1
            continue

        for listing in listings:
            if listing_store.add_if_new(
                email.signature, listing.url, listing.title, email.subject
            ):
                logging.info(
                    "Queued Stekkies listing | message_id=%s | subject=%r | title=%r | url=%s",
                    email.message_id or "unknown",
                    email.subject or "(no subject)",
                    listing.title,
                    listing.url,
                )
        staging_store.mark_read(email.signature)
        handled += 1
    return handled


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Extract listings from queued Stekkies emails")
    parser.add_argument("--config", default="values.yaml", help="Path to values.yaml")
    parser.add_argument(
        "--staging-database",
        default=None,
        help="Email staging SQLite path; overrides staging_database in values.yaml",
    )
    parser.add_argument(
        "--listings-database",
        default=None,
        help="Listings SQLite path; overrides listings_database in values.yaml",
    )
    parser.add_argument("--once", action="store_true", help="Process queued emails once and exit")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    args = parser.parse_args()
    if args.poll_seconds < DEFAULT_POLL_SECONDS:
        parser.error(f"--poll-seconds must be at least {DEFAULT_POLL_SECONDS}")

    settings = load_settings(args.config)
    staging_store = SeenEmailStore(args.staging_database or settings.staging_database)
    listing_store = ListingStore(args.listings_database or settings.listings_database)
    try:
        while True:
            handled = process_once(staging_store, listing_store)
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
    finally:
        staging_store.close()
        listing_store.close()


if __name__ == "__main__":
    raise SystemExit(main())
