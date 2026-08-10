"""기사별 AI 요약.

기사 전문과 API 키는 로그에 남기지 않는다(길이와 id만 기록).
한 기사가 실패해도 ERROR 로깅 후 다음 기사로 계속 진행한다.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from .ai_client import AIError, BaseAIClient
from .models import SummarizeStats, utc_now_iso
from .storage import Storage

logger = logging.getLogger(__name__)

TargetMode = Literal["all", "id", "unsummarized"]

SYSTEM_PROMPT = (
    "당신은 뉴스 요약 전문가입니다. 주어진 기사 내용만을 근거로 요약하세요. "
    "기사에 없는 사실, 추측, 배경지식을 절대 추가하지 마세요. "
    "요약은 반드시 한국어로 작성하고, 핵심을 3~5개의 문장으로 정리하세요. "
    "머리말, 맺음말, 목록 기호, 마크다운 없이 문장만 출력하세요."
)

USER_PROMPT_TEMPLATE = (
    "다음 뉴스 기사를 한국어 3~5문장으로 요약하세요.\n"
    "기사에 없는 내용은 추가하지 마세요.\n\n"
    "제목: {title}\n"
    "출처: {source}\n"
    "카테고리: {category}\n"
    "본문:\n{body}\n"
)


class SummarizeError(Exception):
    """요약 대상 선택 등 사용자 입력 문제."""


def build_prompt(article: dict[str, Any], *, max_body_chars: int = 4000) -> str:
    """기사 dict에서 사용자 프롬프트를 만든다."""
    body = (article.get("body") or "").strip()
    if not body:
        body = "(본문 없음 - 제목만으로 요약하지 말고 정보 부족을 명시하세요)"
    elif len(body) > max_body_chars:
        body = body[:max_body_chars] + "\n...(이하 생략)"

    return USER_PROMPT_TEMPLATE.format(
        title=article.get("title") or "(제목 없음)",
        source=article.get("source") or "unknown",
        category=article.get("category") or "unknown",
        body=body,
    )


def select_targets(
    storage: Storage,
    *,
    mode: TargetMode,
    article_id: int | None = None,
    limit: int | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    """요약 대상 기사를 고른다."""
    if mode == "id":
        if article_id is None:
            raise SummarizeError("--id 모드에는 기사 id가 필요합니다.")
        article = storage.get_clean_article(article_id)
        if article is None:
            raise SummarizeError(f"id={article_id}인 clean 기사를 찾을 수 없습니다.")
        return [article]

    if mode == "unsummarized":
        return storage.query_clean_articles(status="unsummarized", limit=limit)

    if mode == "all":
        status = "all" if force else "unsummarized"
        return storage.query_clean_articles(status=status, limit=limit)

    raise SummarizeError(f"알 수 없는 요약 대상 모드: {mode!r}")


def summarize_articles(
    storage: Storage,
    client: BaseAIClient,
    *,
    mode: TargetMode = "unsummarized",
    article_id: int | None = None,
    limit: int | None = None,
    force: bool = False,
    max_body_chars: int = 4000,
) -> SummarizeStats:
    """대상 기사들을 요약해 저장한다."""
    targets = select_targets(
        storage, mode=mode, article_id=article_id, limit=limit, force=force
    )
    stats = SummarizeStats()
    model = getattr(client, "model", "unknown")

    logger.info("요약 시작: 대상 %d건 (mode=%s, force=%s, model=%s)", len(targets), mode, force, model)

    for article in targets:
        already = bool((article.get("summary") or "").strip())
        if already and not force:
            stats.skipped += 1
            logger.info("이미 요약된 기사 skip: id=%s", article["id"])
            continue

        stats.attempted += 1
        prompt = build_prompt(article, max_body_chars=max_body_chars)
        try:
            summary = client.complete(SYSTEM_PROMPT, prompt).strip()
            if not summary:
                raise AIError("빈 요약")
        except AIError as exc:
            stats.failed += 1
            # 기사 전문은 남기지 않고 id와 오류만 기록한다.
            logger.error("요약 실패(다음 기사로 계속): id=%s error=%s", article["id"], exc)
            continue
        except Exception as exc:  # 예기치 못한 오류도 전체를 중단시키지 않는다
            stats.failed += 1
            logger.error(
                "요약 중 예기치 못한 오류(다음 기사로 계속): id=%s error=%s",
                article["id"],
                exc.__class__.__name__,
            )
            continue

        storage.update_summary(
            int(article["id"]),
            summary=summary,
            model=model,
            original_length=len(article.get("body") or ""),
            summarized_at=utc_now_iso(),
        )
        stats.succeeded += 1
        logger.info(
            "요약 완료: id=%s original_len=%d summary_len=%d model=%s",
            article["id"],
            len(article.get("body") or ""),
            len(summary),
            model,
        )

    logger.info("요약 종료: %s", stats.summary_line())
    return stats