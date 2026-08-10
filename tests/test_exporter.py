"""내보내기 round-trip 테스트."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from news_cli.ai_client import FakeAIClient
from news_cli.cleaner import clean_articles
from news_cli.exporter import ExportError, export_articles, export_rows
from news_cli.summarizer import summarize_articles
from tests.conftest import make_raw

KOREAN_TITLE = "한글 제목: AI 반도체 투자 확대 🇰🇷"
KOREAN_BODY = "본문에도 한글과 특수문자 “인용부호”가 들어 있다."


@pytest.fixture
def seeded(storage):
    make_raw(storage, url="https://example.test/1", title=KOREAN_TITLE, body=KOREAN_BODY, category="AI")
    make_raw(storage, url="https://example.test/2", title="요약 없는 기사", body="본문2", category="IT")
    clean_articles(storage, duplicate_policy="skip")

    target = storage.query_clean_articles()[0]
    storage.update_summary(int(target["id"]), summary="한국어 요약문입니다.", model="m", original_length=10)
    return storage


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def test_csv_roundtrip_preserves_korean(seeded, tmp_path):
    path, count = export_articles(seeded, fmt="csv", output=tmp_path / "news.csv")
    assert count == 2
    assert path.exists()

    # utf-8-sig BOM 확인 (Excel 한글 호환)
    assert path.read_bytes()[:3] == b"\xef\xbb\xbf"

    frame = pd.read_csv(path, encoding="utf-8-sig")
    assert len(frame) == 2
    assert KOREAN_TITLE in set(frame["title"])
    row = frame[frame["title"] == KOREAN_TITLE].iloc[0]
    assert row["category"] == "AI"
    assert KOREAN_BODY in row["body"]


# ---------------------------------------------------------------------------
# JSONL
# ---------------------------------------------------------------------------
def test_jsonl_roundtrip(seeded, tmp_path):
    path, count = export_articles(seeded, fmt="jsonl", output=tmp_path / "news.jsonl")
    assert count == 2

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]  # 각 줄이 유효한 JSON 객체
    assert all(isinstance(r, dict) for r in records)

    titles = {r["title"] for r in records}
    assert KOREAN_TITLE in titles

    # ensure_ascii=False 로 한글이 이스케이프되지 않아야 한다
    assert "한글 제목" in path.read_text(encoding="utf-8")
    assert "\\u" not in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------
def test_xlsx_roundtrip(seeded, tmp_path):
    path, count = export_articles(seeded, fmt="xlsx", output=tmp_path / "news.xlsx")
    assert count == 2
    assert path.exists() and path.stat().st_size > 0

    frame = pd.read_excel(path, engine="openpyxl")
    assert len(frame) == 2
    assert KOREAN_TITLE in set(frame["title"])


# ---------------------------------------------------------------------------
# 필터
# ---------------------------------------------------------------------------
def test_status_summarized_filter(seeded, tmp_path):
    path, count = export_articles(
        seeded, fmt="jsonl", status="summarized", output=tmp_path / "summarized.jsonl"
    )
    assert count == 1
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["title"] == KOREAN_TITLE
    assert records[0]["summary"] == "한국어 요약문입니다."


def test_status_unsummarized_filter(seeded, tmp_path):
    path, count = export_articles(
        seeded, fmt="jsonl", status="unsummarized", output=tmp_path / "un.jsonl"
    )
    assert count == 1
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert record["title"] == "요약 없는 기사"
    assert record["summary"] is None


def test_category_filter(seeded, tmp_path):
    _, count = export_articles(seeded, fmt="csv", category="AI", output=tmp_path / "ai.csv")
    assert count == 1


def test_empty_export_creates_file(storage, tmp_path):
    path, count = export_articles(storage, fmt="csv", output=tmp_path / "empty.csv")
    assert count == 0
    assert path.exists()
    frame = pd.read_csv(path, encoding="utf-8-sig")
    assert len(frame) == 0
    assert "title" in frame.columns


def test_unsupported_format_raises(seeded, tmp_path):
    with pytest.raises(ExportError):
        export_rows([], fmt="parquet", output=tmp_path / "x.parquet")


def test_unsupported_status_raises(seeded):
    with pytest.raises(ExportError):
        export_articles(seeded, fmt="csv", status="weird")


def test_default_output_path_used(seeded, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path, count = export_articles(seeded, fmt="csv")
    assert count == 2
    assert path.parts[-2] == "exports"
    assert path.exists()


def test_all_three_formats_in_one_run(seeded, tmp_path):
    for fmt in ("csv", "jsonl", "xlsx"):
        path, count = export_articles(seeded, fmt=fmt, output=tmp_path / f"all.{fmt}")
        assert count == 2, fmt
        assert path.exists(), fmt