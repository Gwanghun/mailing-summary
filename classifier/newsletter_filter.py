"""Newsletter classification and category assignment.

This module provides :class:`NewsletterFilter`, which decides whether an
email is a newsletter and assigns it one of four broad categories:

- ``"tech"``     – technology, engineering, software
- ``"finance"``  – markets, investing, economics
- ``"startup"``  – entrepreneurship, venture capital, product
- ``"general"``  – anything else that looks like a newsletter

Classification is rule-based and deliberately simple:

1. ``List-Unsubscribe`` header present   → strong positive signal
2. Sender domain in the known-sources list → positive signal
3. Subject matches a configured regex pattern → positive signal

At least one signal must be present for an email to be classified as a
newsletter.  Category is derived from keyword matching on the sender domain
and subject line.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from gmail.message_parser import ParsedEmail

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category keyword maps
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "tech": [
        "tech", "engineer", "software", "developer", "coding", "programming",
        "python", "javascript", "typescript", "react", "kubernetes", "devops",
        "ai", "ml", "machine learning", "deep learning", "llm", "openai",
        "anthropic", "github", "hacker", "open source", "cloud", "aws",
        "google cloud", "azure", "databricks", "data science",
    ],
    "finance": [
        "finance", "market", "invest", "stock", "crypto", "bitcoin",
        "ethereum", "defi", "trading", "portfolio", "economy", "economics",
        "fund", "venture", "vc", "equity", "hedge", "forex", "commodity",
        "earnings", "revenue", "fintech", "bank",
    ],
    "startup": [
        "startup", "founder", "entrepreneurship", "product", "launch",
        "saas", "b2b", "b2c", "growth", "traction", "pivot", "bootstrapped",
        "yc", "y combinator", "accelerator", "incubator", "pitch",
        "scale", "runway", "mrr", "arr",
    ],
}


class NewsletterFilter:
    """Determines whether an email is a newsletter and assigns a category.

    Configuration is loaded from a YAML file that lists known sender domains
    and subject-line regex patterns.

    Args:
        sources_config_path: Path to ``newsletter_sources.yaml``.

    Example::

        filt = NewsletterFilter("config/newsletter_sources.yaml")
        if filt.is_newsletter(parsed_email):
            category = filt.get_category(parsed_email)
    """

    def __init__(
        self,
        sources_config_path: str = "config/newsletter_sources.yaml",
        allow_senders_path: str = "config/allow_senders.yaml",
    ) -> None:
        self._config = self._load_config(sources_config_path)
        self._allow = self._load_config(allow_senders_path)

        self._known_domains: set[str] = {
            d.lower() for d in self._config.get("domains", [])
        } | {d.lower() for d in self._allow.get("domains", [])}

        self._allow_emails: set[str] = {
            e.lower() for e in self._allow.get("emails", [])
        }

        self._subject_patterns: list[re.Pattern[str]] = [
            re.compile(p, re.IGNORECASE)
            for p in self._config.get("subject_patterns", [])
        ]
        logger.debug(
            "NewsletterFilter loaded %d domains, %d allow-emails, %d subject patterns.",
            len(self._known_domains),
            len(self._allow_emails),
            len(self._subject_patterns),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_newsletter(self, email: ParsedEmail) -> bool:
        """Return ``True`` if *email* is identified as a newsletter.

        An email is classified as a newsletter when *at least one* of the
        following conditions is met:

        - A ``List-Unsubscribe`` header is present.
        - The sender domain matches a domain in the sources config.
        - The subject line matches one of the configured regex patterns.

        Args:
            email: A :class:`~gmail.message_parser.ParsedEmail` instance.

        Returns:
            ``True`` if the email is a newsletter, ``False`` otherwise.
        """
        if self._check_allow_email(email):
            logger.debug(
                "message_id=%s classified as newsletter via allow-list (email=%s).",
                email.message_id,
                email.sender,
            )
            return True

        if self._check_list_unsubscribe(email):
            logger.debug(
                "message_id=%s classified as newsletter via List-Unsubscribe.",
                email.message_id,
            )
            return True

        if self._check_domain(email):
            logger.debug(
                "message_id=%s classified as newsletter via domain match (%s).",
                email.message_id,
                email.sender_domain,
            )
            return True

        if self._check_subject_pattern(email):
            logger.debug(
                "message_id=%s classified as newsletter via subject pattern.",
                email.message_id,
            )
            return True

        return False

    def get_category(self, email: ParsedEmail) -> str:
        """Return the content category for *email*.

        The category is determined by keyword matching against the sender
        domain and subject line combined.  If no category-specific keyword
        is matched, ``"general"`` is returned.

        Args:
            email: A :class:`~gmail.message_parser.ParsedEmail` instance.

        Returns:
            One of ``"tech"``, ``"finance"``, ``"startup"``, or ``"general"``.
        """
        haystack = (
            f"{email.sender_domain} {email.subject} {email.plain_text[:500]}"
        ).lower()

        for category, keywords in _CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw in haystack:
                    logger.debug(
                        "message_id=%s assigned category=%r (keyword=%r).",
                        email.message_id,
                        category,
                        kw,
                    )
                    return category

        return "general"

    # ------------------------------------------------------------------
    # Signal checkers
    # ------------------------------------------------------------------

    def _check_allow_email(self, email: ParsedEmail) -> bool:
        """Return ``True`` when the sender address is in the allow-list."""
        if not self._allow_emails:
            return False
        sender_lower = email.sender.lower()
        return any(allowed in sender_lower for allowed in self._allow_emails)

    def _check_list_unsubscribe(self, email: ParsedEmail) -> bool:
        """Return ``True`` when the ``List-Unsubscribe`` header is present.

        Args:
            email: Parsed email instance.

        Returns:
            Boolean newsletter signal.
        """
        return email.has_list_unsubscribe

    def _check_domain(self, email: ParsedEmail) -> bool:
        """Return ``True`` when the sender domain is in the known-sources list.

        The check is performed on the full domain AND every suffix.  For
        example, ``"mail.substack.com"`` matches because ``"substack.com"``
        is in the known domains list.

        Args:
            email: Parsed email instance.

        Returns:
            Boolean newsletter signal.
        """
        domain = email.sender_domain.lower()
        if not domain:
            return False

        if domain in self._known_domains:
            return True

        # Check suffix: e.g. "mail.substack.com" → "substack.com"
        parts = domain.split(".")
        for i in range(1, len(parts)):
            suffix = ".".join(parts[i:])
            if suffix in self._known_domains:
                return True

        return False

    def _check_subject_pattern(self, email: ParsedEmail) -> bool:
        """Return ``True`` when the subject matches a configured regex pattern.

        Args:
            email: Parsed email instance.

        Returns:
            Boolean newsletter signal.
        """
        for pattern in self._subject_patterns:
            if pattern.search(email.subject):
                return True
        return False

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    @staticmethod
    def _load_config(path: str) -> dict[str, Any]:
        """Load and return the YAML configuration at *path*.

        Args:
            path: Path to the ``newsletter_sources.yaml`` config file.

        Returns:
            Parsed YAML as a Python dict.  Returns an empty dict when the
            file is missing so the filter can still operate with no rules.
        """
        config_path = Path(path)
        if not config_path.exists():
            logger.warning(
                "Newsletter sources config not found at %r – using empty config.",
                path,
            )
            return {}

        with config_path.open("r", encoding="utf-8") as fh:
            data: dict[str, Any] = yaml.safe_load(fh) or {}
        logger.debug("Loaded newsletter sources config from %r.", path)
        return data
