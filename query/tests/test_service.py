import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from query_service.service import StekkiesQueryService
from query_service.store import SeenEmailStore
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


class QueryServiceTests(unittest.TestCase):
    def test_parses_stekkies_links_only(self) -> None:
        parsed = parse_listing_email(
            message_bytes(
                "Stekkies <alerts@stekkies.com>",
                "New listings",
                "See https://stekkies.com/listing/42 and https://example.test/info",
                "<one@example.test>",
            )
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.links[0], "https://stekkies.com/listing/42")
        self.assertEqual(parsed.received_at, "Tue, 08 Sep 2026 09:30:00 +0200")

    def test_ignores_non_stekkies_sender(self) -> None:
        parsed = parse_listing_email(
            message_bytes("news@example.test", "Other", "https://example.test", "<two@example.test>")
        )
        self.assertIsNone(parsed)

    def test_poll_marks_a_message_seen_and_does_not_change_yahoo_read_state(self) -> None:
        raw_messages = [
            message_bytes(
                "Stekkies <alerts@stekkies.com>",
                "New listing",
                "https://stekkies.com/listing/42",
                "<listing@example.test>",
            ),
            message_bytes("news@example.test", "Other", "ignore", "<other@example.test>"),
        ]
        fake_imap = FakeImap(raw_messages)
        mailbox = YahooMailbox("person@yahoo.com", "app-password", client=fake_imap)
        with tempfile.TemporaryDirectory() as directory:
            store = SeenEmailStore(Path(directory) / "seen.sqlite3")
            service = StekkiesQueryService(mailbox, store)
            first_poll = service.poll_once()
            second_poll = service.poll_once()
            queued = store.unread_emails()
            store.close()

        self.assertEqual(len(first_poll), 1)
        self.assertEqual(second_poll, [])
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0].raw_message, raw_messages[0])
        self.assertEqual(fake_imap.selected, ("INBOX", True))
        self.assertTrue(all(command[1] == "(BODY.PEEK[])" for command in fake_imap.fetch_commands))

    def test_new_stekkies_email_without_links_is_queued_for_the_processor(self) -> None:
        fake_imap = FakeImap(
            [
                message_bytes(
                    "Stekkies <alerts@stekkies.com>",
                    "Newsletter",
                    "No listing links today",
                    "<newsletter@example.test>",
                ),
                message_bytes("news@example.test", "Other", "ignore", "<other@example.test>"),
            ]
        )
        mailbox = YahooMailbox("person@yahoo.com", "app-password", client=fake_imap)
        with tempfile.TemporaryDirectory() as directory:
            store = SeenEmailStore(Path(directory) / "seen.sqlite3")
            service = StekkiesQueryService(mailbox, store)
            self.assertEqual(len(service.poll_once()), 1)
            self.assertEqual(len(store.unread_emails()), 1)
            store.close()
