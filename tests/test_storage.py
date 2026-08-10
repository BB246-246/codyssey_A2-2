"""SQLite 저장소 테스트."""

from __future__ import annotations

import json

import pytest

from news_cli.models import CleanArticle, FetchStats, RawArticle, AnalysisResult
from news_cli.storage import Storage, StorageError
from tests.conftest import make_raw


def _raw(url: str = "https://example.test/a", source: str = "s1") -> RawArticle:
    return RawArticle(
        source_name=source,
        collection_method="rss",
        canonical_url=url,
        raw_payload=json.dumps({"title": "제목", "url": url}, ensure_ascii=False),
    )


def test_schema_created_on_enter(storage):
    tables = {
        row["name"]
        for row in storage.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"raw_articles", "clean_articles", "analysis_runs", "fetch_runs"}.issubset(tables)


def test_raw_duplicate_is_skipped(storage):
    first, first_id = storage.save_raw_article(_raw())
    second, second_id = storage.save_raw_article(_raw())
    assert first == "inserted"
    assert second == "duplicate"
    assert first_id == second_id
    assert storage.count_raw_articles() == 1


def test_same_url_different_source_is_not_duplicate(storage):
    storage.save_raw_article(_raw(source="s1"))
    outcome, _ = storage.save_raw_article(_raw(source="s2"))
    assert outcome == "inserted"
    assert storage.count_raw_articles() == 2


def test_data_persists_after_close_and_reconnect(tmp_path):
    db_path = tmp_path / "persist.db"
    with Storage(db_path) as store:
        make_raw(store, title="지속성 테스트", url="https://example.test/persist")
        assert store.count_raw_articles() == 1

    with Storage(db_path) as store:
        assert store.count_raw_articles() == 1
        rows = store.list_raw_articles()
        assert json.loads(rows[0]["raw_payload"])["title"] == "지속성 테스트"


def _clean(raw_id: int, url: str = "https://example.test/c", title: str = "제목") -> CleanArticle:
    return CleanArticle(
        raw_id=raw_id,
        title=title,
        url=url,
        source="s1",
        category="IT",
        collected_at="2026-08-05T00:00:00+00:00",
        body="본문",
        clean_status="ok",
    )


def test_clean_skip_policy_keeps_original(storage):
    raw_id = make_raw(storage)
    assert storage.save_clean_article(_clean(raw_id, title="원본"), duplicate_policy="skip") == "inserted"
    assert storage.save_clean_article(_clean(raw_id, title="변경"), duplicate_policy="skip") == "skipped"

    rows = storage.query_clean_articles()
    assert len(rows) == 1
    assert rows[0]["title"] == "원본"


def test_clean_upsert_policy_updates(storage):
    raw_id = make_raw(storage)
    storage.save_clean_article(_clean(raw_id, title="원본"), duplicate_policy="upsert")
    assert storage.save_clean_article(_clean(raw_id, title="변경"), duplicate_policy="upsert") == "updated"

    rows = storage.query_clean_articles()
    assert len(rows) == 1
    assert rows[0]["title"] == "변경"


def test_clean_url_conflict_is_handled(storage):
    raw_a = make_raw(storage, url="https://example.test/a")
    raw_b = make_raw(storage, url="https://example.test/b")
    storage.save_clean_article(_clean(raw_a, url="https://example.test/same"))
    outcome = storage.save_clean_article(
        _clean(raw_b, url="https://example.test/same"), duplicate_policy="skip"
    )
    assert outcome == "skipped"
    assert len(storage.query_clean_articles()) == 1


