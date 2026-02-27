"""
Daily Digest builder.

Assembles a DigestEmail from a list of SummaryResult objects using a
Jinja2 HTML template and generates a plain-text fallback.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SummaryResult:
    """Represents a single processed newsletter with summary data.

    This mirrors the fields stored in ProcessedEmail but is used as the
    in-memory DTO that flows through the pipeline into the digest builder.
    """

    message_id: str
    subject: str
    sender: str
    category: str
    importance_score: int
    summary: str
    key_points: list[str] = field(default_factory=list)
    received_at: str = ""


@dataclass
class DigestEmail:
    """Fully rendered email ready to be handed to DigestSender."""

    subject: str               # "[뉴스레터 다이제스트] 2026-02-26 - 12건"
    html_body: str
    plain_text: str
    total_count: int
    high_importance_count: int  # importance_score >= 4


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class DigestBuilder:
    """Assemble a daily digest email from SummaryResult objects.

    Parameters
    ----------
    template_path:
        Path to the Jinja2 HTML template file.  The directory portion is
        used as the Jinja2 ``FileSystemLoader`` search path so that the
        template can extend or include other templates in the same folder.
    """

    def __init__(
        self,
        template_path: str = "digest/templates/daily_digest.html",
    ) -> None:
        self._template_path = template_path

        template_dir = os.path.dirname(os.path.abspath(template_path))
        template_file = os.path.basename(template_path)

        self._env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self._template_file = template_file

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, results: list[SummaryResult], digest_date: date) -> DigestEmail:
        """Build a DigestEmail from *results* for the given *digest_date*.

        Steps
        -----
        1. Sort by importance descending (5 → 1).
        2. Group by category.
        3. Render the Jinja2 HTML template.
        4. Generate a plain-text fallback.
        5. Return a populated DigestEmail dataclass.
        """
        if not results:
            logger.warning("build() called with an empty results list – digest will be empty.")

        sorted_results = sorted(results, key=lambda r: r.importance_score, reverse=True)
        grouped = self._group_by_category(sorted_results)
        high_importance = [r for r in sorted_results if r.importance_score >= 4]

        stats: dict[str, Any] = {
            "total": len(sorted_results),
            "high_importance": len(high_importance),
            "categories": len(grouped),
        }

        date_str = digest_date.strftime("%Y-%m-%d")
        subject = f"[뉴스레터 다이제스트] {date_str} - {len(sorted_results)}건"

        html_body = self._render_html(sorted_results, grouped, digest_date, stats)
        plain_text = self._build_plain_text(sorted_results, digest_date)

        return DigestEmail(
            subject=subject,
            html_body=html_body,
            plain_text=plain_text,
            total_count=len(sorted_results),
            high_importance_count=len(high_importance),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _group_by_category(
        self, results: list[SummaryResult]
    ) -> dict[str, list[SummaryResult]]:
        """Return an ordered dict mapping category → sorted results."""
        grouped: dict[str, list[SummaryResult]] = {}
        for result in results:
            cat = result.category or "기타"
            grouped.setdefault(cat, []).append(result)
        return grouped

    def _render_html(
        self,
        results: list[SummaryResult],
        grouped: dict[str, list[SummaryResult]],
        digest_date: date,
        stats: dict[str, Any],
    ) -> str:
        """Render the Jinja2 HTML template and return the HTML string."""
        try:
            template = self._env.get_template(self._template_file)
            return template.render(
                results=results,
                grouped=grouped,
                date=digest_date,
                date_str=digest_date.strftime("%Y년 %m월 %d일"),
                stats=stats,
            )
        except Exception:
            logger.exception("Failed to render digest HTML template")
            # Fallback: minimal HTML so the sender always has something to send
            return (
                f"<html><body><h1>뉴스레터 다이제스트 {digest_date}</h1>"
                f"<p>템플릿 렌더링 오류가 발생했습니다. plain text 버전을 확인하세요.</p></body></html>"
            )

    def _build_plain_text(
        self, results: list[SummaryResult], digest_date: date
    ) -> str:
        """Generate a plain-text representation of the digest."""
        lines: list[str] = []
        date_str = digest_date.strftime("%Y년 %m월 %d일")

        lines.append("=" * 60)
        lines.append(f"뉴스레터 다이제스트 – {date_str}")
        lines.append(f"총 {len(results)}건")
        lines.append("=" * 60)
        lines.append("")

        grouped = self._group_by_category(results)

        for category, items in grouped.items():
            lines.append(f"[{category}]")
            lines.append("-" * 40)
            for item in items:
                importance_label = "★" * item.importance_score
                lines.append(f"중요도: {importance_label} ({item.importance_score}/5)")
                lines.append(f"제목: {item.subject}")
                lines.append(f"발신: {item.sender}")
                lines.append(f"요약: {item.summary}")
                if item.key_points:
                    lines.append("핵심 포인트:")
                    for point in item.key_points:
                        lines.append(f"  • {point}")
                lines.append(f"원문: https://mail.google.com/mail/u/0/#all/{item.message_id}")
                lines.append("")

        lines.append("=" * 60)
        lines.append("이 다이제스트는 Claude AI가 자동으로 생성했습니다.")
        lines.append("=" * 60)

        return "\n".join(lines)
