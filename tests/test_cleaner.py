"""정제 로직 테스트."""

from __future__ import annotations

import json

import pytest

from news_cli.cleaner import (
    CleanError,
    build_clean_article,
    clean_articles,
    content_hash,
    normalize_date,
    normalize_text,
    normalize_url,
)
from news_cli.models import CLEAN_STATUS_NO_BODY, CLEAN_STATUS_OK, RawArticle, utc_now_iso
from tests.conftest import make_raw


# ---------------------------------------------------------------------------
# 텍스트 정규화
# ---------------------------------------------------------------------------
def test_normalize_text_strips_tags_and_entities():
    raw = "<p>안녕&nbsp;<b>하세요</b> &amp; 반갑습니다</p><script>evil()</script>"
    result = normalize_text(raw)
    assert "<" not in result and ">" not in result
    assert "evil" not in result
    assert "안녕 하세요 & 반갑습니다" in result


def test_normalize_text_collapses_whitespace_and_newlines():
    raw = "첫째   줄\r\n\r\n\r\n\r\n둘째\t줄   "
    result = normalize_text(raw)
    assert result == "첫째 줄\n\n둘째 줄"


def test_normalize_text_applies_nfkc():
    assert normalize_text("ＡＢＣ１２３") == "ABC123"


def test_normalize_text_handles_none_and_empty():
    assert normalize_text(None) == ""
    assert normalize_text("") == ""


# ---------------------------------------------------------------------------
# URL 정규화
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://Example.test/News/1#section", "https://example.test/News/1"),
        ("https://example.test/a?utm_source=x&id=7", "https://example.test/a?id=7"),
        ("https://example.test/a?fbclid=abc", "https://example.test/a"),
        ("https://example.test/a/", "https://example.test/a"),
        ("https://example.test:443/a", "https://example.test/a"),
        ("https://example.test/a?b=2&a=1", "https://example.test/a?a=1&b=2"),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_normalize_url_empty():
    assert normalize_url(None) == ""
    assert normalize_url("   ") == ""


# ---------------------------------------------------------------------------
# 날짜 정규화
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,prefix",
    [
        ("2026-08-05T09:30:00+00:00", "2026-08-05T09:30:00"),
        ("Mon, 04 Aug 2026 09:30:00 +0000", "2026-08-04T09:30:00"),
        ("2026-08-05", "2026-08-05T00:00:00"),
        ("Thursday, August 6, 2026", "2026-08-06T00:00:00"),
        ("2026-08-05T09:30:00Z", "2026-08-05T09:30:00"),
    ],
)
def test_normalize_date_formats(raw, prefix):
    assert normalize_date(raw).startswith(prefix)


def test_normalize_date_converts_to_utc():
    assert normalize_date("2026-08-05T18:30:00+09:00").startswith("2026-08-05T09:30:00")


def test_normalize_date_unparseable_returns_none():
    assert normalize_date("언젠가 그날") is None
    assert normalize_date(None) is None


def test_normalize_date_accepts_struct_time_tuple():
    assert normalize_date((2026, 8, 5, 9, 30, 0, 0, 0, 0)).startswith("2026-08-05T09:30:00")


# ---------------------------------------------------------------------------
# content hash
# ---------------------------------------------------------------------------
def test_content_hash_is_stable_and_whitespace_insensitive():
    a = content_hash("제목", "본문   내용")
    b = content_hash("제목", "본문 내용")
    assert a == b
    assert len(a) == 64
    assert a != content_hash("다른 제목", "본문 내용")


# ---------------------------------------------------------------------------
# build_clean_article
# ---------------------------------------------------------------------------
def _raw_row(payload: dict, raw_id: int = 1) -> dict:
    return {
        "id": raw_id,
        "source_name": "test_rss",
        "collected_at": "2026-08-05T00:00:00+00:00",
        "canonical_url": payload.get("url", ""),
        "raw_payload": json.dumps(payload, ensure_ascii=False),
    }


def test_build_clean_article_success():
    article = build_clean_article(
        _raw_row(
            {
                "title": "  <b>제목</b>  ",
                "url": "https://example.test/a?utm_source=x#frag",
                "body": "<p>본문&nbsp;입니다</p>",
                "category": "AI",
                "published_at": "Mon, 04 Aug 2026 09:30:00 +0000",
            }
        )
    )
    assert article.title == "제목"
    assert article.url == "https://example.test/a"
    assert article.body == "본문 입니다"
    assert article.category == "AI"
    assert article.published_at.startswith("2026-08-04T09:30:00")
    assert article.clean_status == CLEAN_STATUS_OK
    assert article.content_hash and len(article.content_hash) == 64


