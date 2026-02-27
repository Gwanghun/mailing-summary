"""Email message parsing utilities.

Converts raw Gmail API message dicts into structured :class:`ParsedEmail`
dataclass instances that are ready for downstream processing (classification,
summarisation, etc.).

Handles:
- ``multipart/alternative`` and ``multipart/mixed`` MIME structures
- URL-safe Base64 encoded body parts
- HTML-to-plain-text conversion via ``html2text`` + ``BeautifulSoup``
- Header decoding (RFC 2047 encoded-words)
- Sender domain extraction
- ``List-Unsubscribe`` header detection
"""

from __future__ import annotations

import base64
import binascii
import logging
import quopri
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

import html2text
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_FALLBACK_DATETIME = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass
class ParsedEmail:
    """Structured representation of a single Gmail message.

    Attributes:
        message_id: The Gmail-internal message identifier (not the
            ``Message-ID`` SMTP header).
        subject: Decoded subject line, or an empty string when absent.
        sender: Full ``From`` value, e.g. ``"Alice <alice@example.com>"``.
        sender_domain: Domain part of the sender's email address, lower-cased.
        received_at: Date/time the message was received, UTC-aware.
        plain_text: Best-effort plain-text body, truncated to *max_chars*
            (default 3 000).
        raw_headers: Mapping of header name → header value (last occurrence
            wins for duplicate headers).
        has_list_unsubscribe: ``True`` when a ``List-Unsubscribe`` header is
            present (strong newsletter signal).
    """

    message_id: str
    subject: str
    sender: str
    sender_domain: str
    received_at: datetime
    plain_text: str
    raw_headers: dict[str, str] = field(default_factory=dict)
    has_list_unsubscribe: bool = False


