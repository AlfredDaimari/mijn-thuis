"""Periodically rebuild error-summary rows from the pipeline source tables."""

from __future__ import annotations

import argparse
import logging
import time

from .config import load_settings
from .database import PipelineDatabase


DEFAULT_REFRESH_SECONDS = 7_200


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh derived pipeline error summaries")
    parser.add_argument("--config", default="values.yaml")
    parser.add_argument("--database", default=None)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_REFRESH_SECONDS)
    args = parser.parse_args()
    if args.poll_seconds < 3_600:
        parser.error("--poll-seconds must be at least 3600")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings(args.config)
    database = PipelineDatabase(args.database or settings.database)
    try:
        while True:
            groups = database.refresh_error_summaries()
            logging.info("Refreshed pipeline error summaries | groups=%d", groups)
            if args.once:
                return 0
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        logging.info("SIGINT received; shutting down error-summary worker")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
