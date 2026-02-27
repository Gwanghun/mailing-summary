"""
summarizer/claude_client.py

Claude API 클라이언트 모듈.
개별 이메일 요약 및 배치 처리 기능을 제공합니다.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

import anthropic

from gmail.message_parser import ParsedEmail
from summarizer.prompt_builder import (
    SYSTEM_PROMPT,
    build_analysis_prompt,
    parse_claude_response,
)

logger = logging.getLogger(__name__)

# Claude API 단일 요청 최대 재시도 횟수
_MAX_RETRIES = 2

# 배치 처리 시 요청 간 최소 대기 시간(초) - rate limit 방어
_BATCH_INTERVAL_SECONDS = 0.5


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class SummaryResult:
    """단일 이메일 요약 결과.

    Attributes
    ----------
    message_id:
        Gmail 메시지 고유 ID.
    subject:
        이메일 제목.
    sender:
        발신자 주소 (``Name <email@domain>`` 또는 순수 주소).
    received_at:
        이메일 수신 UTC 타임스탬프.
    importance_score:
        중요도 점수 (1–5). 5가 가장 중요.
    summary:
        한국어 핵심 요약 (3–5줄).
    key_points:
        핵심 포인트 목록 (최대 3개).
    category:
        이메일 카테고리 (AI | 개발 | 비즈니스 | 스타트업 | 마케팅 | 기타).
    action_required:
        즉각적인 행동이 필요한 경우 True.
    tokens_used:
        이 요약에 사용된 총 토큰 수 (input + output).
    """

    message_id: str
    subject: str
    sender: str
    received_at: datetime
    importance_score: int  # 1–5
    summary: str
    key_points: list[str]
    category: str  # AI | 개발 | 비즈니스 | 스타트업 | 마케팅 | 기타
    action_required: bool
    tokens_used: int = 0


def _make_default_result(email: ParsedEmail, reason: str = "요약 실패") -> SummaryResult:
    """요약 실패 시 반환할 기본 SummaryResult 생성.

    Parameters
    ----------
    email:
        원본 파싱 이메일 객체.
    reason:
        실패 사유 메시지.

    Returns
    -------
    SummaryResult
        importance_score=3, summary=reason 으로 채워진 기본 결과.
    """
    return SummaryResult(
        message_id=email.message_id,
        subject=email.subject,
        sender=email.sender,
        received_at=email.received_at,
        importance_score=3,
        summary=reason,
        key_points=[],
        category="기타",
        action_required=False,
        tokens_used=0,
    )


# ---------------------------------------------------------------------------
# Claude Client
# ---------------------------------------------------------------------------


class ClaudeClient:
    """Anthropic Claude API를 이용한 뉴스레터 요약 클라이언트.

    Parameters
    ----------
    api_key:
        Anthropic API 키.
    model:
        사용할 Claude 모델 식별자. 기본값: ``"claude-sonnet-4-6"``.

    Examples
    --------
    >>> client = ClaudeClient(api_key="sk-ant-...")
    >>> result = client.summarize(parsed_email)
    >>> print(result.summary)
    """

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6") -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        logger.debug("ClaudeClient 초기화 완료. 모델: %s", self._model)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def summarize(self, email: ParsedEmail) -> SummaryResult:
        """단일 이메일 요약 (동기).

        Claude API를 호출하여 이메일 본문을 분석하고 요약 결과를 반환합니다.
        API 오류 발생 시 기본 SummaryResult를 반환하며 예외를 전파하지 않습니다.

        Parameters
        ----------
        email:
            요약할 파싱된 이메일 객체.

        Returns
        -------
        SummaryResult
            요약 결과. 실패 시 importance_score=3, summary="요약 실패" 포함.
        """
        user_prompt = build_analysis_prompt(
            subject=email.subject,
            body=email.plain_text,
        )

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                )
            except anthropic.APIError as exc:
                logger.warning(
                    "Claude API 오류 (시도 %d/%d) — message_id=%s: %s",
                    attempt,
                    _MAX_RETRIES,
                    email.message_id,
                    exc,
                )
                if attempt == _MAX_RETRIES:
                    return _make_default_result(email, reason=f"요약 실패: {exc}")
                time.sleep(attempt * 1.0)  # 지수 백오프 (1s, 2s)
                continue
            except Exception as exc:  # pylint: disable=broad-except
                logger.error(
                    "예상치 못한 오류 — message_id=%s: %s",
                    email.message_id,
                    exc,
                    exc_info=True,
                )
                return _make_default_result(email, reason=f"요약 실패: {exc}")

            # 토큰 사용량 추출
            tokens_used = 0
            if response.usage:
                tokens_used = (
                    getattr(response.usage, "input_tokens", 0)
                    + getattr(response.usage, "output_tokens", 0)
                )

            logger.debug(
                "요약 완료 — message_id=%s | 토큰: %d",
                email.message_id,
                tokens_used,
            )

            # 응답 텍스트 추출
            response_text = ""
            if response.content:
                for block in response.content:
                    if hasattr(block, "text"):
                        response_text += block.text

            # JSON 파싱 및 유효성 검증
            parsed = parse_claude_response(response_text)

            return SummaryResult(
                message_id=email.message_id,
                subject=email.subject,
                sender=email.sender,
                received_at=email.received_at,
                importance_score=parsed["importance_score"],
                summary=parsed["summary"],
                key_points=parsed["key_points"],
                category=parsed["category"],
                action_required=parsed["action_required"],
                tokens_used=tokens_used,
            )

        # 이 지점에 도달해서는 안 되지만 타입 안전성을 위해 기본값 반환
        return _make_default_result(email, reason="요약 실패: 최대 재시도 초과")

    def summarize_batch(self, emails: list[ParsedEmail]) -> list[SummaryResult]:
        """배치 요약 — 각 메일을 순차 처리.

        rate limit을 고려하여 요청 사이에 0.5초 간격을 둡니다.
        진행률은 INFO 레벨로 로깅됩니다.

        Parameters
        ----------
        emails:
            요약할 파싱된 이메일 목록.

        Returns
        -------
        list[SummaryResult]
            입력 이메일 순서와 동일한 순서의 요약 결과 목록.
            개별 요약 실패 시 해당 항목은 기본값으로 채워집니다.
        """
        total = len(emails)
        if total == 0:
            logger.info("배치 요약: 처리할 이메일이 없습니다.")
            return []

        results: list[SummaryResult] = []
        total_tokens = 0

        for i, email in enumerate(emails):
            logger.info("요약 중: %d/%d — %s", i + 1, total, email.subject[:60])

            result = self.summarize(email)
            results.append(result)
            total_tokens += result.tokens_used

            # 마지막 항목이 아닌 경우에만 대기 (rate limit 방어)
            if i < total - 1:
                time.sleep(_BATCH_INTERVAL_SECONDS)

        logger.info(
            "배치 요약 완료: %d건 처리 | 총 토큰 사용량: %d",
            total,
            total_tokens,
        )
        return results
