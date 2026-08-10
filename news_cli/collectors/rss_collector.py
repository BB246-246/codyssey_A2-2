"""RSS/Atom 수집기.

`parse_feed`는 순수 함수라서 네트워크 없이 fixture로 테스트할 수 있다.
"""

from __future__ import annotations

import logging
from typing import Any

import feedparser

from ..config import SourceConfig
from .base import CollectedItem, CollectResult, FetchError, HttpFetcher

logger = logging.getLogger(__name__)


def _first_text(entry: Any, *keys: str) -> str | None:
    for key in keys:
        value = entry.get(key) if hasattr(entry, "get") else None
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, list) and value:
            candidate = value[0]
            text = candidate.get("value") if isinstance(candidate, dict) else None
            if isinstance(text, str) and text.strip():
                return text
    return None


def parse_feed(feed_content: str | bytes, source: SourceConfig, *, limit: int | None = None) -> CollectResult:
    """RSS/Atom 문서를 파싱해 CollectedItem 목록을 만든다.

    한 항목이 깨져도 나머지 항목은 계속 처리한다.
    """
    parsed = feedparser.parse(feed_content)
    result = CollectResult()

    if getattr(parsed, "bozo", 0) and getattr(parsed, "entries", None) in (None, []):
        message = str(getattr(parsed, "bozo_exception", "알 수 없는 파싱 오류"))
        logger.error("RSS 파싱 실패: %s", message)
        result.errors.append((source.url or "", f"RSS 파싱 실패: {message}"))
        return result

    entries = list(parsed.entries or [])
    if limit is not None:
        entries = entries[: max(0, int(limit))]

    for entry in entries:
        result.attempted += 1
        try:
            url = _first_text(entry, "link", "id")
            if not url:
                links = entry.get("links") or []
                url = next((l.get("href") for l in links if l.get("href")), None)
            if not url:
                raise ValueError("항목에 link가 없습니다")

            title = _first_text(entry, "title") or ""
            body = _first_text(entry, "content", "summary", "description", "subtitle")
            published = (
                entry.get("published")
                or entry.get("updated")
                or entry.get("created")
                or entry.get("published_parsed")
                or entry.get("updated_parsed")
            )

            result.items.append(
                CollectedItem(
                    url=url,
                    title=title,
                    body=body,
                    published_at=published,
                    external_id=_first_text(entry, "id", "guid") or url,
                    category=source.category,
                    source_url=source.url,
                    extra={"feed_title": getattr(parsed.feed, "title", None)},
                )
            )
        except Exception as exc:  # 개별 항목 실패는 전체를 막지 않는다
            logger.error("RSS 항목 처리 실패: %s", exc)
            result.errors.append((source.url or "", f"RSS 항목 처리 실패: {exc}"))

    logger.info(
        "RSS 파싱 완료: source=%s entries=%d ok=%d error=%d",
        source.name,
        result.attempted,
        len(result.items),
        len(result.errors),
    )
    return result


def collect(source: SourceConfig, fetcher: HttpFetcher, *, limit: int | None = None) -> CollectResult:
    """실제 네트워크에서 RSS를 내려받아 파싱한다."""
    if not source.url:
        raise FetchError(f"RSS 소스 '{source.name}'에 url이 없습니다.")

    logger.info("RSS 수집 시작: source=%s url=%s limit=%s", source.name, source.url, limit)
    try:
        content = fetcher.get_text(source.url, respect_delay=False)
    except FetchError as exc:
        logger.error("RSS 다운로드 실패: %s", exc)
        result = CollectResult()
        result.errors.append((source.url, str(exc)))
        return result

    return parse_feed(content, source, limit=limit)