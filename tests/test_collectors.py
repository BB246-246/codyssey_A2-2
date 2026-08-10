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

# ---------------------------------------------------------------------------
# 인증이 필요한 검색 API (네이버 뉴스 검색) — fixture/mock만 사용
# ---------------------------------------------------------------------------
def test_build_request_url_merges_params(naver_source):
    from news_cli.collectors.rss_collector import build_request_url

    url = build_request_url(naver_source)
    assert url.startswith("https://openapi.naver.test/v1/search/news.xml?")
    assert "query=AI" in url and "sort=date" in url and "display=20" in url


def test_build_request_url_limit_overrides_display(naver_source):
    from news_cli.collectors.rss_collector import build_request_url

    assert "display=5" in build_request_url(naver_source, limit=5)
    # 네이버 API 상한(100)을 넘지 않는다
    assert "display=100" in build_request_url(naver_source, limit=500)


def test_auth_headers_read_from_env(naver_source, monkeypatch):
    from news_cli.collectors.base import resolve_auth_headers

    monkeypatch.setenv("NAVER_CLIENT_ID", "id-value")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret-value")
    headers = resolve_auth_headers(naver_source)
    assert headers == {"X-Naver-Client-Id": "id-value", "X-Naver-Client-Secret": "secret-value"}


def test_missing_auth_env_gives_actionable_error(naver_source, monkeypatch):
    from news_cli.collectors.base import resolve_auth_headers

    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)
    with pytest.raises(FetchError) as excinfo:
        resolve_auth_headers(naver_source)
    message = str(excinfo.value)
    assert "NAVER_CLIENT_ID" in message and "NAVER_CLIENT_SECRET" in message


def test_collect_sends_auth_headers(naver_source, sample_naver_xml, monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id-value")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret-value")

    captured = {}

    class CapturingSession(FakeSession):
        def get(self, url, timeout=None, headers=None, **kwargs):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse(sample_naver_xml)

    fetcher = HttpFetcher(
        user_agent="t", timeout=5, delay=0, respect_robots=False, session=CapturingSession({})
    )
    result = rss_collect(naver_source, fetcher, limit=3)

    assert captured["headers"]["X-Naver-Client-Id"] == "id-value"
    assert "query=AI" in captured["url"]
    assert len(result.items) == 3


def test_collect_fails_cleanly_without_credentials(naver_source, monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)

    result = rss_collect(naver_source, make_fetcher({}), limit=3)
    assert result.items == []
    assert "NAVER_CLIENT_ID" in result.errors[0][1]


def test_naver_originallink_preferred_over_portal_link(naver_source, sample_naver_xml):
    result = parse_feed(sample_naver_xml, naver_source)
    assert len(result.items) == 3
    # 1·2번째는 언론사 원문 주소, 3번째는 originallink가 없어 포털 주소로 대체
    assert result.items[0].url == "https://www.example-press.co.kr/view/2026081012345"
    assert result.items[1].url.startswith("https://www.example-daily.com/article/98765")
    assert result.items[2].url.startswith("https://n.news.naver.com/")


def test_naver_double_encoded_tags_are_stripped(naver_source, sample_naver_xml):
    from news_cli.cleaner import normalize_text

    item = parse_feed(sample_naver_xml, naver_source).items[0]
    title = normalize_text(item.title)
    assert "<b>" not in title and "&lt;" not in title
    assert title.startswith("국내 기업 AI 반도체 투자 확대")
    assert '"내년 두 배"' in title


def test_naver_items_flow_into_storage(storage, app_config, naver_source, sample_naver_xml, monkeypatch):
    """수집 → raw 저장 → clean까지 네이버 응답이 정상 처리되는지 확인."""
    from news_cli.cleaner import clean_articles

    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    fetcher = make_fetcher({}, default=FakeResponse(sample_naver_xml))

    stats = run_fetch(storage, app_config, source=naver_source, method="rss", limit=3, fetcher=fetcher)
    assert stats.succeeded == 3

    clean_articles(storage, duplicate_policy="skip")
    rows = storage.query_clean_articles()
    assert len(rows) == 3
    assert all("<b>" not in r["title"] for r in rows)
    # 추적 파라미터(utm_source)는 canonical URL에서 제거된다
    assert all("utm_source" not in r["url"] for r in rows)
    assert rows[0]["published_at"].startswith("2026-08-10T00:00:00")


def test_placeholder_credentials_are_rejected(naver_source, monkeypatch):
    """문서의 '<Client ID>' 예시를 그대로 환경변수에 넣은 경우를 잡아낸다."""
    from news_cli.collectors.base import resolve_auth_headers

    monkeypatch.setenv("NAVER_CLIENT_ID", "<Client ID>")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "<Client Secret>")
    with pytest.raises(FetchError) as excinfo:
        resolve_auth_headers(naver_source)
    assert "자리표시자" in str(excinfo.value)


def test_authenticated_api_skips_robots_check(naver_source, sample_naver_xml, monkeypatch):
    """공식 API 호스트가 robots.txt로 크롤러를 막아도 인증 호출은 진행한다."""
    monkeypatch.setenv("NAVER_CLIENT_ID", "abcd1234EFGH5678ijkl")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "AbCdEfGhIj")

    mapping = {
        "https://openapi.naver.test/robots.txt": FakeResponse("User-agent: *\nDisallow: /\n"),
    }
    fetcher = make_fetcher(mapping, default=FakeResponse(sample_naver_xml), respect_robots=True)
    result = rss_collect(naver_source, fetcher, limit=3)

    assert len(result.items) == 3, "인증된 API 호출이 robots.txt로 막히면 안 됩니다"
    assert result.errors == []


def test_crawling_still_honors_robots(web_source, sample_list_html):
    """크롤링 경로에서는 robots.txt 차단이 그대로 적용되어야 한다."""
    mapping = {
        "https://example.test/robots.txt": FakeResponse("User-agent: *\nDisallow: /\n"),
        web_source.list_url: FakeResponse(sample_list_html),
    }
    result = web_collect(web_source, make_fetcher(mapping, respect_robots=True), limit=2)
    assert result.items == []
    assert "robots.txt" in result.errors[0][1]


def test_unauthenticated_rss_still_honors_robots(rss_source, sample_feed_text):
    mapping = {
        "https://example.test/robots.txt": FakeResponse("User-agent: *\nDisallow: /\n"),
        rss_source.url: FakeResponse(sample_feed_text),
    }
    result = rss_collect(rss_source, make_fetcher(mapping, respect_robots=True), limit=2)
    assert result.items == []
    assert "robots.txt" in result.errors[0][1]


def test_http_error_includes_response_body_for_diagnosis():
    """API가 본문에 담아 주는 실패 사유를 오류 메시지에 포함한다."""
    body = '{"errorMessage":"Scope Status Invalid : Authentication failed.","errorCode":"024"}'
    fetcher = make_fetcher({"https://api.test/x": FakeResponse(body, status_code=401)})
    with pytest.raises(FetchError) as excinfo:
        fetcher.get("https://api.test/x")
    message = str(excinfo.value)
    assert "401" in message and "Scope Status Invalid" in message
