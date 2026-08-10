"""matplotlib 차트 생성.

- headless 환경을 위해 Agg backend를 강제한다.
- 한글 폰트를 탐색하고, 없으면 경고 후 기본 폰트로 fallback한다.
- 데이터가 없어도 예외 없이 안내 문구가 있는 차트를 생성한다.
- 저장 후 반드시 figure를 close한다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")  # noqa: E402 - import 순서보다 backend 지정이 먼저다

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_CHART_DIR = Path("output/charts")
CATEGORY_CHART_NAME = "category_counts.png"
DAILY_CHART_NAME = "daily_collection_trend.png"

# 플랫폼별 대표적인 한글 폰트 후보
KOREAN_FONT_CANDIDATES = (
    "Malgun Gothic",
    "NanumGothic",
    "NanumBarunGothic",
    "Noto Sans KR",
    "Noto Sans CJK KR",
    "AppleGothic",
    "Gulim",
    "Batang",
    "UnDotum",
)

_font_configured = False
_active_font: str | None = None


def configure_korean_font(force: bool = False) -> str | None:
    """사용 가능한 한글 폰트를 찾아 matplotlib에 설정한다.

    Returns: 적용된 폰트 이름. 없으면 None(기본 폰트 fallback).
    """
    global _font_configured, _active_font
    if _font_configured and not force:
        return _active_font

    available = {f.name for f in font_manager.fontManager.ttflist}
    chosen = next((name for name in KOREAN_FONT_CANDIDATES if name in available), None)

    if chosen:
        plt.rcParams["font.family"] = chosen
        logger.info("차트 한글 폰트 적용: %s", chosen)
    else:
        logger.warning(
            "한글 폰트를 찾지 못했습니다. 기본 폰트로 대체하며 한글이 깨질 수 있습니다. "
            "설치 권장: %s",
            ", ".join(KOREAN_FONT_CANDIDATES[:3]),
        )

    plt.rcParams["axes.unicode_minus"] = False
    _font_configured = True
    _active_font = chosen
    return chosen


def _prepare_path(path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _empty_chart(path: Path, title: str, message: str = "표시할 데이터가 없습니다") -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    try:
        ax.set_title(title)
        ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=14, transform=ax.transAxes)
        ax.set_xticks([])
        ax.set_yticks([])
        fig.tight_layout()
        fig.savefig(path, dpi=120)
    finally:
        plt.close(fig)
    logger.warning("데이터가 없어 빈 차트를 생성했습니다: %s", path)
    return path


def create_category_chart(
    counts: Sequence[tuple[str, int]],
    path: str | Path = DEFAULT_CHART_DIR / CATEGORY_CHART_NAME,
    *,
    title: str = "카테고리별 뉴스 수",
) -> Path:
    """카테고리별 뉴스 수 막대그래프."""
    configure_korean_font()
    target = _prepare_path(path)

    if not counts:
        return _empty_chart(target, title)

    labels = [str(label) for label, _ in counts]
    values = [int(value) for _, value in counts]

    fig, ax = plt.subplots(figsize=(max(8, len(labels) * 1.1), 4.8))
    try:
        bars = ax.bar(labels, values, color="#4C78A8")
        ax.set_title(title)
        ax.set_xlabel("카테고리")
        ax.set_ylabel("기사 수")
        ax.set_ylim(0, max(values) * 1.18 if values else 1)
        ax.grid(axis="y", linestyle="--", alpha=0.35)
        for bar, value in zip(bars, values):
            ax.annotate(
                str(value),
                xy=(bar.get_x() + bar.get_width() / 2, value),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=9,
            )
        if len(labels) > 6:
            plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
        fig.tight_layout()
        fig.savefig(target, dpi=120)
    finally:
        plt.close(fig)

    logger.info("차트 저장: %s", target)
    return target


def create_daily_trend_chart(
    counts: Sequence[tuple[str, int]],
    path: str | Path = DEFAULT_CHART_DIR / DAILY_CHART_NAME,
    *,
    title: str = "일자별 수집 추이",
) -> Path:
    """일자별 수집 건수 선그래프."""
    configure_korean_font()
    target = _prepare_path(path)

    if not counts:
        return _empty_chart(target, title)

    days = [str(day) for day, _ in counts]
    values = [int(value) for _, value in counts]

    fig, ax = plt.subplots(figsize=(max(8, len(days) * 0.8), 4.8))
    try:
        ax.plot(days, values, marker="o", color="#E45756", linewidth=2)
        ax.set_title(title)
        ax.set_xlabel("일자")
        ax.set_ylabel("수집 기사 수")
        ax.set_ylim(0, max(values) * 1.2 if values else 1)
        ax.grid(True, linestyle="--", alpha=0.35)
        for day, value in zip(days, values):
            ax.annotate(
                str(value),
                xy=(day, value),
                xytext=(0, 6),
                textcoords="offset points",
                ha="center",
                fontsize=9,
            )
        if len(days) > 8:
            plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
        fig.tight_layout()
        fig.savefig(target, dpi=120)
    finally:
        plt.close(fig)

    logger.info("차트 저장: %s", target)
    return target


def generate_default_charts(
    category_counts: Sequence[tuple[str, int]],
    daily_counts: Sequence[tuple[str, int]],
    output_dir: str | Path = DEFAULT_CHART_DIR,
) -> list[Path]:
    """필수 차트 2개를 생성하고 경로 목록을 돌려준다."""
    directory = Path(output_dir)
    return [
        create_category_chart(category_counts, directory / CATEGORY_CHART_NAME),
        create_daily_trend_chart(daily_counts, directory / DAILY_CHART_NAME),
    ]