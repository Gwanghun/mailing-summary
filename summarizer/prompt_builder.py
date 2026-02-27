"""
summarizer/prompt_builder.py

Claude API 프롬프트 설계 모듈.
뉴스레터 분석용 시스템 프롬프트 정의 및 Claude 응답 파싱 유틸리티를 제공합니다.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """당신은 전문적인 뉴스레터 분석가입니다. 주어진 이메일 뉴스레터를 분석하고 한국어로 핵심 내용을 요약해 주세요.

## 중요도 점수 기준 (1–5점)

| 점수 | 기준 | 예시 |
|------|------|------|
| 5 | 긴급 / 마감 임박 / 즉각적인 행동이 필요한 항목 | 보안 취약점 패치 공지, 오늘 마감 이벤트, 긴급 정책 변경 |
| 4 | 트렌드 변화 / 산업에 중대한 영향을 미치는 내용 | 주요 기술 출시, 시장 패러다임 전환, 중요한 규제 변화 |
| 3 | 일반 정보 / 알아두면 유용한 내용 | 업계 동향, 기술 튜토리얼, 케이스 스터디 |
| 2 | 참고용 / 나중에 읽어도 되는 내용 | 일반 블로그 포스팅, 간단한 팁, 큐레이션 링크 모음 |
| 1 | 광고성 / 프로모션 / 정보 가치가 낮은 내용 | 할인 쿠폰, 제품 홍보, 구독 유도 이메일 |

## 응답 형식

반드시 아래 JSON 형식으로만 응답하세요. JSON 외의 텍스트는 절대 포함하지 마세요.

```json
{
  "importance_score": <1–5 사이의 정수>,
  "summary": "<3–5줄 분량의 핵심 요약 (한국어)>",
  "key_points": [
    "<핵심 포인트 1>",
    "<핵심 포인트 2>",
    "<핵심 포인트 3>"
  ],
  "category": "<AI | 개발 | 비즈니스 | 스타트업 | 마케팅 | 기타 중 하나>",
  "action_required": <true | false>
}
```

## 작성 지침

- **summary**: 독자가 30초 안에 핵심을 파악할 수 있도록 3–5줄로 작성하세요. 한국어로 작성합니다.
- **key_points**: 이메일에서 가장 중요한 포인트를 최대 3개까지 한국어 문장으로 작성하세요.
- **category**: 이메일의 주제에 가장 잘 맞는 카테고리를 하나만 선택하세요.
  - AI: 인공지능, 머신러닝, LLM 관련
  - 개발: 프로그래밍, DevOps, 오픈소스, 도구
  - 비즈니스: 경영, 전략, 투자, 금융
  - 스타트업: 창업, 펀딩, 스타트업 생태계
  - 마케팅: 마케팅, 성장 해킹, SEO, 광고
  - 기타: 위 카테고리에 해당하지 않는 내용
- **action_required**: 독자가 특정 기한 내에 행동해야 하거나 즉각적인 대응이 필요한 경우 true로 설정하세요.
"""


# ---------------------------------------------------------------------------
# Prompt Builder
# ---------------------------------------------------------------------------


def build_analysis_prompt(subject: str, body: str) -> str:
    """뉴스레터 분석 프롬프트 생성.

    Parameters
    ----------
    subject:
        이메일 제목.
    body:
        이메일 본문 텍스트 (HTML이 이미 제거된 상태).

    Returns
    -------
    str
        Claude user-turn 메시지로 전달할 프롬프트 문자열.
    """
    # 본문이 너무 길면 처음 4,000자만 사용 (토큰 절약)
    MAX_BODY_CHARS = 4_000
    truncated_body = body[:MAX_BODY_CHARS]
    if len(body) > MAX_BODY_CHARS:
        truncated_body += "\n\n[... 본문이 너무 길어 일부 생략되었습니다 ...]"

    prompt = (
        f"다음 뉴스레터 이메일을 분석해 주세요.\n\n"
        f"## 제목\n{subject}\n\n"
        f"## 본문\n{truncated_body}"
    )
    return prompt


# ---------------------------------------------------------------------------
# Response Parser
# ---------------------------------------------------------------------------

_DEFAULT_RESPONSE: dict[str, Any] = {
    "importance_score": 3,
    "summary": "요약 실패: Claude 응답을 파싱할 수 없습니다.",
    "key_points": [],
    "category": "기타",
    "action_required": False,
}

_VALID_CATEGORIES = {"AI", "개발", "비즈니스", "스타트업", "마케팅", "기타"}


def parse_claude_response(response_text: str) -> dict[str, Any]:
    """Claude 응답 JSON 파싱 및 유효성 검증.

    Claude가 JSON 코드 블록(```json ... ```) 또는 순수 JSON 문자열을
    반환하는 두 가지 경우를 모두 처리합니다.

    Parameters
    ----------
    response_text:
        Claude API 응답의 텍스트 내용.

    Returns
    -------
    dict
        파싱된 분석 결과. 파싱 실패 또는 필수 필드 누락 시 기본값 반환.
        포함 필드:
        - importance_score (int): 1–5
        - summary (str): 한국어 요약
        - key_points (list[str]): 핵심 포인트 목록 (최대 3개)
        - category (str): AI | 개발 | 비즈니스 | 스타트업 | 마케팅 | 기타
        - action_required (bool): 즉각적 행동 필요 여부
    """
    text = response_text.strip()

    # 코드 블록 래핑 제거 (```json ... ``` 또는 ``` ... ```)
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if start != end:
            inner = text[start + 3 : end].strip()
            # 언어 식별자 제거 (json, JSON 등)
            if inner.lower().startswith("json"):
                inner = inner[4:].strip()
            text = inner

    try:
        data: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Claude 응답 JSON 파싱 실패: %s | 응답: %.200s", exc, response_text)
        return dict(_DEFAULT_RESPONSE)

    result = dict(_DEFAULT_RESPONSE)

    # importance_score: int, 1–5 범위 검증
    raw_score = data.get("importance_score")
    if isinstance(raw_score, (int, float)):
        clamped = max(1, min(5, int(raw_score)))
        result["importance_score"] = clamped
    else:
        logger.debug("importance_score 누락 또는 잘못된 타입: %r", raw_score)

    # summary: str
    raw_summary = data.get("summary")
    if isinstance(raw_summary, str) and raw_summary.strip():
        result["summary"] = raw_summary.strip()
    else:
        logger.debug("summary 누락 또는 빈 문자열: %r", raw_summary)

    # key_points: list[str], 최대 3개
    raw_kp = data.get("key_points")
    if isinstance(raw_kp, list):
        points = [str(p).strip() for p in raw_kp if str(p).strip()]
        result["key_points"] = points[:3]
    else:
        logger.debug("key_points 누락 또는 잘못된 타입: %r", raw_kp)

    # category: str, 허용 목록 검증
    raw_cat = data.get("category")
    if isinstance(raw_cat, str) and raw_cat.strip() in _VALID_CATEGORIES:
        result["category"] = raw_cat.strip()
    else:
        logger.debug("category 누락 또는 허용되지 않는 값: %r", raw_cat)

    # action_required: bool
    raw_action = data.get("action_required")
    if isinstance(raw_action, bool):
        result["action_required"] = raw_action
    elif isinstance(raw_action, str):
        result["action_required"] = raw_action.lower() in {"true", "1", "yes"}
    else:
        logger.debug("action_required 누락 또는 잘못된 타입: %r", raw_action)

    return result
