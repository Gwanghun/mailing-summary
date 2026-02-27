"""Gmail API wrapper providing high-level operations for the mailing_summary pipeline.

This module wraps the low-level ``googleapiclient`` calls behind a clean
interface that handles:

- Service initialisation and credential management
- Paginated email retrieval with time-window filtering
- Batch label mutations (≤50 messages per request, per API limits)
- Exponential-backoff retry for transient HTTP errors
- Email sending via the Gmail API (no separate SMTP dependency)
"""

from __future__ import annotations

import base64
import logging
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from gmail.auth import authenticate

logger = logging.getLogger(__name__)

# Maximum number of message IDs accepted by the Gmail batchModify endpoint.
_BATCH_SIZE = 50

# HTTP status codes that are considered transient and worth retrying.
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Retry configuration for the exponential-backoff helper.
_MAX_RETRIES = 5
_BASE_BACKOFF_SECONDS = 1.0


class GmailClient:
    """High-level Gmail API client.

    Args:
        credentials_path: Path to the ``credentials.json`` OAuth2
            client-secrets file.
        token_path: Path where the ``token.json`` is stored / restored.
        environment: Runtime environment string forwarded to
            :func:`gmail.auth.authenticate`.  Use ``"github_actions"`` when
            running in CI.

    Example::

        client = GmailClient(
            credentials_path="data/credentials.json",
            token_path="data/token.json",
        )
        client.build_service()
        emails = client.fetch_emails(lookback_hours=24)
    """

    def __init__(
        self,
        credentials_path: str,
        token_path: str,
        environment: str = "local",
    ) -> None:
        self._credentials_path = credentials_path
        self._token_path = token_path
        self._environment = environment
        self._service: Any = None  # set by build_service()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def build_service(self) -> None:
        """Authenticate and initialise the Gmail API service object.

        Must be called before any other method.  Subsequent calls are
        idempotent – the service is rebuilt only if it has not been
        initialised yet.
        """
        if self._service is not None:
            return

        creds = authenticate(
            credentials_path=self._credentials_path,
            token_path=self._token_path,
            environment=self._environment,
        )
        self._service = build("gmail", "v1", credentials=creds)
        logger.info("Gmail API service initialised.")

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def fetch_emails(
        self,
        lookback_hours: int = 24,
        max_results: int = 50,
    ) -> list[dict]:
        """Return a list of raw message dicts received within *lookback_hours*.

        Each element is the full message resource returned by
        ``users.messages.get`` (``format=full``).

        Args:
            lookback_hours: How many hours back from *now* to scan.
            max_results: Upper bound on the number of messages to return.
                The Gmail API is queried with this limit; actual results may
                be fewer if the mailbox has less matching mail.

        Returns:
            A list of message dicts, newest first.
        """
        self._ensure_service()

        after_epoch = int(time.time()) - lookback_hours * 3600
        query = f"after:{after_epoch}"
        logger.debug(
            "Fetching emails with query=%r, max_results=%d", query, max_results
        )

        message_ids = self._list_message_ids(query=query, max_results=max_results)
        if not message_ids:
            logger.info("No emails found for the given time window.")
            return []

        messages: list[dict] = []
        for msg_id in message_ids:
            detail = self.get_message_detail(msg_id)
            messages.append(detail)

        logger.info("Fetched %d email(s).", len(messages))
        return messages

    def get_message_detail(self, message_id: str) -> dict:
        """Retrieve the full resource for a single message.

        Args:
            message_id: Gmail message ID string.

        Returns:
            The full message resource dict from the Gmail API.
        """
        self._ensure_service()

        return self._with_retry(
            lambda: (
                self._service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        )

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    def create_label_if_not_exists(self, label_name: str) -> str:
        """Return the label ID for *label_name*, creating it if necessary.

        Args:
            label_name: Display name of the label (e.g. ``"Newsletter"``).

        Returns:
            The Gmail label ID string.
        """
        self._ensure_service()

        existing = self._with_retry(
            lambda: self._service.users().labels().list(userId="me").execute()
        )
        for label in existing.get("labels", []):
            if label["name"].lower() == label_name.lower():
                logger.debug("Label %r already exists (id=%s).", label_name, label["id"])
                return label["id"]

        created = self._with_retry(
            lambda: (
                self._service.users()
                .labels()
                .create(userId="me", body={"name": label_name})
                .execute()
            )
        )
        logger.info("Created label %r (id=%s).", label_name, created["id"])
        return created["id"]

    def add_labels(self, message_ids: list[str], label_ids: list[str]) -> None:
        """Add *label_ids* to each message in *message_ids*.

        Uses ``users.messages.batchModify`` in chunks of
        :data:`_BATCH_SIZE`.

        Args:
            message_ids: Gmail message IDs to modify.
            label_ids: Label IDs to add to each message.
        """
        self._ensure_service()
        if not message_ids or not label_ids:
            return

        for chunk in _chunked(message_ids, _BATCH_SIZE):
            self._with_retry(
                lambda ids=chunk: (
                    self._service.users()
                    .messages()
                    .batchModify(
                        userId="me",
                        body={"ids": ids, "addLabelIds": label_ids},
                    )
                    .execute()
                )
            )
        logger.debug(
            "Added labels %s to %d message(s).", label_ids, len(message_ids)
        )

    def remove_labels(self, message_ids: list[str], label_ids: list[str]) -> None:
        """Remove *label_ids* from each message in *message_ids*.

        Uses ``users.messages.batchModify`` in chunks of
        :data:`_BATCH_SIZE`.

        Args:
            message_ids: Gmail message IDs to modify.
            label_ids: Label IDs to remove from each message.
        """
        self._ensure_service()
        if not message_ids or not label_ids:
            return

        for chunk in _chunked(message_ids, _BATCH_SIZE):
            self._with_retry(
                lambda ids=chunk: (
                    self._service.users()
                    .messages()
                    .batchModify(
                        userId="me",
                        body={"ids": ids, "removeLabelIds": label_ids},
                    )
                    .execute()
                )
            )
        logger.debug(
            "Removed labels %s from %d message(s).", label_ids, len(message_ids)
        )

    def mark_as_read(self, message_ids: list[str]) -> None:
        """Remove the ``UNREAD`` system label from *message_ids*.

        Args:
            message_ids: Gmail message IDs to mark as read.
        """
        self.remove_labels(message_ids, ["UNREAD"])
        logger.debug("Marked %d message(s) as read.", len(message_ids))

    def archive(self, message_ids: list[str]) -> None:
        """Remove the ``INBOX`` system label from *message_ids* (archive them).

        Args:
            message_ids: Gmail message IDs to archive.
        """
        self.remove_labels(message_ids, ["INBOX"])
        logger.debug("Archived %d message(s).", len(message_ids))

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send_email(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> dict:
        """Send an email from the authenticated account.

        The message is sent as a ``multipart/alternative`` MIME envelope
        containing both the plain-text and HTML versions.

        Args:
            to: Recipient email address.
            subject: Email subject line.
            html_body: HTML version of the message body.
            text_body: Plain-text version of the message body.

        Returns:
            The ``Message`` resource dict returned by the Gmail API, which
            includes the ``id`` and ``threadId`` of the sent message.
        """
        self._ensure_service()

        mime_msg = MIMEMultipart("alternative")
        mime_msg["To"] = to
        mime_msg["Subject"] = subject
        mime_msg.attach(MIMEText(text_body, "plain", "utf-8"))
        mime_msg.attach(MIMEText(html_body, "html", "utf-8"))

        raw_bytes = mime_msg.as_bytes()
        encoded = base64.urlsafe_b64encode(raw_bytes).decode("ascii")

        result = self._with_retry(
            lambda: (
                self._service.users()
                .messages()
                .send(userId="me", body={"raw": encoded})
                .execute()
            )
        )
        logger.info(
            "Email sent to %r (message_id=%s).", to, result.get("id")
        )
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_service(self) -> None:
        """Raise :exc:`RuntimeError` if :meth:`build_service` has not been called."""
        if self._service is None:
            raise RuntimeError(
                "GmailClient.build_service() must be called before using the client."
            )

    def _list_message_ids(self, query: str, max_results: int) -> list[str]:
        """Return a flat list of message IDs matching *query*.

        Handles pagination transparently.

        Args:
            query: Gmail search query string (``q`` parameter).
            max_results: Maximum total IDs to return.

        Returns:
            List of Gmail message ID strings.
        """
        ids: list[str] = []
        page_token: str | None = None

        while len(ids) < max_results:
            request_limit = min(_BATCH_SIZE, max_results - len(ids))
            response = self._with_retry(
                lambda pt=page_token, lim=request_limit: (
                    self._service.users()
                    .messages()
                    .list(
                        userId="me",
                        q=query,
                        maxResults=lim,
                        pageToken=pt,
                    )
                    .execute()
                )
            )

            for msg in response.get("messages", []):
                ids.append(msg["id"])

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return ids[:max_results]

    def _with_retry(self, fn: Any, max_retries: int = _MAX_RETRIES) -> Any:
        """Execute *fn* with exponential-backoff retry on transient errors.

        Args:
            fn: A zero-argument callable that performs the API call.
            max_retries: Maximum number of retry attempts.

        Returns:
            The return value of *fn* on success.

        Raises:
            :class:`googleapiclient.errors.HttpError`: When all retries are
                exhausted or the error is not transient.
        """
        delay = _BASE_BACKOFF_SECONDS
        for attempt in range(max_retries + 1):
            try:
                return fn()
            except HttpError as exc:
                status = exc.resp.status if exc.resp else 0
                if status not in _RETRYABLE_STATUS_CODES or attempt == max_retries:
                    raise
                logger.warning(
                    "Gmail API error %d on attempt %d/%d – retrying in %.1fs.",
                    status,
                    attempt + 1,
                    max_retries,
                    delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, 60.0)  # cap at 60 s

        # Unreachable, but satisfies type checkers.
        raise RuntimeError("Exceeded maximum retries.")  # pragma: no cover


# ---------------------------------------------------------------------------
# Module-level utility
# ---------------------------------------------------------------------------


def _chunked(items: list[Any], size: int) -> list[list[Any]]:
    """Split *items* into sub-lists of at most *size* elements.

    Args:
        items: The flat list to partition.
        size: Maximum length of each partition.

    Returns:
        A list of sub-lists.
    """
    return [items[i : i + size] for i in range(0, len(items), size)]
