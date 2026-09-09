"""Resolver-group tests using a local fake Playwright browser."""

import pytest

from query_service.database import PipelineDatabase
from query_service.resolver import parse_cookie_header, resolve_once


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


@pytest.mark.unit
def test_cookie_parser_builds_stekkies_browser_context_cookie() -> None:
    """Cookie header values become secure Playwright cookies for Stekkies only."""
    cookies = parse_cookie_header("session=abc==; preferences=dark")

    assert cookies[0]["name"] == "session"
    assert cookies[0]["value"] == "abc=="
    assert cookies[0]["url"] == "https://www.stekkies.com"
    assert cookies[1]["name"] == "preferences"


@pytest.mark.resolver
def test_resolver_persists_external_url_before_marking_source_read(tmp_path) -> None:
    """A successful browser visit writes the durable output then advances input."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    database.add_listing_if_new(
        "email-1", "https://email.stekkies.com/e/c/42", "Go to listing", "Listings"
    )
    fake = FakePlaywright()

    count = resolve_once(
        database, "session=abc", sleeper=lambda _seconds: None,
        playwright_factory=lambda: fake,
    )

    assert count == 1
    assert database.unread_listings() == []
    queued = database.unread_resolved_listings()
    assert len(queued) == 1
    assert queued[0].resolved_url == "https://provider.example/listings/42"
    assert fake.context.cookies[0]["name"] == "session"


@pytest.mark.resolver
def test_resolver_logs_context_and_retries_when_click_fails(caplog, tmp_path) -> None:
    """An expired browser session logs diagnostics and leaves the listing unread."""
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    database.add_listing_if_new(
        "email-1", "https://email.stekkies.com/e/c/42", "View match", "Listings"
    )

    with caplog.at_level("ERROR"):
        count = resolve_once(
            database, "session=abc", sleeper=lambda _seconds: None,
            playwright_factory=lambda: FakePlaywright(RuntimeError("expired session")),
        )

    assert count == 0
    assert len(database.unread_listings()) == 1
    assert database.unread_resolved_listings() == []
    assert "leaving it unread for retry" in caplog.text
    assert "step=locating Go to listing action" in caplog.text
    assert "current_url=https://www.stekkies.com/e/c/42" in caplog.text
