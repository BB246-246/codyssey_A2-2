"""차트 생성 테스트."""

from __future__ import annotations

import logging

import matplotlib.pyplot as plt

from news_cli.charts import (
    CATEGORY_CHART_NAME,
    DAILY_CHART_NAME,
    configure_korean_font,
    create_category_chart,
    create_daily_trend_chart,
    generate_default_charts,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_backend_is_headless():
    import matplotlib

    assert matplotlib.get_backend().lower() == "agg"


def test_generates_two_pngs(tmp_path):
    paths = generate_default_charts(
        [("AI", 5), ("IT", 3)],
        [("2026-08-01", 2), ("2026-08-02", 6)],
        tmp_path,
    )
    assert len(paths) == 2
    assert {p.name for p in paths} == {CATEGORY_CHART_NAME, DAILY_CHART_NAME}
    for path in paths:
        assert path.exists()
        assert path.stat().st_size > 0
        assert path.read_bytes()[:8] == PNG_MAGIC


def test_empty_data_does_not_raise(tmp_path):
    paths = generate_default_charts([], [], tmp_path)
    assert len(paths) == 2
    for path in paths:
        assert path.exists()
        assert path.read_bytes()[:8] == PNG_MAGIC


def test_figures_are_closed(tmp_path):
    plt.close("all")
    create_category_chart([("AI", 1)], tmp_path / "c.png")
    create_daily_trend_chart([("2026-08-01", 1)], tmp_path / "d.png")
    create_category_chart([], tmp_path / "empty.png")
    assert plt.get_fignums() == [], "figure가 닫히지 않아 리소스가 누수됩니다"


def test_output_directory_is_created(tmp_path):
    target = tmp_path / "nested" / "deeper" / "chart.png"
    create_category_chart([("AI", 1)], target)
    assert target.exists()


def test_korean_font_configured_or_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="news_cli.charts"):
        font = configure_korean_font(force=True)
    if font is None:
        assert any("한글 폰트" in r.message for r in caplog.records)
    else:
        assert plt.rcParams["font.family"][0] == font
    assert plt.rcParams["axes.unicode_minus"] is False


def test_many_categories_render(tmp_path):
    counts = [(f"카테고리{i}", i + 1) for i in range(12)]
    path = create_category_chart(counts, tmp_path / "many.png")
    assert path.exists() and path.stat().st_size > 0