import tempfile
import unittest
from pathlib import Path

from query_service.resolver import parse_cookie_header, resolve_once
from query_service.database import ListingDatabase, ResolvedListingDatabase


class FakeLocator:
    def __init__(self, page, error: Exception | None = None) -> None:
        self.page = page
        self.error = error

    def wait_for(self, **_kwargs) -> None:
        if self.error:
            raise self.error

    def click(self, **_kwargs) -> None:
        if self.error:
            raise self.error
        self.page.url = "https://provider.example/listings/42"

    def get_attribute(self, name: str):
        return "https://provider.example/listings/42" if name == "href" else None

    @property
    def first(self):
        return self


class FakePage:
    def __init__(self, context, click_error: Exception | None = None) -> None:
        self.context = context
        self.url = "about:blank"
        self.click_error = click_error

    def goto(self, url: str, **_kwargs) -> None:
        self.url = url.replace("email.", "www.")

    def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None

    def get_by_role(self, role: str, **_kwargs):
        if role == "link":
            return FakeLocator(self, self.click_error)
        return FakeLocator(self, RuntimeError("button fallback should not be used"))

    def close(self) -> None:
        return None


class FakeContext:
    def __init__(self, click_error: Exception | None = None) -> None:
        self.pages: list[FakePage] = []
        self.cookies = []
        self.click_error = click_error

    def add_cookies(self, cookies) -> None:
        self.cookies = cookies

    def new_page(self) -> FakePage:
        page = FakePage(self, self.click_error)
        self.pages.append(page)
        return page

    def close(self) -> None:
        return None


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context

    def new_context(self) -> FakeContext:
        return self.context

    def close(self) -> None:
        return None


class FakePlaywright:
    def __init__(self, click_error: Exception | None = None) -> None:
        self.context = FakeContext(click_error)
        self.chromium = self

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def launch(self, **_kwargs) -> FakeBrowser:
        return FakeBrowser(self.context)


class ResolverTests(unittest.TestCase):
    def test_parses_cookie_header_for_the_stekkies_browser_context(self) -> None:
        cookies = parse_cookie_header("session=abc==; preferences=dark")
        self.assertEqual(cookies[0]["name"], "session")
        self.assertEqual(cookies[0]["value"], "abc==")
        self.assertEqual(cookies[0]["url"], "https://www.stekkies.com")
        self.assertEqual(cookies[1]["name"], "preferences")

    def test_resolver_writes_external_url_then_marks_source_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            listings = ListingDatabase(Path(directory) / "listings.sqlite3")
            resolved = ResolvedListingDatabase(Path(directory) / "resolved.sqlite3")
            listings.add_if_new("email-1", "https://email.stekkies.com/e/c/42", "Go to listing", "Listings")
            fake = FakePlaywright()

            count = resolve_once(
                listings,
                resolved,
                "session=abc",
                sleeper=lambda _seconds: None,
                playwright_factory=lambda: fake,
            )

            self.assertEqual(count, 1)
            self.assertEqual(listings.unread_listings(), [])
            queued = resolved.unread_listings()
            self.assertEqual(len(queued), 1)
            self.assertEqual(queued[0].resolved_url, "https://provider.example/listings/42")
            self.assertEqual(fake.context.cookies[0]["name"], "session")

    def test_failed_click_is_logged_and_keeps_source_listing_unread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            listings = ListingDatabase(Path(directory) / "listings.sqlite3")
            resolved = ResolvedListingDatabase(Path(directory) / "resolved.sqlite3")
            listings.add_if_new("email-1", "https://email.stekkies.com/e/c/42", "View match", "Listings")

            with self.assertLogs(level="ERROR") as logs:
                count = resolve_once(
                    listings,
                    resolved,
                    "session=abc",
                    sleeper=lambda _seconds: None,
                    playwright_factory=lambda: FakePlaywright(RuntimeError("expired session")),
                )

            self.assertEqual(count, 0)
            self.assertEqual(len(listings.unread_listings()), 1)
            self.assertEqual(resolved.unread_listings(), [])
            self.assertIn("leaving it unread for retry", logs.output[0])
            self.assertIn("step=locating Go to listing action", logs.output[0])
            self.assertIn("current_url=https://www.stekkies.com/e/c/42", logs.output[0])
