"""정적 HTML 웹 크롤링 수집기.

선택자는 모두 config의 source 설정에서 읽는다. 사이트 구조가 바뀌어도
config만 고치면 되고 다른 모듈은 수정할 필요가 없다.

`parse_list_html` / `parse_article_html`은 순수 함수라서 fixture로 테스트한다.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..config import SourceConfig
from .base import CollectedItem, CollectResult, FetchError, HttpFetcher

logger = logging.getLogger(__name__)

_PARSER = "html.parser"  # 표준 라이브러리 기반이라 어느 환경에서나 동작


def _soup(markup: str) -> BeautifulSoup:
    return BeautifulSoup(markup, _PARSER)


def parse_list_html(markup: str, source: SourceConfig, *, limit: int | None = None) -> list[str]:
    """목록 페이지에서 기사 링크 목록을 뽑는다(절대 URL, 중복 제거)."""
    if not source.article_link_selector:
        raise FetchError(f"web 소스 '{source.name}'에 article_link_selector가 없습니다.")

    base = source.base_url or source.list_url or ""
    soup = _soup(markup)
    urls: list[str] = []
    seen: set[str] = set()

    for anchor in soup.select(source.article_link_selector):
        href = anchor.get("href")
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute = urljoin(base, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        urls.append(absolute)
        if limit is not None and len(urls) >= max(0, int(limit)):
            break

    logger.info("목록 페이지에서 링크 %d개를 찾았습니다 (source=%s)", len(urls), source.name)
    return urls


def parse_article_html(markup: str, source: SourceConfig, url: str) -> CollectedItem:
    """기사 상세 페이지에서 제목/본문/날짜를 뽑는다.

    선택자가 하나 실패해도 예외 대신 None으로 두어 후속 정제 단계가 상태를 판단한다.
    """
    soup = _soup(markup)

    title = None
    if source.title_selector:
        node = soup.select_one(source.title_selector)
        if node is not None:
            title = node.get_text(" ", strip=True)
    if not title:
        node = soup.select_one("h1") or soup.find("title")
        title = node.get_text(" ", strip=True) if node is not None else None
        if source.title_selector:
            logger.warning("title 선택자가 맞지 않아 fallback을 사용했습니다: %s", url)

    body = None
    if source.body_selector:
        nodes = soup.select(source.body_selector)
        chunks = [n.get_text(" ", strip=True) for n in nodes]
        body = "\n\n".join(c for c in chunks if c) or None
    if not body:
        logger.warning("본문을 찾지 못했습니다(선택자 확인 필요): %s", url)

    published = None
    if source.date_selector:
        node = soup.select_one(source.date_selector)
        if node is not None:
            # <time datetime>, <meta content>, 일반 텍스트 순으로 시도한다.
            published = (
                node.get("datetime") or node.get("content") or node.get_text(" ", strip=True)
            )
    if not published:
        meta = soup.select_one('meta[property="article:published_time"]') or soup.select_one(
            'meta[name="date"]'
        )
        if meta is not None:
            published = meta.get("content")

    return CollectedItem(
        url=url,
        title=title,
        body=body,
        published_at=published,
        external_id=url,
        category=source.category,
        source_url=source.list_url,
    )


def collect(source: SourceConfig, fetcher: HttpFetcher, *, limit: int | None = None) -> CollectResult:
    """목록 페이지 → 기사 상세 페이지 순으로 크롤링한다.

    개별 기사 실패는 errors에 남기고 다음 기사로 계속 진행한다.
    """
    if not source.list_url:
        raise FetchError(f"web 소스 '{source.name}'에 list_url이 없습니다.")

    result = CollectResult()
    logger.info("웹 크롤링 시작: source=%s list_url=%s limit=%s", source.name, source.list_url, limit)

    try:
        list_markup = fetcher.get_text(source.list_url, respect_delay=False)
    except FetchError as exc:
        logger.error("목록 페이지 수집 실패: %s", exc)
        result.errors.append((source.list_url, str(exc)))
        return result

    try:
        urls = parse_list_html(list_markup, source, limit=limit)
    except FetchError as exc:
        logger.error("목록 페이지 파싱 실패: %s", exc)
        result.errors.append((source.list_url, str(exc)))
        return result

    if not urls:
        logger.warning("목록 페이지에서 기사 링크를 찾지 못했습니다: %s", source.list_url)

    for url in urls:
        result.attempted += 1
        try:
            markup = fetcher.get_text(url)
            result.items.append(parse_article_html(markup, source, url))
        except FetchError as exc:
            logger.error("기사 수집 실패(계속 진행): %s", exc)
            result.errors.append((url, str(exc)))
        except Exception as exc:  # 파싱 예외도 전체를 막지 않는다
            logger.error("기사 파싱 실패(계속 진행): %s (%s)", url, exc)
            result.errors.append((url, f"파싱 실패: {exc}"))

    logger.info(
        "웹 크롤링 완료: source=%s attempted=%d ok=%d error=%d",
        source.name,
        result.attempted,
        len(result.items),
        len(result.errors),
    )
    return result