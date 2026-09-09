from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable, MutableMapping
from urllib.parse import urlparse

from .config import load_settings
from .database import ListingDatabase, ResolvedListingDatabase


DEFAULT_TIMEOUT_MS = 30_000
# Stekkies currently renders this as “Go to listing”; older and Dutch-email
# flows have used the other labels. Keep all known labels to avoid coupling the
# resolver to presentation copy.
VIEW_LISTING_PATTERN = re.compile(
    r"(?:view\s+(?:listing|match)|go\s+to\s+listing|ga\s+naar.*(?:woning|listing))",
    re.IGNORECASE,
)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def parse_cookie_header(cookie_header: str) -> list[dict[str, str | bool]]:
    """Convert a browser Cookie request header into Playwright cookie objects.

    A Cookie header intentionally contains only ``name=value`` pairs. Adding
    the Stekkies URL here keeps host-only (including ``__Host-``) cookies
    valid, without putting the secret in an actual navigation URL.
    """
    cookies: list[dict[str, str | bool]] = []
    for part in cookie_header.split(";"):
        name, separator, value = part.strip().partition("=")
        if not separator or not name:
            raise ValueError("stekkies_cookie must be a Cookie header of name=value pairs")
        cookies.append(
            {
                "name": name,
                "value": value,
                "url": "https://www.stekkies.com",
                "secure": True,
            }
        )
    if not cookies:
        raise ValueError("stekkies_cookie must contain at least one name=value pair")
    return cookies


def _is_stekkies_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == "stekkies.com" or host.endswith(".stekkies.com")


def _page_diagnostics(page) -> tuple[str, str]:
    """Return useful failure context without exposing session credentials."""
    try:
        current_url = page.url
    except Exception:
        current_url = "unavailable"
    try:
        page_title = page.title()
    except Exception:
        page_title = "unavailable"
    return current_url, page_title


def _view_listing(page, source_url: str, attempt: MutableMapping[str, str]):
    """Follow the email link and click its View listing control.

    The control can navigate the existing tab or open a new one, so the caller
    receives whichever page now displays the destination.
    """
    attempt["step"] = "opening email View match link"
    page.goto(source_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    try:
        # Some third-party analytics connections never become idle. The page
        # itself is already usable after DOM content loads, so do not turn
        # that into a failed listing attempt.
        page.wait_for_load_state("networkidle", timeout=5_000)
    except Exception:
        pass
    if not _is_stekkies_url(page.url):
        attempt["step"] = "checking redirected destination"
        attempt["final_url"] = page.url
        return page

    attempt["step"] = "locating Go to listing action"
    control = page.get_by_role("link", name=VIEW_LISTING_PATTERN).first
    control_role = "link"
    try:
        control.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
    except Exception:
        control = page.get_by_role("button", name=VIEW_LISTING_PATTERN).first
        control_role = "button"
        control.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)

    attempt["action_role"] = control_role
    action_href = control.get_attribute("href")
    attempt["action_href"] = action_href or "none"
    attempt["step"] = "clicking listing action"
    pages_before = list(page.context.pages)
    control.click(timeout=DEFAULT_TIMEOUT_MS)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    except Exception:
        # A button may open a new tab, or a slow provider page may not emit a
        # load event. The final URL check below supplies the useful error.
        pass
    new_pages = [candidate for candidate in page.context.pages if candidate not in pages_before]
    destination = new_pages[-1] if new_pages else page
    if not new_pages and _is_stekkies_url(destination.url) and action_href and not _is_stekkies_url(action_href):
        # The live Stekkies control is an external target=_blank link. Some
        # headless Chromium environments do not surface that tab after a
        # click, although the public href is rendered and verified. Open that
        # exact href in a new local browser page as the target-blank fallback.
        attempt["step"] = "opening target-blank listing href"
        destination = page.context.new_page()
        destination.goto(action_href, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    try:
        destination.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    except Exception:
        pass
    attempt["step"] = "checking destination after listing click"
    attempt["final_url"] = destination.url
    return destination


def resolve_once(
    listings_database: ListingDatabase,
    resolved_database: ResolvedListingDatabase,
    cookie: str,
    sleeper: Callable[[float], None] = time.sleep,
    limit: int = 50,
    playwright_factory: Callable[[], object] | None = None,
) -> int:
    """Resolve unread Stekkies links, retaining failures for a later retry."""
    if playwright_factory is None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Playwright is not installed. Run: python3 -m pip install -r requirements.txt "
                "and then: python3 -m playwright install chromium"
            ) from error
        playwright_factory = sync_playwright

    queued = listings_database.unread_listings(limit=limit)
    if not queued:
        return 0

    resolved = 0
    with playwright_factory() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        try:
            context.add_cookies(parse_cookie_header(cookie))
            for item in queued:
                page = context.new_page()
                attempt: dict[str, str] = {"step": "creating browser page", "action_role": "none"}
                try:
                    destination = _view_listing(page, item.url, attempt)
                    if _is_stekkies_url(destination.url):
                        raise RuntimeError(
                            "the View listing control did not leave Stekkies; "
                            "the session cookie may be expired or incomplete"
                        )
                    resolved_database.add_if_new(item, destination.url)
                    listings_database.mark_read(item.source_signature, item.url)
                    logging.info(
                        "Resolved listing URL | title=%s | source_url=%s | resolved_url=%s",
                        item.title or "(no title)",
                        item.url,
                        destination.url,
                    )
                    resolved += 1
                except Exception as error:
                    current_url, page_title = _page_diagnostics(page)
                    logging.exception(
                        "Could not resolve Stekkies listing; leaving it unread for retry "
                        "| title=%s | source_url=%s | step=%s | action_role=%s "
                        "| current_url=%s | final_url=%s | action_href=%s | page_title=%r | error=%s",
                        item.title or "(no title)",
                        item.url,
                        attempt["step"],
                        attempt["action_role"],
                        current_url,
                        attempt.get("final_url", "not reached"),
                        attempt.get("action_href", "not reached"),
                        page_title,
                        error,
                    )
                finally:
                    page.close()
                # This is deliberately between attempts, including failures:
                # an invalid-session retry should not hammer Stekkies.
                sleeper(random.uniform(20, 35))
        finally:
            context.close()
            browser.close()
    return resolved


def main() -> int:
    import argparse

    configure_logging()
    parser = argparse.ArgumentParser(description="Resolve Stekkies links with local Playwright")
    parser.add_argument("--config", default="values.yaml")
    parser.add_argument("--listings-database", default=None)
    parser.add_argument("--resolved-database", default=None)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = load_settings(args.config)
    if not settings.stekkies_cookie:
        parser.error("values.yaml requires stekkies_cookie")
    listings_database = ListingDatabase(args.listings_database or settings.listings_database)
    resolved_database = ResolvedListingDatabase(args.resolved_database or settings.resolved_database)
    while True:
        resolve_once(listings_database, resolved_database, settings.stekkies_cookie, limit=args.limit)
        if args.once:
            return 0
        time.sleep(300)


if __name__ == "__main__":
    raise SystemExit(main())
