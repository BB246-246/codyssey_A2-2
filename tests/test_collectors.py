"""수집기 테스트 (fixture와 mock만 사용, 실제 네트워크 없음)."""

from __future__ import annotations

import dataclasses
import json

import pytest
import requests

from news_cli.collectors import run_fetch
from news_cli.collectors.base import FetchError, HttpFetcher, RobotsDisallowed
from news_cli.collectors.rss_collector import collect as rss_collect, parse_feed
from news_cli.collectors.web_collector import (
    collect as web_collect,
    parse_article_html,
    parse_list_html,
)


# ---------------------------------------------------------------------------
# 테스트용 가짜 HTTP 계층
# ---------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"
        self.content = text.encode("utf-8")


class FakeSession:
    """requests.Session 대역. url -> 응답/예외 매핑."""

    def __init__(self, mapping: dict[str, object], default: object | None = None):
        self.mapping = mapping
        self.default = default
        self.headers: dict[str, str] = {}
        self.requested: list[str] = []

    def get(self, url: str, timeout: float | None = None, **kwargs):
        self.requested.append(url)
        result = self.mapping.get(url, self.default)
        if result is None:
            return FakeResponse("", status_code=404)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        pass


def make_fetcher(mapping, default=None, **kwargs) -> HttpFetcher:
    session = FakeSession(mapping, default)
    fetcher = HttpFetcher(
        user_agent="test-agent",
        timeout=kwargs.pop("timeout", 5.0),
        delay=kwargs.pop("delay", 0.0),
        respect_robots=kwargs.pop("respect_robots", False),
        session=session,
    )
    return fetcher


# ---------------------------------------------------------------------------
# RSS
# ---------------------------------------------------------------------------
def test_parse_rss_fixture(sample_feed_text, rss_source):
    result = parse_feed(sample_feed_text, rss_source)

    assert result.attempted == 4
    # link가 없는 항목 1개는 실패 처리되고 나머지는 계속 처리된다
    assert len(result.items) == 3
    assert len(result.errors) == 1

    first = result.items[0]
    assert first.title == "오픈소스 LLM이 기업 도입을 가속한다"
    assert "example.test/news/1" in first.url
    assert first.category == "AI"
    assert first.published_at


def test_parse_rss_respects_limit(sample_feed_text, rss_source):
    result = parse_feed(sample_feed_text, rss_source, limit=2)
    assert result.attempted == 2
    assert len(result.items) == 2


def test_parse_rss_invalid_document(rss_source):
    result = parse_feed("이건 RSS가 아닙니다", rss_source)
    assert result.items == []
    assert result.errors


def test_rss_collect_uses_http_layer(sample_feed_text, rss_source):
    fetcher = make_fetcher({rss_source.url: FakeResponse(sample_feed_text)})
    result = rss_collect(rss_source, fetcher, limit=2)
    assert len(result.items) == 2
    assert fetcher.session.requested == [rss_source.url]


def test_rss_collect_reports_http_error(rss_source):
    fetcher = make_fetcher({rss_source.url: FakeResponse("", status_code=500)})
    result = rss_collect(rss_source, fetcher)
    assert result.items == []
    assert "500" in result.errors[0][1]


# ---------------------------------------------------------------------------
# Web crawl
# ---------------------------------------------------------------------------
def test_parse_list_html_fixture(sample_list_html, web_source):
    urls = parse_list_html(sample_list_html, web_source)
    assert urls == [
        "https://example.test/wiki/article-1",
        "https://example.test/wiki/article-2",
        "https://example.test/wiki/article-3",
    ]


def test_parse_list_html_limit(sample_list_html, web_source):
    assert len(parse_list_html(sample_list_html, web_source, limit=2)) == 2


def test_parse_article_html_fixture(sample_article_html, web_source):
    item = parse_article_html(sample_article_html, web_source, "https://example.test/wiki/article-1")
    assert item.title == "한국어 기사 제목: AI 반도체 투자 확대"
    assert "AI" in item.body and "반도체" in item.body
    assert "tracking" not in item.body  # script 내용은 본문에 포함되지 않는다
    assert item.published_at == "Thursday, August 6, 2026"
    assert item.category == "IT"


def test_parse_article_html_falls_back_when_selector_misses(sample_article_html, web_source):
    broken = dataclasses.replace(web_source, title_selector="h9#nope", body_selector="p.nope")
    item = parse_article_html(sample_article_html, broken, "https://example.test/x")
    assert item.title  # h1 fallback으로 제목을 얻는다
    assert item.body is None  # 본문 선택자가 깨지면 None (예외 없음)


def test_web_collect_continues_after_single_failure(sample_list_html, sample_article_html, web_source):
    mapping = {
        web_source.list_url: FakeResponse(sample_list_html),
        "https://example.test/wiki/article-1": FakeResponse(sample_article_html),
        "https://example.test/wiki/article-2": FakeResponse("", status_code=503),
        "https://example.test/wiki/article-3": FakeResponse(sample_article_html),
    }
    fetcher = make_fetcher(mapping)
    result = web_collect(web_source, fetcher, limit=3)

    assert result.attempted == 3
    assert len(result.items) == 2
    assert len(result.errors) == 1
    assert "503" in result.errors[0][1]


