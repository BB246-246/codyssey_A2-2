"""기간/카테고리별 AI 종합 분석.

AI에 구조화 JSON을 요구하고, 필수 키(trends/keywords/implications)를 검증한다.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .ai_client import AIError, BaseAIClient
from .models import AnalysisResult
from .storage import Storage

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ("trends", "keywords", "implications")
OPTIONAL_KEYS = ("commonalities_differences",)

SYSTEM_PROMPT = (
    "당신은 뉴스 트렌드 분석가입니다. 제공된 기사 목록만을 근거로 분석하세요. "
    "기사에 없는 사실을 만들어내지 마세요. "
    "반드시 아래 스키마를 만족하는 JSON 객체 하나만 출력하세요. "
    "설명 문장, 마크다운 코드펜스, 주석을 붙이지 마세요.\n"
    '{"trends": [문자열...], "keywords": [문자열...], '
    '"commonalities_differences": [문자열...], "implications": [문자열...]}\n'
    "모든 값은 한국어 문자열이어야 하고, 각 배열은 최소 1개 이상의 항목을 포함해야 합니다."
)

USER_PROMPT_TEMPLATE = (
    "다음은 분석 대상 뉴스 목록입니다.\n"
    "조건: 기간={date_from}~{date_to}, 카테고리={category}, 기사 수={count}건\n\n"
    "{articles}\n\n"
    "위 기사들을 종합해 다음을 JSON으로 작성하세요.\n"
    "- trends: 주요 트렌드 3~5개\n"
    "- keywords: 핵심 키워드 5~10개\n"
    "- commonalities_differences: 기사들 간 공통점과 차이점 2~5개\n"
    "- implications: 시사점 3~5개\n"
)

_CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class AnalysisError(Exception):
    """분석 단계 오류."""


class NoArticlesError(AnalysisError):
    """조건에 맞는 기사가 없음."""


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _CODE_FENCE_RE.sub("", stripped).strip()
    return stripped


def extract_json_object(text: str) -> dict[str, Any]:
    """모델 응답에서 JSON 객체를 추출한다."""
    candidate = _strip_code_fence(text)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            raise AnalysisError("AI 응답에서 JSON 객체를 찾지 못했습니다.") from None
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AnalysisError(f"AI 응답 JSON 파싱 실패: {exc.msg}") from exc

    if not isinstance(parsed, dict):
        raise AnalysisError("AI 응답 JSON의 최상위가 객체가 아닙니다.")
    return parsed


def _coerce_str_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise AnalysisError(f"'{key}'는 문자열 배열이어야 합니다 (현재 타입: {type(value).__name__}).")
    items = [str(v).strip() for v in value if str(v).strip()]
    return items


def parse_analysis_response(text: str) -> AnalysisResult:
    """AI 응답 문자열을 검증된 AnalysisResult로 변환한다."""
    data = extract_json_object(text)

    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise AnalysisError(f"AI 응답에 필수 키가 없습니다: {', '.join(missing)}")

    values: dict[str, list[str]] = {}
    for key in REQUIRED_KEYS:
        items = _coerce_str_list(data.get(key), key)
        if not items:
            raise AnalysisError(f"AI 응답의 '{key}'가 비어 있습니다.")
        values[key] = items

    return AnalysisResult(
        trends=values["trends"],
        keywords=values["keywords"],
        implications=values["implications"],
        commonalities_differences=_coerce_str_list(
            data.get("commonalities_differences"), "commonalities_differences"
        ),
    )


def build_analysis_input(
    articles: list[dict[str, Any]],
    *,
    max_chars: int = 20000,
    max_body_chars: int = 800,
) -> tuple[str, int]:
    """제목+요약(없으면 잘린 본문)으로 분석 입력을 만든다.

    Returns: (프롬프트에 넣을 텍스트, 실제 포함된 기사 수)
    """
    blocks: list[str] = []
    total = 0
    used = 0

    for index, article in enumerate(articles, start=1):
        summary = (article.get("summary") or "").strip()
        if not summary:
            body = (article.get("body") or "").strip()
            summary = (body[:max_body_chars] + "…") if len(body) > max_body_chars else body
        if not summary:
            summary = "(요약/본문 없음)"

        block = (
            f"[{index}] 제목: {article.get('title') or '(제목 없음)'}\n"
            f"    출처: {article.get('source') or 'unknown'} | "
            f"카테고리: {article.get('category') or 'unknown'} | "
            f"일자: {(article.get('published_at') or article.get('collected_at') or '')[:10]}\n"
            f"    내용: {summary}"
        )
        if total + len(block) > max_chars and used > 0:
            logger.warning(
                "입력 글자 수 제한(%d자)에 도달해 %d건만 분석합니다.", max_chars, used
            )
            break
        blocks.append(block)
        total += len(block)
        used += 1

    return "\n\n".join(blocks), used


def analyze(
    storage: Storage,
    client: BaseAIClient,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    category: str | None = None,
    limit: int | None = None,
    max_articles: int = 50,
    max_chars: int = 20000,
) -> tuple[int, AnalysisResult, int]:
    """조건에 맞는 기사를 모아 AI 종합 분석을 수행하고 DB에 저장한다.

    Returns: (analysis_run_id, 결과, 분석에 사용한 기사 수)
    """
    effective_limit = min(limit or max_articles, max_articles)
    articles = storage.query_clean_articles(
        date_from=date_from,
        date_to=date_to,
        category=category,
        limit=effective_limit,
        order="DESC",
    )

    if not articles:
        raise NoArticlesError(
            "조건에 맞는 기사가 없습니다. "
            f"(date_from={date_from or '-'}, date_to={date_to or '-'}, category={category or '-'})\n"
            "먼저 'fetch'와 'clean'을 실행했는지, 날짜/카테고리 필터가 너무 좁지 않은지 확인하세요."
        )

    articles_text, used = build_analysis_input(articles, max_chars=max_chars)
    prompt = USER_PROMPT_TEMPLATE.format(
        date_from=date_from or "전체",
        date_to=date_to or "전체",
        category=category or "전체",
        count=used,
        articles=articles_text,
    )

    logger.info(
        "AI 분석 시작: 기사 %d건, 입력 %d자, model=%s",
        used,
        len(articles_text),
        getattr(client, "model", "unknown"),
    )

    try:
        raw_response = client.complete(SYSTEM_PROMPT, prompt, json_mode=True)
    except AIError as exc:
        raise AnalysisError(f"AI 분석 호출 실패: {exc}") from exc

    result = parse_analysis_response(raw_response)

    run_id = storage.save_analysis_run(
        result,
        article_count=used,
        date_from=date_from,
        date_to=date_to,
        category=category,
        model=getattr(client, "model", None),
    )
    logger.info("AI 분석 저장 완료: analysis_run id=%d (기사 %d건)", run_id, used)
    return run_id, result, used


def analysis_row_to_result(row: dict[str, Any]) -> AnalysisResult:
    """analysis_runs 한 행을 AnalysisResult로 되돌린다."""

    def _load(value: Any) -> list[str]:
        if not value:
            return []
        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            return []
        return [str(v) for v in data] if isinstance(data, list) else []

    return AnalysisResult(
        trends=_load(row.get("trends_json")),
        keywords=_load(row.get("keywords_json")),
        implications=_load(row.get("implications_json")),
        commonalities_differences=_load(row.get("commonalities_differences_json")),
    )