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
    room_count: int | None = None


@dataclass(frozen=True)
class ListingDetails:
    """Conservative display metadata found on a provider listing page.

    Every value is optional: providers use different page structures, and an
    unknown value is safer than presenting a guess in the user interface.
    """

    provider_title: str | None = None
    location: str | None = None
    monthly_rent_cents: int | None = None
    area_m2: int | None = None
    room_count: int | None = None


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
    # The actionable email button is a Stekkies tracking wrapper. It must be
    # retained exactly so the resolver can follow the same path as a click.
    if host == "email.stekkies.com" and path.startswith("/e/c/"):
        return True
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


def _is_listing_button(title: str | None) -> bool:
    normalized = (title or "").casefold()
    return "view match" in normalized or "view listing" in normalized


_ROOM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "een": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5,
}
_ROOM_PATTERN = re.compile(
    r"\b(?:(?P<number>\d{1,2})|(?P<word>one|two|three|four|five|een|twee|drie|vier|vijf))"
    r"\s*(?:-|\s)?(?:kamer(?:woning|appartement)?|kamers|room(?:s)?|bedroom(?:s)?)\b",
    re.IGNORECASE,
)


def extract_room_count(text: str | None) -> int | None:
    """Return a plausible Dutch or English room count from visible listing text."""
    if not text:
        return None
    match = _ROOM_PATTERN.search(text)
    if not match:
        return None
    room_count = int(match.group("number")) if match.group("number") else _ROOM_WORDS[match.group("word").lower()]
    return room_count if 1 <= room_count <= 20 else None


_LOCATION_PATTERN = re.compile(
    r"(?:locatie|plaats|adres|location|city|address)\s*[:\-]\s*([^\n|]{2,100})",
    re.IGNORECASE,
)
_RENT_PATTERN = re.compile(
    r"(?:huurprijs|huur|rent|rental price|prijs)\s*[:\-]?\s*(?:€|eur)\s*"
    r"(?P<amount>\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)",
    re.IGNORECASE,
)
_MONTHLY_PRICE_PATTERN = re.compile(
    r"(?:€|eur)\s*(?P<amount>\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)"
    r"\s*(?:p(?:er)?\.?\s*(?:m(?:aand)?|month)|/\s*(?:m(?:aand)?|month))",
    re.IGNORECASE,
)
_AREA_PATTERN = re.compile(r"\b(?P<area>\d{1,4})\s*(?:m²|m2)\b", re.IGNORECASE)


def _euro_amount_to_cents(amount: str) -> int | None:
    """Convert common Dutch/English euro formatting without using floats."""
    normalized = amount.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        euros, _, decimals = normalized.partition(".")
        cents = (decimals + "00")[:2]
        value = int(euros) * 100 + int(cents)
    except ValueError:
        return None
    return value if 0 < value <= 10_000_000 else None


def extract_listing_details(text: str | None, provider_title: str | None = None) -> ListingDetails:
    """Extract only explicitly labelled, broadly reusable housing facts.

    This deliberately skips unlabelled addresses and arbitrary euro amounts
    (for example deposits or service charges) instead of guessing.
    """
    text = text or ""
    location_match = _LOCATION_PATTERN.search(text)
    location = " ".join(location_match.group(1).split()) if location_match else None
    if location:
        location = location.rstrip(".,;:")

    rent_match = _RENT_PATTERN.search(text) or _MONTHLY_PRICE_PATTERN.search(text)
    rent_cents = _euro_amount_to_cents(rent_match.group("amount")) if rent_match else None
    area_match = _AREA_PATTERN.search(text)
    area_m2 = int(area_match.group("area")) if area_match else None
    if area_m2 is not None and not 5 <= area_m2 <= 2_000:
        area_m2 = None

    return ListingDetails(
        provider_title=" ".join(provider_title.split()) if provider_title else None,
        location=location,
        monthly_rent_cents=rent_cents,
        area_m2=area_m2,
        room_count=extract_room_count(text),
    )


def extract_listings(raw_message: bytes) -> list[Listing]:
    """Extract unique HTTP(S) listing links and optional anchor titles from an email."""
    message: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw_message)
    body = _body_text(message)
    found: list[Listing] = []

    parser = _AnchorParser()
    parser.feed(body)
    # Listing emails provide the actionable destination through this button.
    # Do not treat image, tracking, or marketing URLs as a house listing.
    found.extend(anchor for anchor in parser.anchors if _is_listing_button(anchor.title))

    unique: dict[str, Listing] = {}
    for listing in found:
        normalized_url = unescape(listing.url)
        if _is_listing_url(normalized_url):
            unique.setdefault(
                normalized_url,
                Listing(normalized_url, listing.title, extract_room_count(listing.title)),
            )
    return list(unique.values())
