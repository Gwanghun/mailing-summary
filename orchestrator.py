"""
Main orchestrator for the Gmail Newsletter Summary System.
Coordinates all modules: fetch → filter → summarize → organize → send.
"""
import logging
from datetime import date, datetime
from typing import Optional

from config.settings import Settings
from gmail.auth import authenticate
from gmail.client import GmailClient
from gmail.message_parser import MessageParser
from classifier.newsletter_filter import NewsletterFilter
from summarizer.claude_client import ClaudeClient
from organizer.gmail_organizer import GmailOrganizer
from digest.digest_builder import DigestBuilder
from digest.sender import DigestSender
from storage.database import init_db, save_processed_emails, is_already_processed
from storage.models import ProcessedEmail

logger = logging.getLogger(__name__)


class DigestOrchestrator:
    """Coordinates the full daily digest pipeline."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._gmail_client: Optional[GmailClient] = None
        self._claude_client: Optional[ClaudeClient] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_gmail_client(self) -> GmailClient:
        if self._gmail_client is None:
            self._gmail_client = GmailClient(
                credentials_path=self.settings.google_credentials_path,
                token_path=self.settings.google_token_path,
                environment=self.settings.environment,
            )
            self._gmail_client.build_service()
        return self._gmail_client

    def _get_claude_client(self) -> ClaudeClient:
        if self._claude_client is None:
            self._claude_client = ClaudeClient(
                api_key=self.settings.anthropic_api_key,
                model=self.settings.claude_model,
            )
        return self._claude_client

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------

    def _step_fetch(self) -> list:
        """STEP 1: Fetch recent emails from Gmail."""
        logger.info(
            "[STEP 1] Fetching emails: lookback=%dh, max=%d",
            self.settings.lookback_hours,
            self.settings.max_emails_per_run,
        )
        gmail = self._get_gmail_client()
        raw_emails = gmail.fetch_emails(
            lookback_hours=self.settings.lookback_hours,
            max_results=self.settings.max_emails_per_run,
        )

        parser = MessageParser()
        parsed = [parser.parse(raw) for raw in raw_emails]
        logger.info("[STEP 1] Fetched %d emails", len(parsed))
        return parsed

    def _step_filter(self, parsed_emails: list) -> list:
        """STEP 2: Keep only newsletters; remove already-processed ones."""
        logger.info("[STEP 2] Filtering %d emails", len(parsed_emails))

        newsletter_filter = NewsletterFilter()
        newsletters = [e for e in parsed_emails if newsletter_filter.is_newsletter(e)]
        logger.info("[STEP 2] Newsletter candidates: %d", len(newsletters))

        # Deduplicate against DB
        new_emails = [
            e for e in newsletters
            if not is_already_processed(e.message_id, self.settings.db_path)
        ]
        skipped = len(newsletters) - len(new_emails)
        logger.info(
            "[STEP 2] New: %d, Already processed (skipped): %d",
            len(new_emails), skipped,
        )
        return new_emails

    def _step_summarize(self, emails: list) -> list:
        """STEP 3: Summarize each newsletter with Claude API."""
        if not emails:
            logger.info("[STEP 3] No emails to summarize")
            return []

        logger.info("[STEP 3] Summarizing %d emails with Claude", len(emails))
        claude = self._get_claude_client()
        results = claude.summarize_batch(emails)

        # Apply minimum importance filter
        filtered = [
            r for r in results
            if r.importance_score >= self.settings.min_importance_score
        ]
        logger.info(
            "[STEP 3] Summarized: %d, above threshold (>=%d): %d",
            len(results), self.settings.min_importance_score, len(filtered),
        )
        return filtered

    def _step_organize(self, results: list) -> None:
        """STEP 4: Apply labels, archive, mark as read in Gmail."""
        if not results:
            return
        logger.info("[STEP 4] Organizing %d emails in Gmail", len(results))
        gmail = self._get_gmail_client()
        organizer = GmailOrganizer(gmail_client=gmail)
        stats = organizer.organize(results)
        logger.info(
            "[STEP 4] Organized: labeled=%d, archived=%d, inbox_kept=%d, read=%d",
            stats.labeled, stats.archived, stats.kept_in_inbox, stats.read_marked,
        )

    def _step_save(self, results: list, today: date) -> None:
        """STEP 5: Persist processed email records to SQLite."""
        if not results:
            return
        records = [
            ProcessedEmail(
                message_id=r.message_id,
                subject=r.subject,
                sender=r.sender,
                received_at=r.received_at,
                processed_at=datetime.utcnow(),
                importance_score=r.importance_score,
                summary=r.summary,
                category=r.category,
                digest_date=today.isoformat(),
            )
            for r in results
        ]
        saved = save_processed_emails(records, self.settings.db_path)
        logger.info("[STEP 5] Saved %d records to DB", saved)

    def _step_send(self, results: list, today: date) -> None:
        """STEP 6: Build and send the Daily Digest email."""
        if not results:
            logger.info("[STEP 6] No summaries to send today")
            return

        builder = DigestBuilder()
        digest = builder.build(results, today)

        if not self.settings.digest_recipient:
            logger.warning("[STEP 6] DIGEST_RECIPIENT not set — skipping send")
            return

        sender = DigestSender(
            gmail_user=self.settings.gmail_user,
            app_password=self.settings.gmail_app_password,
            smtp_host=self.settings.smtp_host,
            smtp_port=self.settings.smtp_port,
        )
        success = sender.send(digest, self.settings.digest_recipient)
        if success:
            logger.info("[STEP 6] Digest sent: '%s'", digest.subject)
        else:
            logger.error("[STEP 6] Failed to send digest email")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, dry_run: bool = False, lookback_hours: Optional[int] = None) -> None:
        """Execute the full pipeline."""
        if lookback_hours is not None:
            self.settings.lookback_hours = lookback_hours

        today = date.today()
        start = datetime.now()
        logger.info(
            "[RUN START] date=%s, dry_run=%s, lookback=%dh",
            today, dry_run, self.settings.lookback_hours,
        )

        try:
            init_db(self.settings.db_path)

            parsed_emails = self._step_fetch()
            new_emails = self._step_filter(parsed_emails)

            if not new_emails:
                logger.info("[RUN] No new newsletters found — done")
                return

            results = self._step_summarize(new_emails)

            if not dry_run:
                self._step_organize(results)
                self._step_save(results, today)
                self._step_send(results, today)
            else:
                logger.info("[DRY RUN] Skipped organize / save / send")
                for r in results:
                    logger.info(
                        "  [%d/5] %s | %s",
                        r.importance_score, r.category, r.subject,
                    )

            elapsed = (datetime.now() - start).total_seconds()
            logger.info(
                "[RUN END] success=True, summaries=%d, elapsed=%.1fs",
                len(results), elapsed,
            )

        except Exception:
            elapsed = (datetime.now() - start).total_seconds()
            logger.exception(
                "[RUN END] success=False, elapsed=%.1fs", elapsed
            )
            raise