def test_web_collect_handles_list_page_failure(web_source):
    fetcher = make_fetcher({web_source.list_url: requests.ConnectionError("연결 거부")})
    result = web_collect(web_source, fetcher)
    assert result.items == []
    assert result.errors


# ---------------------------------------------------------------------------
# HTTP 계층 (timeout / robots / delay)
# ---------------------------------------------------------------------------
def test_timeout_is_converted_to_fetch_error():
    fetcher = make_fetcher({"https://example.test/x": requests.Timeout("timed out")})
    with pytest.raises(FetchError) as excinfo:
        fetcher.get("https://example.test/x")
    assert "시간 초과" in str(excinfo.value)


def test_http_error_status_raises():
    fetcher = make_fetcher({"https://example.test/x": FakeResponse("", status_code=404)})
    with pytest.raises(FetchError):
        fetcher.get("https://example.test/x")


def test_timeout_value_is_passed_to_requests(monkeypatch):
    captured = {}

    class CapturingSession(FakeSession):
        def get(self, url, timeout=None, **kwargs):
            captured["timeout"] = timeout
            return super().get(url, timeout=timeout, **kwargs)

    session = CapturingSession({"https://example.test/x": FakeResponse("ok")})
    fetcher = HttpFetcher(user_agent="t", timeout=3.5, delay=0, respect_robots=False, session=session)
    fetcher.get("https://example.test/x")
    assert captured["timeout"] == 3.5


def test_user_agent_header_is_set():
    session = FakeSession({})
    HttpFetcher(user_agent="my-agent/9", timeout=1, delay=0, respect_robots=False, session=session)
    assert session.headers["User-Agent"] == "my-agent/9"


def test_robots_disallow_blocks_request():
    robots = "User-agent: *\nDisallow: /private\n"
    mapping = {
        "https://example.test/robots.txt": FakeResponse(robots),
        "https://example.test/private/a": FakeResponse("secret"),
    }
    fetcher = make_fetcher(mapping, respect_robots=True)
    with pytest.raises(RobotsDisallowed):
        fetcher.get("https://example.test/private/a")


def test_robots_allows_other_paths():
    robots = "User-agent: *\nDisallow: /private\n"
    mapping = {
        "https://example.test/robots.txt": FakeResponse(robots),
        "https://example.test/public/a": FakeResponse("hello"),
    }
    fetcher = make_fetcher(mapping, respect_robots=True)
    assert fetcher.get_text("https://example.test/public/a") == "hello"


def test_delay_is_applied_between_requests(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("news_cli.collectors.base.time.sleep", lambda s: slept.append(s))

    mapping = {f"https://example.test/{i}": FakeResponse("ok") for i in range(3)}
    fetcher = make_fetcher(mapping, delay=1.5)
    for i in range(3):
        fetcher.get(f"https://example.test/{i}")

    # 첫 요청 전에는 대기하지 않고, 이후 요청 전에 대기한다
    assert len(slept) == 2
    assert all(0 < s <= 1.5 for s in slept)


# ---------------------------------------------------------------------------
# run_fetch 통합 (저장소까지)
# ---------------------------------------------------------------------------
def test_run_fetch_rss_saves_raw_and_stats(storage, app_config, sample_feed_text):
    source = app_config.get_source("default_rss")
    fetcher = make_fetcher({source.url: FakeResponse(sample_feed_text)})

    stats = run_fetch(storage, app_config, source=source, method="rss", limit=10, fetcher=fetcher)

    assert stats.succeeded == 3
    assert stats.failed == 1  # link 없는 항목
    assert storage.count_raw_articles() >= 3

    rows = storage.list_raw_articles(status="ok")
    payload = json.loads(rows[0]["raw_payload"])
    assert payload["title"]
    assert storage.list_fetch_runs()[0]["success_count"] == 3


def test_run_fetch_second_run_counts_duplicates(storage, app_config, sample_feed_text):
    source = app_config.get_source("default_rss")
    for _ in range(2):
        fetcher = make_fetcher({source.url: FakeResponse(sample_feed_text)})
        stats = run_fetch(storage, app_config, source=source, method="rss", limit=10, fetcher=fetcher)

    assert stats.duplicates == 3
    assert stats.succeeded == 0


def test_run_fetch_rejects_method_source_mismatch(storage, app_config):
    source = app_config.get_source("default_rss")
    with pytest.raises(FetchError) as excinfo:
        run_fetch(storage, app_config, source=source, method="crawl", limit=1, fetcher=make_fetcher({}))
    assert "type" in str(excinfo.value)


def test_run_fetch_crawl(storage, app_config, sample_list_html, sample_article_html):
    source = app_config.get_source("default_web")
    mapping = {
        source.list_url: FakeResponse(sample_list_html),
        "https://example.test/wiki/article-1": FakeResponse(sample_article_html),
        "https://example.test/wiki/article-2": FakeResponse(sample_article_html),
        "https://example.test/wiki/article-3": FakeResponse(sample_article_html),
    }
    stats = run_fetch(
        storage, app_config, source=source, method="crawl", limit=3, fetcher=make_fetcher(mapping)
    )
    assert stats.attempted == 3
    assert stats.succeeded == 3
    assert stats.failed == 0