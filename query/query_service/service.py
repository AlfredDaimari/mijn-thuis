from __future__ import annotations

from .store import SeenEmailStore
from .yahoo import ListingEmail, YahooMailbox


class StekkiesQueryService:
    def __init__(self, mailbox: YahooMailbox, store: SeenEmailStore) -> None:
        self.mailbox = mailbox
        self.store = store

    def poll_once(self) -> list[ListingEmail]:
        """Fetch matching messages and return only ones not previously read."""
        new_messages: list[ListingEmail] = []
        for message in self.mailbox.fetch_stekkies_messages():
            if self.store.remember_if_new(
                message.signature,
                message.message_id,
                message.sender,
                message.subject,
                message.raw_message,
            ):
                new_messages.append(message)
        return new_messages
