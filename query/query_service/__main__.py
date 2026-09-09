from __future__ import annotations

import argparse
import logging
import time

from .config import load_settings
from .database import EmailDatabase
from .service import StekkiesQueryService
from .yahoo import YahooMailbox

DEFAULT_POLL_SECONDS = 300


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Read new Stekkies emails from Yahoo Mail")
    parser.add_argument("--config", default="values.yaml", help="Path to values.yaml")
    parser.add_argument(
        "--database",
        default=None,
        help="Staging SQLite path; overrides staging_database in values.yaml",
    )
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=DEFAULT_POLL_SECONDS,
        help=f"Polling interval; minimum {DEFAULT_POLL_SECONDS} seconds",
    )
    args = parser.parse_args()
    if args.poll_seconds < DEFAULT_POLL_SECONDS:
        parser.error(f"--poll-seconds must be at least {DEFAULT_POLL_SECONDS}")

    settings = load_settings(args.config)
    database = EmailDatabase(args.database or settings.staging_database)
    mailbox = YahooMailbox(settings.email, settings.password)
    service = StekkiesQueryService(mailbox, database)
    try:
        while True:
            new_messages = service.poll_once()
            for message in new_messages:
                logging.info(
                    "Found new Stekkies listing email | email_received_at=%s | subject=%r",
                    message.received_at or "unknown",
                    message.subject or "(no subject)",
                )
            logging.info("Stekkies mailbox poll complete | new_listing_emails=%d", len(new_messages))
            if args.once:
                return 0
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        logging.info("SIGINT received; shutting down Stekkies query service")
        return 130
    except Exception:
        logging.exception("Yahoo mailbox poll failed")
        return 1
    finally:
        mailbox.close()


if __name__ == "__main__":
    raise SystemExit(main())