class MessageParser:
    """Converts raw Gmail API message dicts to :class:`ParsedEmail` objects.

    Example::

        parser = MessageParser()
        parsed = parser.parse(raw_message_dict)
        print(parsed.subject, parsed.sender_domain)
    """

    def parse(self, raw_message: dict) -> ParsedEmail:
        """Parse a single raw Gmail API message resource.

        Args:
            raw_message: The ``Message`` resource dict as returned by
                ``users.messages.get(format='full')``.

        Returns:
            A populated :class:`ParsedEmail` instance.
        """
        message_id: str = raw_message.get("id", "")
        payload: dict = raw_message.get("payload", {})
        headers_list: list[dict] = payload.get("headers", [])

        raw_headers = self._collect_headers(headers_list)

        subject = self._decode_header_value(raw_headers.get("subject", ""))
        from_raw = raw_headers.get("from", "")
        sender = self._decode_header_value(from_raw)
        sender_domain = self._extract_domain(sender)
        received_at = self._parse_date(raw_headers.get("date", ""))
        has_list_unsubscribe = "list-unsubscribe" in raw_headers

        body_text = self._extract_body(payload)
        plain_text = self._truncate_text(body_text)

        return ParsedEmail(
            message_id=message_id,
            subject=subject,
            sender=sender,
            sender_domain=sender_domain,
            received_at=received_at,
            plain_text=plain_text,
            raw_headers=raw_headers,
            has_list_unsubscribe=has_list_unsubscribe,
        )

    # ------------------------------------------------------------------
    # Body extraction
    # ------------------------------------------------------------------

    def _extract_body(self, payload: dict) -> str:
        """Recursively extract the best plain-text representation of *payload*.

        Priority:
        1. ``text/plain`` parts
        2. ``text/html`` parts (converted to plain text)

        Multipart containers (``multipart/alternative``,
        ``multipart/mixed``, etc.) are traversed recursively.

        Args:
            payload: The ``payload`` sub-dict from a Gmail message resource.

        Returns:
            Plain-text body string, possibly empty.
        """
        mime_type: str = payload.get("mimeType", "")

        # ---- leaf node ---------------------------------------------------
        if mime_type == "text/plain":
            data = payload.get("body", {}).get("data", "")
            return self._decode_base64(data) if data else ""

        if mime_type == "text/html":
            data = payload.get("body", {}).get("data", "")
            html = self._decode_base64(data) if data else ""
            return self._html_to_text(html)

        # ---- multipart container -----------------------------------------
        if mime_type.startswith("multipart/"):
            parts: list[dict] = payload.get("parts", [])
            # Collect text/plain first, then fall back to text/html.
            plain_parts: list[str] = []
            html_parts: list[str] = []

            for part in parts:
                part_mime = part.get("mimeType", "")
                if part_mime == "text/plain":
                    plain_parts.append(self._extract_body(part))
                elif part_mime == "text/html":
                    html_parts.append(self._extract_body(part))
                elif part_mime.startswith("multipart/"):
                    nested = self._extract_body(part)
                    if nested:
                        plain_parts.append(nested)

            if plain_parts:
                return "\n".join(filter(None, plain_parts))
            if html_parts:
                return "\n".join(filter(None, html_parts))

        # Fallback: try top-level body data (rare).
        data = payload.get("body", {}).get("data", "")
        if data:
            raw = self._decode_base64(data)
            if "<html" in raw.lower() or "<body" in raw.lower():
                return self._html_to_text(raw)
            return raw

        return ""

    # ------------------------------------------------------------------
    # HTML → plain text
    # ------------------------------------------------------------------

    def _html_to_text(self, html: str) -> str:
        """Convert *html* to plain text.

        Uses ``BeautifulSoup`` for robust HTML parsing followed by
        ``html2text`` for markdown-style plain-text rendering.

        Args:
            html: Raw HTML string.

        Returns:
            Plain-text string with basic markdown-style formatting.
        """
        if not html:
            return ""

        try:
            # BeautifulSoup normalises malformed HTML before conversion.
            soup = BeautifulSoup(html, "lxml")
            clean_html = str(soup)
        except Exception:  # noqa: BLE001
            clean_html = html

        converter = html2text.HTML2Text()
        converter.ignore_links = True
        converter.ignore_images = True
        converter.ignore_tables = False
        converter.body_width = 0  # disable line wrapping

        try:
            return converter.handle(clean_html).strip()
        except Exception as exc:  # noqa: BLE001
            logger.debug("html2text conversion failed: %s", exc)
            # Last-resort fallback: strip all tags.
            return re.sub(r"<[^>]+>", " ", html).strip()

    # ------------------------------------------------------------------
    # Base64 decoding
    # ------------------------------------------------------------------

    def _decode_base64(self, data: str) -> str:
        """Decode a URL-safe Base64-encoded string to UTF-8 text.

        Gmail uses URL-safe Base64 (``+`` → ``-``, ``/`` → ``_``) with
        padding stripped.

        Args:
            data: URL-safe Base64 string, possibly without padding ``=``.

        Returns:
            Decoded text, or an empty string when decoding fails.
        """
        if not data:
            return ""

        # Re-add padding if necessary.
        padded = data + "=" * (-len(data) % 4)
        try:
            raw_bytes = base64.urlsafe_b64decode(padded)
        except binascii.Error as exc:
            logger.debug("Base64 decode failed: %s", exc)
            return ""

        # Try common encodings in order of likelihood.
        for encoding in ("utf-8", "latin-1", "windows-1252"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue

        return raw_bytes.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # Truncation
    # ------------------------------------------------------------------

    def _truncate_text(self, text: str, max_chars: int = 3000) -> str:
        """Truncate *text* to at most *max_chars* characters.

        Truncation is word-boundary-aware: the last complete word before
        the limit is preserved.

        Args:
            text: Input string.
            max_chars: Maximum character length of the output.

        Returns:
            Original string when ``len(text) <= max_chars``; otherwise a
            truncated string ending with ``" …"``.
        """
        if len(text) <= max_chars:
            return text

        truncated = text[:max_chars].rsplit(None, 1)[0]
        return truncated + " …"

    # ------------------------------------------------------------------
    # Header helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_headers(headers_list: list[dict[str, str]]) -> dict[str, str]:
        """Convert the Gmail headers list to a lower-cased name → value dict.

        When duplicate header names are present the *last* occurrence is
        kept (matches the behaviour of most MUA libraries).

        Args:
            headers_list: List of ``{"name": "...", "value": "..."}`` dicts.

        Returns:
            Dict mapping lower-cased header names to their string values.
        """
        result: dict[str, str] = {}
        for header in headers_list:
            name = header.get("name", "").lower().strip()
            value = header.get("value", "").strip()
            if name:
                result[name] = value
        return result

    @staticmethod
    def _decode_header_value(value: str) -> str:
        """Decode an RFC 2047 encoded-word header value to a plain string.

        Args:
            value: Raw header value, possibly containing ``=?charset?...?=``
                encoded-word sequences.

        Returns:
            Decoded string.
        """
        if not value:
            return ""

        parts: list[str] = []
        for decoded_bytes, charset in decode_header(value):
            if isinstance(decoded_bytes, bytes):
                parts.append(
                    decoded_bytes.decode(charset or "utf-8", errors="replace")
                )
            else:
                parts.append(decoded_bytes)
        return "".join(parts)

    @staticmethod
    def _extract_domain(from_header: str) -> str:
        """Extract the lower-cased domain part from a ``From`` header value.

        Args:
            from_header: Decoded ``From`` header, e.g.
                ``"Newsletter <news@example.com>"``.

        Returns:
            Lower-cased domain string, e.g. ``"example.com"``, or an empty
            string when the address cannot be parsed.
        """
        _, email_addr = parseaddr(from_header)
        if "@" in email_addr:
            return email_addr.split("@", 1)[1].lower().strip(">")
        return ""

    @staticmethod
    def _parse_date(date_str: str) -> datetime:
        """Parse an RFC 2822 date string to a UTC-aware :class:`~datetime.datetime`.

        Args:
            date_str: Raw value of the ``Date`` header.

        Returns:
            Parsed datetime (UTC-aware).  Returns a Unix-epoch datetime when
            parsing fails.
        """
        if not date_str:
            return _FALLBACK_DATETIME

        try:
            dt = parsedate_to_datetime(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to parse date %r: %s", date_str, exc)
            return _FALLBACK_DATETIME
