from __future__ import annotations

from .database import PipelineDatabase
from .yahoo import ListingEmail, YahooMailbox

INITIAL_LOOKBACK_DAYS = 10
DAILY_LOOKBACK_DAYS = 1


class StekkiesQueryService:
    def __init__(self, mailbox: YahooMailbox, database: PipelineDatabase) -> None:
        self.mailbox = mailbox
        self.database = database
        self._initial_sync_complete = False

    def poll_once(self) -> list[ListingEmail]:
        """Backfill ten days once, then poll only the latest day.

        The database signature makes the first wide search safe even when the
        process is restarted; the in-memory flag keeps normal five-minute
        polls cheap after that initial backfill.
        """
        lookback_days = (
            DAILY_LOOKBACK_DAYS
            if self._initial_sync_complete
            else INITIAL_LOOKBACK_DAYS
        )
        new_messages: list[ListingEmail] = []
        for message in self.mailbox.fetch_stekkies_messages(days_back=lookback_days):
            if self.database.remember_if_new(
                message.signature,
                message.message_id,
                message.sender,
                message.subject,
                message.raw_message,
            ):
                new_messages.append(message)
        self._initial_sync_complete = True
        return new_messages