def test_update_summary_and_status_filters(storage):
    raw_a = make_raw(storage, url="https://example.test/1")
    raw_b = make_raw(storage, url="https://example.test/2")
    storage.save_clean_article(_clean(raw_a, url="https://example.test/1"))
    storage.save_clean_article(_clean(raw_b, url="https://example.test/2"))

    target = storage.query_clean_articles()[0]
    storage.update_summary(int(target["id"]), summary="요약문", model="m1", original_length=42)

    assert len(storage.query_clean_articles(status="summarized")) == 1
    assert len(storage.query_clean_articles(status="unsummarized")) == 1
    assert len(storage.query_clean_articles(status="all")) == 2

    updated = storage.get_clean_article(int(target["id"]))
    assert updated["summary"] == "요약문"
    assert updated["summary_model"] == "m1"
    assert updated["summary_length"] == len("요약문")
    assert updated["original_length"] == 42
    assert updated["summarized_at"]


def test_unknown_status_filter_raises(storage):
    with pytest.raises(StorageError):
        storage.query_clean_articles(status="weird")


def test_date_and_category_filters(storage):
    raw_a = make_raw(storage, url="https://example.test/old")
    raw_b = make_raw(storage, url="https://example.test/new")

    old = _clean(raw_a, url="https://example.test/old")
    old.published_at = "2026-07-01T00:00:00+00:00"
    old.category = "IT"
    new = _clean(raw_b, url="https://example.test/new")
    new.published_at = "2026-08-09T00:00:00+00:00"
    new.category = "AI"
    storage.save_clean_article(old)
    storage.save_clean_article(new)

    assert len(storage.query_clean_articles(date_from="2026-08-01T00:00:00+00:00")) == 1
    assert len(storage.query_clean_articles(date_to="2026-07-31T23:59:59+00:00")) == 1
    assert len(storage.query_clean_articles(category="AI")) == 1
    assert len(storage.query_clean_articles(keyword="제목")) == 2


def test_aggregations(storage):
    for i in range(3):
        raw_id = make_raw(storage, url=f"https://example.test/{i}")
        article = _clean(raw_id, url=f"https://example.test/{i}")
        article.category = "AI" if i < 2 else "IT"
        article.collected_at = f"2026-08-0{i + 1}T00:00:00+00:00"
        storage.save_clean_article(article)

    assert storage.category_counts() == [("AI", 2), ("IT", 1)]
    assert storage.source_counts() == [("s1", 3)]
    assert len(storage.daily_counts()) == 3


def test_analysis_run_roundtrip(storage):
    result = AnalysisResult(
        trends=["트렌드1"], keywords=["키워드"], implications=["시사점"],
        commonalities_differences=["공통점"],
    )
    run_id = storage.save_analysis_run(
        result, article_count=5, date_from="2026-08-01", date_to="2026-08-10",
        category="IT", model="test-model",
    )
    assert run_id > 0

    row = storage.latest_analysis_run(date_from="2026-08-01", date_to="2026-08-10", category="IT")
    assert row is not None
    assert row["article_count"] == 5
    assert json.loads(row["trends_json"]) == ["트렌드1"]

    # 조건이 맞지 않으면 가장 최근 분석으로 fallback
    fallback = storage.latest_analysis_run(category="없는카테고리")
    assert fallback is not None and fallback["id"] == run_id


def test_fetch_run_saved(storage):
    stats = FetchStats(source_name="s1", collection_method="rss", attempted=3, succeeded=2, failed=1)
    run_id = storage.save_fetch_run(stats)
    assert run_id > 0
    runs = storage.list_fetch_runs()
    assert runs[0]["success_count"] == 2
    assert runs[0]["finished_at"]


def test_connection_closes_cleanly(tmp_path):
    store = Storage(tmp_path / "c.db")
    store.connect()
    store.init_schema()
    store.close()
    assert store._conn is None
    store.close()  # 두 번 닫아도 안전해야 한다


def test_pagination_offset(storage):
    for i in range(5):
        raw_id = make_raw(storage, url=f"https://example.test/p{i}")
        storage.save_clean_article(_clean(raw_id, url=f"https://example.test/p{i}", title=f"제목{i}"))

    page1 = storage.query_clean_articles(limit=2, offset=0)
    page2 = storage.query_clean_articles(limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 2
    assert {r["id"] for r in page1}.isdisjoint({r["id"] for r in page2})