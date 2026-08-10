"""수집기 패키지와 fetch 오케스트레이션."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..cleaner import normalize_url
from ..config import AppConfig, SourceConfig
from ..models import (
    METHOD_CRAWL,
    METHOD_RSS,
    RAW_STATUS_ERROR,
    RAW_STATUS_OK,
    RAW_STATUS_PARTIAL,
    FetchStats,
    RawArticle,
    utc_now_iso,
)
from ..storage import Storage
from . import rss_collector, web_collector
from .base import (
    CollectedItem,
    CollectResult,
    FetchError,
    HttpFetcher,
    RobotsDisallowed,
    build_fetcher,
)

logger = logging.getLogger(__name__)

METHOD_TO_SOURCE_TYPE = {METHOD_RSS: "rss", METHOD_CRAWL: "web"}

__all__ = [
    "CollectedItem",
    "CollectResult",
    "FetchError",
    "HttpFetcher",
    "RobotsDisallowed",
    "build_fetcher",
    "rss_collector",
    "web_collector",
    "run_fetch",
]


def _json_default(value: Any) -> Any:
    """time.struct_time 등 JSON 직렬화 불가 타입을 문자열로 낮춘다."""
    if isinstance(value, (list, tuple)):
        return list(value)
    return str(value)


def run_fetch(
    storage: Storage,
    config: AppConfig,
    *,
    source: SourceConfig,
    method: str,
    limit: int = 20,
    fetcher: HttpFetcher | None = None,
) -> FetchStats:
    """소스에서 뉴스를 수집해 raw_articles에 저장하고 통계를 남긴다."""
    expected_type = METHOD_TO_SOURCE_TYPE.get(method)
    if expected_type is None:
        raise FetchError(f"알 수 없는 수집 방법: {method!r}")
    if source.type != expected_type:
        raise FetchError(
            f"소스 '{source.name}'의 type은 '{source.type}'인데 --method {method}로 호출했습니다. "
            f"--method {'rss' if source.type == 'rss' else 'crawl'}를 사용하세요."
        )

    owns_fetcher = fetcher is None
    fetcher = fetcher or build_fetcher(config)
    stats = FetchStats(source_name=source.name, collection_method=method)

    try:
        module = rss_collector if method == METHOD_RSS else web_collector
        result = module.collect(source, fetcher, limit=limit)

        stats.attempted = max(result.attempted, len(result.items) + len(result.errors))

        for item in result.items:
            canonical = normalize_url(item.url)
            if not canonical:
                stats.failed += 1
                logger.error("URL이 비어 있어 저장하지 못했습니다: %r", item.url)
                continue

            status = RAW_STATUS_OK
            error_message = None
            if not item.title:
                status = RAW_STATUS_PARTIAL
                error_message = "제목을 찾지 못했습니다"
                logger.warning("제목 누락: %s", canonical)

            raw = RawArticle(
                source_name=source.name,
                collection_method=method,
                canonical_url=canonical,
                raw_payload=json.dumps(item.to_payload(source.name), ensure_ascii=False, default=_json_default),
                status=status,
                collected_at=utc_now_iso(),
                external_id=item.external_id,
                source_url=item.source_url,
                error_message=error_message,
            )
            outcome, _ = storage.save_raw_article(raw)
            if outcome == "duplicate":
                stats.duplicates += 1
            else:
                stats.succeeded += 1

        for url, message in result.errors:
            stats.failed += 1
            canonical = normalize_url(url)
            if not canonical:
                continue
            failure = RawArticle(
                source_name=source.name,
                collection_method=method,
                canonical_url=canonical,
                raw_payload=json.dumps({"url": url, "error": message}, ensure_ascii=False),
                status=RAW_STATUS_ERROR,
                collected_at=utc_now_iso(),
                source_url=source.url or source.list_url,
                error_message=message,
            )
            storage.save_raw_article(failure)
    finally:
        stats.finished_at = utc_now_iso()
        if owns_fetcher:
            fetcher.close()

    storage.save_fetch_run(stats)
    logger.info("수집 완료: %s", stats.summary_line())
    return stats