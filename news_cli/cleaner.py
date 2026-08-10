"""정제 계층.

raw_articles의 JSON payload를 읽어 정규화한 뒤 clean_articles에 적재한다.
텍스트/URL/날짜 정규화 함수는 collector와 storage에서도 재사용한다.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import (
    CLEAN_STATUS_INVALID,
    CLEAN_STATUS_NO_BODY,
    CLEAN_STATUS_OK,
    CleanArticle,
    to_iso,
)
from .storage import Storage

logger = logging.getLogger(__name__)

# 제거 대상 추적용 query parameter
TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "ref",
    "ref_src",
    "spm",
    "yclid",
    "_ga",
    "_hsenc",
    "_hsmi",
}
_TRACKING_PREFIXES = ("utm_",)

_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WS_RE = re.compile("[ 	   -   　​﻿]+")
_NEWLINES_RE = re.compile(r"\n{3,}")

# 흔한 날짜 포맷들 (RSS/HTML 혼재 대응)
_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d %B %Y",
    "%B %d, %Y",
    "%A, %B %d, %Y",
    "%b %d, %Y",
)


class CleanError(Exception):
    """정제 단계에서 복구 불가능한 오류."""


# --------------------------------------------------------------------------
# 정규화 헬퍼
# --------------------------------------------------------------------------
def strip_html(text: str | None) -> str:
    """HTML 태그와 엔티티를 제거한 평문을 돌려준다."""
    if not text:
        return ""
    without_blocks = _SCRIPT_STYLE_RE.sub(" ", text)
    # 블록 태그는 개행으로 바꿔 문단 구분을 살린다.
    with_breaks = re.sub(r"(?i)<(br\s*/?|/p|/div|/li|/h[1-6])>", "\n", without_blocks)
    return _TAG_RE.sub(" ", with_breaks)


def normalize_text(text: str | None) -> str:
    """Unicode(NFKC)/공백/개행을 정규화하고 HTML 엔티티를 해제한다."""
    if not text:
        return ""
    value = strip_html(text)
    value = html.unescape(value)
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(ch for ch in value if ch == "\n" or ch == "\t" or unicodedata.category(ch)[0] != "C")
    value = _WS_RE.sub(" ", value)
    value = "\n".join(line.strip() for line in value.split("\n"))
    value = _NEWLINES_RE.sub("\n\n", value)
    return value.strip()


def normalize_url(url: str | None) -> str:
    """canonical URL을 만든다.

    - 스킴/호스트 소문자화, 기본 포트 제거
    - fragment 제거
    - 추적 query parameter 제거 후 정렬
    - 루트가 아닌 경로의 끝 슬래시 제거
    """
    if not url:
        return ""
    cleaned = normalize_text(url).strip()
    if not cleaned:
        return ""

    parts = urlsplit(cleaned)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if (scheme == "http" and netloc.endswith(":80")) or (scheme == "https" and netloc.endswith(":443")):
        netloc = netloc.rsplit(":", 1)[0]

    path = parts.path or ""
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
        and not any(key.lower().startswith(prefix) for prefix in _TRACKING_PREFIXES)
    ]
    query = urlencode(sorted(kept))

    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_date(value: Any) -> str | None:
    """다양한 날짜 표현을 UTC ISO 8601 문자열로 정규화한다."""
    if value in (None, ""):
        return None

    if isinstance(value, datetime):
        return to_iso(value)

    if isinstance(value, (list, tuple)) and len(value) >= 6:
        try:
            return to_iso(datetime(*[int(v) for v in value[:6]], tzinfo=timezone.utc))
        except (TypeError, ValueError):
            return None

    text = normalize_text(str(value))
    if not text:
        return None

    # ISO 8601 (Z 접미사 포함)
    try:
        return to_iso(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        pass

    # RFC 2822 (RSS pubDate)
    try:
        return to_iso(parsedate_to_datetime(text))
    except (TypeError, ValueError, IndexError):
        pass

    for fmt in _DATE_FORMATS:
        try:
            return to_iso(datetime.strptime(text, fmt))
        except ValueError:
            continue

    # "Thursday, June 15, 2006 (extra text)" 같은 경우 앞부분만 재시도
    match = re.search(r"[A-Z][a-z]+ \d{1,2}, \d{4}", text)
    if match:
        try:
            return to_iso(datetime.strptime(match.group(0), "%B %d, %Y"))
        except ValueError:
            pass

    logger.warning("날짜 형식을 해석하지 못했습니다: %r", text[:60])
    return None


def content_hash(title: str, body: str | None) -> str:
    """정규화된 제목+본문의 SHA-256 해시 (중복 2차 보조 키)."""
    normalized = f"{normalize_text(title).casefold()}\n{normalize_text(body).casefold()}"
    collapsed = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# raw payload -> CleanArticle
# --------------------------------------------------------------------------
def build_clean_article(raw_row: dict[str, Any]) -> CleanArticle:
    """raw_articles 한 행을 CleanArticle로 변환한다.

    필수 필드(title, url)가 없으면 CleanError를 던진다.
    """
    try:
        payload = json.loads(raw_row["raw_payload"])
    except (TypeError, ValueError) as exc:
        raise CleanError(f"raw payload가 올바른 JSON이 아닙니다 (raw_id={raw_row.get('id')}): {exc}") from exc

    if not isinstance(payload, dict):
        raise CleanError(f"raw payload가 객체가 아닙니다 (raw_id={raw_row.get('id')})")

    title = normalize_text(payload.get("title"))
    url = normalize_url(payload.get("url") or raw_row.get("canonical_url"))

    if not title:
        raise CleanError(f"필수 필드 'title'이 비어 있습니다 (raw_id={raw_row.get('id')})")
    if not url:
        raise CleanError(f"필수 필드 'url'이 비어 있습니다 (raw_id={raw_row.get('id')})")

    body = normalize_text(payload.get("body") or payload.get("summary") or payload.get("description"))
    category = normalize_text(payload.get("category")) or normalize_text(raw_row.get("category")) or "unknown"
    published_at = normalize_date(payload.get("published_at") or payload.get("published"))

    status = CLEAN_STATUS_OK if body else CLEAN_STATUS_NO_BODY
    if not body:
        logger.warning("본문이 비어 있어 상태를 '%s'로 표시합니다: %s", CLEAN_STATUS_NO_BODY, url)

    return CleanArticle(
        raw_id=int(raw_row["id"]),
        title=title,
        url=url,
        source=str(raw_row.get("source_name") or payload.get("source") or "unknown"),
        category=category,
        collected_at=str(raw_row.get("collected_at")),
        body=body or None,
        published_at=published_at,
        content_hash=content_hash(title, body),
        clean_status=status,
        original_length=len(body) if body else 0,
    )


def clean_articles(
    storage: Storage,
    *,
    duplicate_policy: str = "skip",
    limit: int | None = None,
) -> dict[str, int]:
    """raw_articles를 정제해 clean_articles에 적재한다.

    같은 명령을 반복 실행해도 clean 레코드는 늘어나지 않는다(멱등).
    """
    if duplicate_policy not in ("skip", "upsert"):
        raise CleanError(f"duplicate_policy는 'skip' 또는 'upsert'여야 합니다 (현재 값: {duplicate_policy!r})")

    rows = storage.list_raw_articles(limit=limit, status="ok")
    stats = {"processed": 0, "inserted": 0, "updated": 0, "skipped": 0, "invalid": 0}

    logger.info("정제 시작: 대상 raw 기사 %d건, 정책=%s", len(rows), duplicate_policy)

    for row in rows:
        stats["processed"] += 1
        try:
            article = build_clean_article(row)
        except CleanError as exc:
            stats["invalid"] += 1
            logger.warning("정제 실패로 건너뜁니다: %s", exc)
            storage.mark_raw_status(int(row["id"]), CLEAN_STATUS_INVALID, str(exc))
            continue

        outcome = storage.save_clean_article(article, duplicate_policy=duplicate_policy)
        stats[outcome] = stats.get(outcome, 0) + 1

    logger.info(
        "정제 완료: processed=%d inserted=%d updated=%d skipped=%d invalid=%d",
        stats["processed"],
        stats["inserted"],
        stats["updated"],
        stats["skipped"],
        stats["invalid"],
    )
    return stats