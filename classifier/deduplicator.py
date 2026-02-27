"""
Deduplicator - filters out emails that have already been processed.
Uses the SQLite database to check processed message IDs.
"""
import logging

from storage.database import is_already_processed

logger = logging.getLogger(__name__)


class Deduplicator:
    """Filters ParsedEmail objects that were already processed in a previous run."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def is_processed(self, message_id: str) -> bool:
        """Return True if this message was already processed."""
        return is_already_processed(message_id, self.db_path)

    def filter_new(self, emails: list) -> list:
        """Return only emails that have not been processed before."""
        new_emails = []
        skipped = 0

        for email in emails:
            if self.is_processed(email.message_id):
                skipped += 1
                logger.debug("Skipping already-processed: %s", email.message_id)
            else:
                new_emails.append(email)

        if skipped:
            logger.info(
                "Deduplicator: %d new, %d already processed (skipped)",
                len(new_emails), skipped,
            )
        return new_emails
