from __future__ import annotations

from .database import PipelineDatabase
from .yahoo import ListingEmail, YahooMailbox


class StekkiesQueryService:
    def __init__(self, mailbox: YahooMailbox, database: PipelineDatabase) -> None:
        self.mailbox = mailbox
        self.database = database

    def poll_once(self) -> list[ListingEmail]:
        """Fetch matching messages and return only ones not previously read."""
        new_messages: list[ListingEmail] = []
        for message in self.mailbox.fetch_stekkies_messages():
            if self.database.remember_if_new(
                message.signature,
                message.message_id,
                message.sender,
                message.subject,
                message.raw_message,
            ):
                new_messages.append(message)
        return new_messages
