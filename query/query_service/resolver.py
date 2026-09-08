from __future__ import annotations

import logging
import random
import time
from pathlib import Path

import requests
from urllib.parse import urlparse

from .config import load_settings
from .store import ListingStore


def resolve_once(listings: ListingStore, cookie: str, sleeper=time.sleep, limit: int = 50) -> int:
    resolved = 0
    for item in listings.unread_listings(limit=limit):
        response = requests.get(item.url, headers={"Cookie": cookie}, allow_redirects=True, timeout=20)
        final_path = urlparse(response.url).path
        is_stekkies_listing = "/api/v1/h/redirect/" in final_path
        if response.status_code >= 400 or ("stekkies.com" in response.url and not is_stekkies_listing):
            logging.error(
                "Could not resolve Stekkies listing | source_url=%s | final_url=%s | status=%s",
                item.url,
                response.url,
                response.status_code,
            )
            continue
        logging.info("Resolved listing URL | source_url=%s | resolved_url=%s", item.url, response.url)
        listings.mark_read(item.source_signature, item.url)
        resolved += 1
        sleeper(random.uniform(20, 35))
    return resolved


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Resolve Stekkies links with a browser session cookie")
    parser.add_argument("--config", default="values.yaml")
    parser.add_argument("--listings-database", default=None)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = load_settings(args.config)
    if not settings.stekkies_cookie:
        parser.error("values.yaml requires stekkies_cookie")
    listings = ListingStore(args.listings_database or settings.listings_database)
    try:
        while True:
            resolve_once(listings, settings.stekkies_cookie, limit=args.limit)
            if args.once:
                return 0
            time.sleep(300)
    finally:
        listings.close()


if __name__ == "__main__":
    raise SystemExit(main())
