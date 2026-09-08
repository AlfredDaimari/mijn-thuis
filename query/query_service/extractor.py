from __future__ import annotations

import re
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlparse

from .yahoo import URL_PATTERN, _body_text


@dataclass(frozen=True)
class Listing:
    url: str
    title: str | None


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.current_url: str | None = None
        self.current_text: list[str] = []
        self.anchors: list[Listing] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href and href.startswith(("http://", "https://")):
                self.current_url = href
                self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.current_url is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current_url is not None:
            title = " ".join("".join(self.current_text).split()) or None
            self.anchors.append(Listing(self.current_url, title))
            self.current_url = None
            self.current_text = []


ASSET_SUFFIXES = (".avif", ".css", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg", ".webp")
NON_LISTING_HOSTS = ("email.stekkies.com", "track.customer.io", "fonts.googleapis.com")


def _is_listing_url(url: str) -> bool:
    """Reject email plumbing/assets while retaining Stekkies and direct listing links."""
    parsed = urlparse(unescape(url))
    host = parsed.netloc.lower().split(":")[0]
    path = parsed.path.lower()
    if not host or host in NON_LISTING_HOSTS:
        return False
    if path.endswith(ASSET_SUFFIXES):
        return False
    if any(segment in path for segment in ("/images/", "/media/", "/publicphotos/", "/static/")):
        return False
    if "unsubscribe" in path or "subscription_preferences" in path:
        return False
    # Other Stekkies pages are marketing/navigation. Listing email links use
    # their redirect endpoint before handing off to the housing provider.
    if host.endswith("stekkies.com") and "/api/v1/redirect/" not in path:
        return False
    return True


def extract_listings(raw_message: bytes) -> list[Listing]:
    """Extract unique HTTP(S) listing links and optional anchor titles from an email."""
    message: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw_message)
    body = _body_text(message)
    found: list[Listing] = []

    parser = _AnchorParser()
    parser.feed(body)
    found.extend(parser.anchors)
    found.extend(Listing(url, None) for url in URL_PATTERN.findall(body))

    unique: dict[str, Listing] = {}
    for listing in found:
        normalized_url = unescape(listing.url)
        if _is_listing_url(normalized_url):
            unique.setdefault(normalized_url, Listing(normalized_url, listing.title))
    return list(unique.values())