def test_missing_title_raises():
    with pytest.raises(CleanError) as excinfo:
        build_clean_article(_raw_row({"title": "   ", "url": "https://example.test/a"}))
    assert "title" in str(excinfo.value)


def test_missing_url_raises():
    with pytest.raises(CleanError) as excinfo:
        build_clean_article(_raw_row({"title": "제목", "url": ""}))
    assert "url" in str(excinfo.value)


def test_missing_body_is_marked_not_fatal():
    article = build_clean_article(_raw_row({"title": "제목", "url": "https://example.test/a"}))
    assert article.clean_status == CLEAN_STATUS_NO_BODY
    assert article.body is None


def test_missing_category_defaults_to_unknown():
    article = build_clean_article(_raw_row({"title": "제목", "url": "https://example.test/a"}))
    assert article.category == "unknown"


def test_broken_payload_raises():
    row = {"id": 1, "source_name": "s", "collected_at": "x", "canonical_url": "u", "raw_payload": "{"}
    with pytest.raises(CleanError):
        build_clean_article(row)


# ---------------------------------------------------------------------------
# clean_articles 통합
# ---------------------------------------------------------------------------
def test_clean_articles_inserts(storage):
    make_raw(storage, url="https://example.test/1")
    make_raw(storage, url="https://example.test/2")

    stats = clean_articles(storage, duplicate_policy="skip")
    assert stats["inserted"] == 2
    assert len(storage.query_clean_articles()) == 2


def test_clean_is_idempotent_on_rerun(storage):
    make_raw(storage, url="https://example.test/1")
    make_raw(storage, url="https://example.test/2")

    clean_articles(storage, duplicate_policy="skip")
    first_count = len(storage.query_clean_articles())

    second = clean_articles(storage, duplicate_policy="skip")
    third = clean_articles(storage, duplicate_policy="upsert")

    assert second["skipped"] == 2
    assert third["updated"] == 2
    assert len(storage.query_clean_articles()) == first_count == 2


def test_clean_upsert_refreshes_content(storage):
    raw_id = make_raw(storage, url="https://example.test/1", title="원래 제목")
    clean_articles(storage, duplicate_policy="skip")

    # raw payload를 바꾼 뒤 upsert로 재정제
    new_payload = json.dumps(
        {"title": "새 제목", "url": "https://example.test/1", "body": "새 본문"}, ensure_ascii=False
    )
    storage.conn.execute("UPDATE raw_articles SET raw_payload = ? WHERE id = ?", (new_payload, raw_id))
    storage.conn.commit()

    clean_articles(storage, duplicate_policy="upsert")
    rows = storage.query_clean_articles()
    assert len(rows) == 1
    assert rows[0]["title"] == "새 제목"


def test_clean_skips_invalid_and_continues(storage):
    make_raw(storage, url="https://example.test/good")
    bad = RawArticle(
        source_name="test_rss",
        collection_method="rss",
        canonical_url="https://example.test/bad",
        raw_payload=json.dumps({"title": "", "url": ""}, ensure_ascii=False),
        collected_at=utc_now_iso(),
    )
    storage.save_raw_article(bad)

    stats = clean_articles(storage, duplicate_policy="skip")
    assert stats["invalid"] == 1
    assert stats["inserted"] == 1
    assert len(storage.query_clean_articles()) == 1


def test_clean_respects_limit(storage):
    for i in range(5):
        make_raw(storage, url=f"https://example.test/{i}")
    stats = clean_articles(storage, duplicate_policy="skip", limit=2)
    assert stats["processed"] == 2
    assert len(storage.query_clean_articles()) == 2


def test_clean_rejects_bad_policy(storage):
    with pytest.raises(CleanError):
        clean_articles(storage, duplicate_policy="merge")

@pytest.mark.parametrize(
    "raw,prefix",
    [
        ("반도체ㆍ디스플레이 입력 :2026/08/10 00:41    수정: 2026/08/10 09:43", "2026-08-10T00:41:00"),
        ("입력 2026.08.09 20:25", "2026-08-09T20:25:00"),
        ("등록일 2026/08/10", "2026-08-10T00:00:00"),
    ],
)
def test_normalize_date_extracts_from_noisy_text(raw, prefix):
    """언론사 페이지의 '입력 :날짜 수정: 날짜' 형태에서 발행일을 뽑아낸다."""
    assert normalize_date(raw).startswith(prefix)


def test_double_encoded_html_entities_are_fully_stripped():
    """네이버 뉴스 검색 API처럼 태그를 '&lt;b&gt;'로 주는 경우."""
    assert normalize_text("국내 &lt;b&gt;AI&lt;/b&gt; 투자 &amp;quot;확대&amp;quot;") == '국내 AI 투자 "확대"'
