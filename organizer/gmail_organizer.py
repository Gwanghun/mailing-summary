"""
organizer/gmail_organizer.py

Gmail 자동 정리 모듈.
SummaryResult 목록을 기반으로 이메일에 라벨을 적용하고
중요도에 따라 아카이브 또는 받은편지함 유지를 결정합니다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from gmail.client import GmailClient
from summarizer.claude_client import SummaryResult

logger = logging.getLogger(__name__)

# 뉴스레터 라벨의 최상위 네임스페이스
_LABEL_NAMESPACE = "Newsletter"

# 받은편지함 유지 기준 중요도 (이상이면 INBOX 유지)
_INBOX_THRESHOLD = 4


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class OrganizeStats:
    """Gmail 정리 작업 통계.

    Attributes
    ----------
    total:
        처리 대상 이메일 총 수.
    labeled:
        라벨이 성공적으로 추가된 이메일 수.
    archived:
        아카이브 처리된 이메일 수.
    kept_in_inbox:
        받은편지함에 유지된 이메일 수.
    read_marked:
        읽음으로 표시된 이메일 수.
    """

    total: int = 0
    labeled: int = 0
    archived: int = 0
    kept_in_inbox: int = 0
    read_marked: int = 0


# ---------------------------------------------------------------------------
# Gmail Organizer
# ---------------------------------------------------------------------------


class GmailOrganizer:
    """SummaryResult를 기반으로 Gmail 메일함을 자동 정리하는 클래스.

    정리 정책
    ---------
    - 중요도 >= 4 : 라벨만 추가, 받은편지함 유지 (중요한 건 직접 확인)
    - 중요도 1–3 : 라벨 추가 + 읽음 처리 + 아카이브

    Parameters
    ----------
    gmail_client:
        Gmail API 래퍼 클라이언트.

    Examples
    --------
    >>> organizer = GmailOrganizer(gmail_client=client)
    >>> stats = organizer.organize(results)
    >>> print(f"아카이브: {stats.archived}, 받은편지함 유지: {stats.kept_in_inbox}")
    """

    def __init__(self, gmail_client: GmailClient) -> None:
        self._gmail = gmail_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def organize(self, results: list[SummaryResult]) -> OrganizeStats:
        """요약 결과를 기반으로 이메일 일괄 정리.

        각 SummaryResult에 대해 카테고리 기반 라벨을 생성/적용하고
        중요도 점수에 따라 아카이브 또는 받은편지함 유지 여부를 결정합니다.

        Parameters
        ----------
        results:
            처리할 SummaryResult 목록.

        Returns
        -------
        OrganizeStats
            정리 작업 통계 (라벨링, 아카이브, 유지, 읽음 처리 건수).
        """
        stats = OrganizeStats(total=len(results))

        for result in results:
            label_name = self._get_label_name(result)

            # 1) 라벨 적용
            try:
                label_id = self._gmail.create_label_if_not_exists(label_name)
                self._gmail.add_labels(
                    message_ids=[result.message_id],
                    label_ids=[label_id],
                )
                stats.labeled += 1
                logger.debug(
                    "라벨 적용 — message_id=%s | 라벨: %s",
                    result.message_id,
                    label_name,
                )
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning(
                    "라벨 적용 실패 — message_id=%s | 라벨: %s | 오류: %s",
                    result.message_id,
                    label_name,
                    exc,
                )

            # 2) 중요도에 따른 처리 분기
            if result.importance_score >= _INBOX_THRESHOLD:
                # 중요도 4, 5: 받은편지함 유지 (직접 확인 필요)
                stats.kept_in_inbox += 1
                logger.debug(
                    "받은편지함 유지 (중요도=%d) — message_id=%s",
                    result.importance_score,
                    result.message_id,
                )
            else:
                # 중요도 1–3: 읽음 처리 + 아카이브
                try:
                    self._gmail.mark_as_read(message_ids=[result.message_id])
                    stats.read_marked += 1
                    logger.debug(
                        "읽음 처리 — message_id=%s",
                        result.message_id,
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    logger.warning(
                        "읽음 처리 실패 — message_id=%s | 오류: %s",
                        result.message_id,
                        exc,
                    )

                try:
                    self._gmail.archive(message_ids=[result.message_id])
                    stats.archived += 1
                    logger.debug(
                        "아카이브 완료 — message_id=%s",
                        result.message_id,
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    logger.warning(
                        "아카이브 실패 — message_id=%s | 오류: %s",
                        result.message_id,
                        exc,
                    )

        logger.info(
            "정리 완료 — 총: %d | 라벨: %d | 아카이브: %d | "
            "받은편지함 유지: %d | 읽음처리: %d",
            stats.total,
            stats.labeled,
            stats.archived,
            stats.kept_in_inbox,
            stats.read_marked,
        )
        return stats

    # ------------------------------------------------------------------
    # Private Helpers
    # ------------------------------------------------------------------

    def _get_label_name(self, result: SummaryResult) -> str:
        """카테고리를 기반으로 Gmail 라벨 이름 생성.

        Parameters
        ----------
        result:
            라벨을 결정할 요약 결과.

        Returns
        -------
        str
            ``Newsletter/<카테고리>`` 형식의 Gmail 라벨 이름.
            예: ``Newsletter/AI``, ``Newsletter/개발``.
        """
        category = result.category.strip() if result.category else "기타"
        return f"{_LABEL_NAMESPACE}/{category}"
