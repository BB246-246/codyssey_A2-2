"""도메인 데이터 모델과 시간 유틸리티.

모든 시각은 timezone-aware UTC ISO 8601 문자열로 저장한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# 상태 상수
RAW_STATUS_OK = "ok"
RAW_STATUS_PARTIAL = "partial"
RAW_STATUS_ERROR = "error"

CLEAN_STATUS_OK = "ok"
CLEAN_STATUS_NO_BODY = "no_body"
CLEAN_STATUS_INVALID = "invalid"

METHOD_RSS = "rss"
METHOD_CRAWL = "crawl"


def utc_now() -> datetime:
    """timezone-aware 현재 UTC 시각."""
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    """datetime을 UTC 기준 ISO 8601 문자열로 변환한다."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def utc_now_iso() -> str:
    """현재 UTC 시각의 ISO 8601 문자열."""
    return to_iso(utc_now())


@dataclass
class RawArticle:
    """수집 직후의 원본 레코드."""

    source_name: str
    collection_method: str
    canonical_url: str
    raw_payload: str
    status: str = RAW_STATUS_OK
    collected_at: str = field(default_factory=utc_now_iso)
    external_id: str | None = None
    source_url: str | None = None
    error_message: str | None = None
    id: int | None = None


@dataclass
class CleanArticle:
    """정제된 기사 레코드."""

    raw_id: int
    title: str
    url: str
    source: str
    category: str
    collected_at: str
    body: str | None = None
    published_at: str | None = None
    content_hash: str | None = None
    clean_status: str = CLEAN_STATUS_OK
    summary: str | None = None
    summary_model: str | None = None
    summarized_at: str | None = None
    original_length: int | None = None
    summary_length: int | None = None
    id: int | None = None


@dataclass
class AnalysisResult:
    """AI 종합 분석 결과."""

    trends: list[str]
    keywords: list[str]
    implications: list[str]
    commonalities_differences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trends": self.trends,
            "keywords": self.keywords,
            "commonalities_differences": self.commonalities_differences,
            "implications": self.implications,
        }


@dataclass
class FetchStats:
    """fetch 실행 통계."""

    source_name: str
    collection_method: str
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    duplicates: int = 0
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str | None = None

    def summary_line(self) -> str:
        return (
            f"source={self.source_name} method={self.collection_method} "
            f"attempted={self.attempted} success={self.succeeded} "
            f"failure={self.failed} duplicate={self.duplicates}"
        )


@dataclass
class SummarizeStats:
    """summarize 실행 통계."""

    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0

    def summary_line(self) -> str:
        return (
            f"attempted={self.attempted} success={self.succeeded} "
            f"failure={self.failed} skipped={self.skipped}"
        )