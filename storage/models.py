"""
SQLAlchemy 2.0 ORM models for the mailing_summary storage layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


class ProcessedEmail(Base):
    """Represents a newsletter email that has been processed by the pipeline.

    Columns
    -------
    message_id : str
        Unique Gmail message ID (primary key).
    subject : str
        Email subject line.
    sender : str
        Sender address (``Name <email@domain>`` or plain address).
    received_at : datetime
        UTC timestamp when the email arrived in the inbox.
    processed_at : datetime
        UTC timestamp when the pipeline finished processing this email.
    importance_score : int
        Classifier score in the range 1–5 (higher = more important).
    summary : str | None
        LLM-generated summary text (NULL until summarisation completes).
    category : str | None
        Category label assigned by the classifier (e.g. "tech", "finance").
    digest_date : str | None
        ISO-8601 date string (YYYY-MM-DD) of the digest this email belongs to.
    labels_applied : str | None
        Comma-separated list of Gmail labels applied to this email.
    """

    __tablename__ = "processed_emails"

    message_id: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
        doc="Unique Gmail message ID",
    )
    subject: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
        doc="Email subject line",
    )
    sender: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        doc="Sender address",
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        doc="UTC timestamp when the email arrived",
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        default=datetime.utcnow,
        doc="UTC timestamp when processing completed",
    )
    importance_score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Classifier importance score (1–5)",
    )
    summary: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc="LLM-generated summary",
    )
    category: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        doc="Category label from the classifier",
    )
    digest_date: Mapped[Optional[str]] = mapped_column(
        String(10),
        nullable=True,
        doc="ISO-8601 date of the digest (YYYY-MM-DD)",
    )
    labels_applied: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        doc="Comma-separated Gmail labels applied to this email",
    )

    def __repr__(self) -> str:
        return (
            f"ProcessedEmail("
            f"message_id={self.message_id!r}, "
            f"subject={self.subject!r}, "
            f"sender={self.sender!r}, "
            f"importance_score={self.importance_score})"
        )
