from __future__ import annotations

import hashlib
import imaplib
import re
import ssl
from dataclasses import dataclass
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from typing import Protocol

YAHOO_IMAP_HOST = "imap.mail.yahoo.com"
YAHOO_IMAP_PORT = 993
STEKKIES_DOMAIN = "stekkies.com"
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")


class ImapClient(Protocol):
    def login(self, user: str, password: str): ...

    def select(self, mailbox: str, readonly: bool = False): ...

    def uid(self, command: str, *args: str): ...

    def logout(self): ...


@dataclass(frozen=True)
class ListingEmail:
    signature: str
    message_id: str
    sender: str
    subject: str
    received_at: str
    links: tuple[str, ...]
    raw_message: bytes


def _header(message: EmailMessage, name: str) -> str:
    value = message.get(name, "")
    return str(make_header(decode_header(value))) if value else ""


def _body_text(message: EmailMessage) -> str:
    parts = message.walk() if message.is_multipart() else [message]
    text_parts: list[str] = []
    for part in parts:
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        try:
            text_parts.append(part.get_content())
        except (LookupError, UnicodeDecodeError):
            payload = part.get_payload(decode=True) or b""
            text_parts.append(payload.decode("utf-8", errors="replace"))
    return "\n".join(text_parts)


def _is_stekkies_sender(sender: str) -> bool:
    address = parseaddr(sender)[1].lower()
    return address == STEKKIES_DOMAIN or address.endswith(f"@{STEKKIES_DOMAIN}")


def parse_listing_email(raw_message: bytes) -> ListingEmail | None:
    """Return a Stekkies message and stable content signature, or None."""
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    sender = _header(message, "From")
    if not _is_stekkies_sender(sender):
        return None

    subject = _header(message, "Subject")
    message_id = _header(message, "Message-ID")
    received_at = _header(message, "Date")
    body = _body_text(message)
    # Message-ID is preferred; the digest prevents a changed/reused ID from
    # hiding a different message and provides a fallback for emails without one.
    signature_input = "\n".join((message_id, sender, subject, body))
    signature = hashlib.sha256(signature_input.encode("utf-8")).hexdigest()
    links = tuple(dict.fromkeys(URL_PATTERN.findall(body)))
    return ListingEmail(signature, message_id, sender, subject, received_at, links, raw_message)


class YahooMailbox:
    """Read matching email through Yahoo IMAP without changing read state."""

    def __init__(self, email: str, password: str, client: ImapClient | None = None) -> None:
        self.email = email
        self.password = password
        self.client = client

    def _connect(self) -> ImapClient:
        if self.client is None:
            self.client = imaplib.IMAP4_SSL(
                YAHOO_IMAP_HOST,
                YAHOO_IMAP_PORT,
                ssl_context=ssl.create_default_context(),
            )
        return self.client

    def fetch_stekkies_messages(self) -> list[ListingEmail]:
        client = self._connect()
        client.login(self.email, self.password)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Could not open Yahoo INBOX read-only")

        # This server-side filter avoids downloading unrelated mailbox content.
        status, data = client.uid("SEARCH", None, "FROM", STEKKIES_DOMAIN)
        if status != "OK":
            raise RuntimeError("Could not search Yahoo INBOX")

        messages: list[ListingEmail] = []
        for uid in data[0].split():
            status, fetched = client.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK" or not fetched:
                continue
            raw_message = next(
                (item[1] for item in fetched if isinstance(item, tuple) and isinstance(item[1], bytes)),
                None,
            )
            if raw_message is None:
                continue
            listing_email = parse_listing_email(raw_message)
            if listing_email is not None:
                messages.append(listing_email)
        return messages

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.logout()
            finally:
                self.client = None
