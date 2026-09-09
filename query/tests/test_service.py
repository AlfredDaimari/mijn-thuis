"""Service-group tests for read-only Yahoo polling."""

from email.message import EmailMessage

import pytest

from query_service.database import PipelineDatabase
from query_service.service import StekkiesQueryService
from query_service.yahoo import YahooMailbox, parse_listing_email


def message_bytes(sender: str, subject: str, body: str, message_id: str) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message["Date"] = "Tue, 08 Sep 2026 09:30:00 +0200"
    message.set_content(body)
    return message.as_bytes()


class FakeImap:
    """Minimal IMAP double that records whether messages were fetched safely."""

    def __init__(self, messages: list[bytes]) -> None:
        self.messages = messages
        self.fetch_commands: list[tuple[object, ...]] = []

    def login(self, user: str, password: str):
        self.login_values = (user, password)
        return "OK", []

    def select(self, mailbox: str, readonly: bool = False):
        self.selected = (mailbox, readonly)
        return "OK", [b"2"]

    def uid(self, command: str, *args):
        if command == "SEARCH":
            return "OK", [b"1 2"]
        if command == "FETCH":
            self.fetch_commands.append(args)
            index = int(args[0]) - 1
            return "OK", [(b"RFC822", self.messages[index])]
        raise AssertionError(f"Unexpected IMAP command {command}")

    def logout(self):
        self.logged_out = True
        return "BYE", []


@pytest.mark.unit
def test_parser_accepts_a_stekkies_email_and_keeps_its_listing_link() -> None:
    """A Stekkies sender is parsed while unrelated URLs are left untouched."""
    parsed = parse_listing_email(
        message_bytes(
            "Stekkies <alerts@stekkies.com>", "New listings",
            "See https://stekkies.com/listing/42 and https://example.test/info",
            "<one@example.test>",
        )
    )

    assert parsed is not None
    assert parsed.links[0] == "https://stekkies.com/listing/42"
    assert parsed.received_at == "Tue, 08 Sep 2026 09:30:00 +0200"


@pytest.mark.unit
def test_parser_ignores_email_from_a_non_stekkies_sender() -> None:
    """Only sender addresses under the Stekkies domain enter the pipeline."""
    parsed = parse_listing_email(
        message_bytes("news@example.test", "Other", "https://example.test", "<two@example.test>")
    )

    assert parsed is None


@pytest.mark.service
def test_poller_queues_a_new_email_without_changing_yahoo_read_state(tmp_path) -> None:
    """Polling twice keeps one queue item and uses IMAP's read-only PEEK fetch."""
    raw_messages = [
        message_bytes(
            "Stekkies <alerts@stekkies.com>", "New listing", "https://stekkies.com/listing/42",
            "<listing@example.test>",
        ),
        message_bytes("news@example.test", "Other", "ignore", "<other@example.test>"),
    ]
    fake_imap = FakeImap(raw_messages)
    mailbox = YahooMailbox("person@yahoo.com", "app-password", client=fake_imap)
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    service = StekkiesQueryService(mailbox, database)

    first_poll = service.poll_once()
    second_poll = service.poll_once()
    queued = database.unread_emails()

    assert len(first_poll) == 1
    assert second_poll == []
    assert len(queued) == 1
    assert queued[0].raw_message == raw_messages[0]
    assert fake_imap.selected == ("INBOX", True)
    assert all(command[1] == "(BODY.PEEK[])" for command in fake_imap.fetch_commands)


@pytest.mark.service
def test_poller_queues_stekkies_email_without_listing_for_observable_processing(tmp_path) -> None:
    """A Stekkies newsletter stays queued so the processor can log its error."""
    fake_imap = FakeImap(
        [
            message_bytes(
                "Stekkies <alerts@stekkies.com>", "Newsletter", "No listing links today",
                "<newsletter@example.test>",
            ),
            message_bytes("news@example.test", "Other", "ignore", "<other@example.test>"),
        ]
    )
    mailbox = YahooMailbox("person@yahoo.com", "app-password", client=fake_imap)
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    service = StekkiesQueryService(mailbox, database)

    assert len(service.poll_once()) == 1
    assert len(database.unread_emails()) == 1
