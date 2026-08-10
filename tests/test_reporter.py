"""리포트 생성 테스트."""

from __future__ import annotations

import pytest

from news_cli.ai_client import FakeAIClient
from news_cli.analyzer import analyze
from news_cli.cleaner import clean_articles
from news_cli.reporter import (
    build_report,
    collect_report_data,
    format_percent,
    render_report,
    safe_ratio,
)
from news_cli.summarizer import summarize_articles
from tests.conftest import make_raw
from tests.test_analyzer import VALID_RESPONSE


@pytest.fixture
def seeded(storage):
    for i in range(4):
        make_raw(
            storage,
            url=f"https://example.test/{i}",
            title=f"기사 {i}",
            body=f"본문 {i}" if i < 3 else None,
            category="IT" if i < 2 else "AI",
            published_at=f"2026-08-0{i + 1}T00:00:00+00:00",
        )
    clean_articles(storage, duplicate_policy="skip")
    summarize_articles(storage, FakeAIClient(["요약"] * 2), mode="unsummarized", limit=2)
    analyze(storage, FakeAIClient([VALID_RESPONSE]))
    return storage


# ---------------------------------------------------------------------------
# 0 나누기 방지
# ---------------------------------------------------------------------------
def test_safe_ratio_handles_zero_denominator():
    assert safe_ratio(0, 0) == 0.0
    assert safe_ratio(5, 0) == 0.0
    assert safe_ratio(1, 4) == 0.25


def test_format_percent():
    assert format_percent(0.0) == "0.0%"
    assert format_percent(0.25) == "25.0%"


def test_empty_database_report_has_no_zero_division(storage, tmp_path):
    content, path = build_report(storage, fmt="md", chart_dir=tmp_path / "charts")
    assert "0.0%" in content
    assert "데이터가 부족합니다" in content
    assert path.exists()


# ---------------------------------------------------------------------------
# 지표
# ---------------------------------------------------------------------------
def test_quality_metrics(seeded, tmp_path):
    data = collect_report_data(seeded, chart_dir=tmp_path / "charts")
    assert data.raw_count == 4
    assert data.clean_count == 4
    assert data.body_count == 3
    assert data.summarized_count == 2
    assert data.clean_rate == 1.0
    assert data.body_rate == 0.75
    assert data.summary_rate == 0.5


def test_report_contains_required_sections(seeded, tmp_path):
    data = collect_report_data(seeded, top_n=3, chart_dir=tmp_path / "charts")
    content = render_report(data, "md")

    assert "품질 지표" in content
    assert "정제 성공률" in content
    assert "본문 보유율" in content
    assert "요약 완료율" in content
    assert "TOP 3" in content
    assert "AI 인사이트" in content
    assert "생성된 차트" in content
    assert "생성 시각" in content


def test_report_includes_chart_paths(seeded, tmp_path):
    chart_dir = tmp_path / "charts"
    data = collect_report_data(seeded, chart_dir=chart_dir)
    assert len(data.chart_paths) == 2
    for path in data.chart_paths:
        from pathlib import Path

        assert Path(path).exists(), f"차트 파일이 없습니다: {path}"
        assert Path(path).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    content = render_report(data, "md")
    assert "category_counts.png" in content
    assert "daily_collection_trend.png" in content


def test_report_includes_ai_insight(seeded, tmp_path):
    data = collect_report_data(seeded, chart_dir=tmp_path / "charts")
    content = render_report(data, "md")
    assert "생성형 AI 확산" in content
    assert "국내 기업의 대응 필요" in content


def test_top_n_limits_rows(seeded, tmp_path):
    data = collect_report_data(seeded, top_n=1, chart_dir=tmp_path / "charts")
    assert len(data.top_categories) == 1
    assert len(data.top_sources) == 1


def test_txt_and_md_formats(seeded, tmp_path):
    data = collect_report_data(seeded, chart_dir=tmp_path / "charts")
    md = render_report(data, "md")
    txt = render_report(data, "txt")
    assert md.startswith("# ")
    assert not txt.startswith("# ")
    assert "|" in md  # 마크다운 표
    assert "-" * 60 in txt


def test_unsupported_format_raises(seeded, tmp_path):
    data = collect_report_data(seeded, chart_dir=tmp_path / "charts", make_charts=False)
    with pytest.raises(ValueError):
        render_report(data, "pdf")


def test_build_report_writes_file(seeded, tmp_path):
    output = tmp_path / "reports" / "custom.md"
    content, path = build_report(
        seeded, fmt="md", output=output, chart_dir=tmp_path / "charts", top_n=2
    )
    assert path == output
    assert output.read_text(encoding="utf-8") == content
    assert "AI 뉴스 트렌드 분석 리포트" in content


def test_report_respects_category_filter(seeded, tmp_path):
    data = collect_report_data(seeded, category="AI", chart_dir=tmp_path / "charts")
    assert data.clean_count == 2
    assert all(name == "AI" for name, _ in data.top_categories)