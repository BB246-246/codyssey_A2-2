"""RSS/Atom 수집기.

`parse_feed`는 순수 함수라서 네트워크 없이 fixture로 테스트할 수 있다.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser

from ..config import SourceConfig
from .base import CollectedItem, CollectResult, FetchError, HttpFetcher, resolve_auth_headers

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
            # 네이버 뉴스 검색 API는 언론사 원문 주소를 originallink로 준다.
            # 포털 재게시 주소(link)보다 안정적이라 중복 판정 키로 우선 사용한다.
            url = _first_text(entry, "originallink", "link", "id")
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


def build_request_url(source: SourceConfig, *, limit: int | None = None) -> str:
    """소스 url에 config의 params를 붙인 최종 요청 URL을 만든다.

    검색형 API(예: 네이버 뉴스 검색)는 `display`로 건수를 제어하므로,
    params에 display가 있으면 --limit 값으로 덮어써 불필요한 조회를 줄인다.
    """
    if not source.url:
        raise FetchError(f"RSS 소스 '{source.name}'에 url이 없습니다.")
    if not source.params:
        return source.url

    params = dict(source.params)
    if limit is not None and "display" in params:
        params["display"] = max(1, min(int(limit), 100))  # 네이버 API 상한 100

    parts = urlsplit(source.url)
    merged = dict(parse_qsl(parts.query, keep_blank_values=True))
    merged.update({str(k): str(v) for k, v in params.items()})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(merged), parts.fragment))


def collect(source: SourceConfig, fetcher: HttpFetcher, *, limit: int | None = None) -> CollectResult:
    """실제 네트워크에서 RSS(또는 RSS를 돌려주는 검색 API)를 내려받아 파싱한다."""
    result = CollectResult()
    try:
        url = build_request_url(source, limit=limit)
        headers = resolve_auth_headers(source)
    except FetchError as exc:
        logger.error("RSS 요청 준비 실패: %s", exc)
        result.errors.append((source.url or source.name, str(exc)))
        return result

    # 자격증명을 요구하는 공식 API는 robots.txt가 아니라 API 이용약관의 적용을 받는다.
    # (예: openapi.naver.com/robots.txt는 크롤러를 막기 위해 Disallow: / 이지만,
    #  발급받은 Client ID/Secret으로 호출하는 것은 제공자가 의도한 사용 방식이다.)
    is_authenticated_api = bool(headers)
    if is_authenticated_api:
        logger.info("인증된 API 호출이므로 robots.txt 검사를 건너뜁니다: %s", urlsplit(url).netloc)

    logger.info("RSS 수집 시작: source=%s url=%s limit=%s", source.name, url, limit)
    try:
        content = fetcher.get_text(
            url,
            respect_delay=False,
            headers=headers or None,
            check_robots=not is_authenticated_api,
        )
    except FetchError as exc:
        logger.error("RSS 다운로드 실패: %s", exc)
        result.errors.append((url, str(exc)))
        return result

    return parse_feed(content, source, limit=limit)