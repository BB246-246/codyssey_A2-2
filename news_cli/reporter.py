"""통계·품질 지표·AI 인사이트를 담은 리포트 생성.

데이터가 0건이어도 ZeroDivisionError 없이 0%와 안내 문구를 출력한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .analyzer import analysis_row_to_result
from .charts import generate_default_charts
from .models import utc_now_iso
from .storage import Storage

logger = logging.getLogger(__name__)

DEFAULT_REPORT_DIR = Path("output/reports")


def safe_ratio(numerator: int, denominator: int) -> float:
    """0으로 나누기를 방지한 비율(0.0~1.0)."""
    if not denominator:
        return 0.0
    return numerator / denominator


def format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"


@dataclass
class ReportData:
    """리포트에 필요한 모든 수치."""

    generated_at: str
    date_from: str | None
    date_to: str | None
    category: str | None
    top_n: int
    raw_count: int
    clean_count: int
    summarized_count: int
    body_count: int
    clean_rate: float
    body_rate: float
    summary_rate: float
    top_sources: list[tuple[str, int]] = field(default_factory=list)
    top_categories: list[tuple[str, int]] = field(default_factory=list)
    analysis: dict[str, Any] | None = None
    chart_paths: list[str] = field(default_factory=list)


def collect_report_data(
    storage: Storage,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    category: str | None = None,
    top_n: int = 5,
    chart_dir: str | Path = "output/charts",
    make_charts: bool = True,
) -> ReportData:
    """DB에서 리포트 데이터를 모으고 필요한 차트를 만든다."""
    filters = {"date_from": date_from, "date_to": date_to, "category": category}

    raw_count = storage.count_raw_articles()
    clean_rows = storage.query_clean_articles(**filters)
    clean_count = len(clean_rows)
    summarized_count = sum(1 for r in clean_rows if (r.get("summary") or "").strip())
    body_count = sum(1 for r in clean_rows if (r.get("body") or "").strip())

    category_counts = storage.category_counts(**filters)
    source_counts = storage.source_counts(**filters)
    daily_counts = storage.daily_counts(**filters)

    chart_paths: list[str] = []
    if make_charts:
        chart_paths = [str(p) for p in generate_default_charts(category_counts, daily_counts, chart_dir)]

    analysis = storage.latest_analysis_run(date_from=date_from, date_to=date_to, category=category)

    return ReportData(
        generated_at=utc_now_iso(),
        date_from=date_from,
        date_to=date_to,
        category=category,
        top_n=top_n,
        raw_count=raw_count,
        clean_count=clean_count,
        summarized_count=summarized_count,
        body_count=body_count,
        clean_rate=safe_ratio(clean_count, raw_count),
        body_rate=safe_ratio(body_count, clean_count),
        summary_rate=safe_ratio(summarized_count, clean_count),
        top_sources=source_counts[:top_n],
        top_categories=category_counts[:top_n],
        analysis=analysis,
        chart_paths=chart_paths,
    )


def _bullet_lines(items: Sequence[str], marker: str = "-", empty: str = "(없음)") -> list[str]:
    if not items:
        return [f"{marker} {empty}"]
    return [f"{marker} {item}" for item in items]


def _rank_lines(pairs: Sequence[tuple[str, int]], marker: str = "-") -> list[str]:
    if not pairs:
        return [f"{marker} (데이터 없음)"]
    return [f"{marker} {i}. {name}: {count}건" for i, (name, count) in enumerate(pairs, start=1)]


def render_report(data: ReportData, fmt: str = "md") -> str:
    """ReportData를 txt 또는 md 문자열로 렌더링한다."""
    if fmt not in ("txt", "md"):
        raise ValueError(f"지원하지 않는 리포트 형식: {fmt!r} (txt 또는 md)")

    is_md = fmt == "md"
    h1 = "# " if is_md else ""
    h2 = "## " if is_md else ""
    rule = "" if is_md else "-" * 60

    period = f"{data.date_from or '전체'} ~ {data.date_to or '전체'}"
    lines: list[str] = []

    lines.append(f"{h1}AI 뉴스 트렌드 분석 리포트")
    lines.append("")
    lines.append(f"- 생성 시각(UTC): {data.generated_at}")
    lines.append(f"- 분석 기간: {period}")
    lines.append(f"- 카테고리 필터: {data.category or '전체'}")
    lines.append(f"- TOP N: {data.top_n}")
    lines.append("")
    if rule:
        lines.append(rule)

    lines.append(f"{h2}1. 수집 현황")
    lines.append("")
    lines.append(f"- raw 기사 수: {data.raw_count}건")
    lines.append(f"- clean 기사 수: {data.clean_count}건")
    lines.append(f"- 요약 완료 수: {data.summarized_count}건")
    lines.append("")
    if rule:
        lines.append(rule)

    lines.append(f"{h2}2. 품질 지표")
    lines.append("")
    if is_md:
        lines.append("| 지표 | 계산식 | 값 |")
        lines.append("| --- | --- | --- |")
        lines.append(f"| 정제 성공률 | clean/raw = {data.clean_count}/{data.raw_count} | {format_percent(data.clean_rate)} |")
        lines.append(f"| 본문 보유율 | body 보유 clean/clean = {data.body_count}/{data.clean_count} | {format_percent(data.body_rate)} |")
        lines.append(f"| 요약 완료율 | summarized/clean = {data.summarized_count}/{data.clean_count} | {format_percent(data.summary_rate)} |")
    else:
        lines.append(f"- 정제 성공률 (clean/raw = {data.clean_count}/{data.raw_count}): {format_percent(data.clean_rate)}")
        lines.append(f"- 본문 보유율 (body 보유 clean/clean = {data.body_count}/{data.clean_count}): {format_percent(data.body_rate)}")
        lines.append(f"- 요약 완료율 (summarized/clean = {data.summarized_count}/{data.clean_count}): {format_percent(data.summary_rate)}")
    if data.raw_count == 0 or data.clean_count == 0:
        lines.append("")
        lines.append("> 데이터가 부족합니다. 'fetch'와 'clean'을 먼저 실행하세요.")
    lines.append("")
    if rule:
        lines.append(rule)

    lines.append(f"{h2}3. TOP {data.top_n} 집계")
    lines.append("")
    lines.append("출처별 뉴스 수" + (":" if not is_md else ""))
    lines.extend(_rank_lines(data.top_sources))
    lines.append("")
    lines.append("카테고리별 뉴스 수" + (":" if not is_md else ""))
    lines.extend(_rank_lines(data.top_categories))
    lines.append("")
    if rule:
        lines.append(rule)

    lines.append(f"{h2}4. AI 인사이트")
    lines.append("")
    if data.analysis:
        result = analysis_row_to_result(data.analysis)
        lines.append(
            f"- 분석 실행 id: {data.analysis.get('id')} | 대상 기사 {data.analysis.get('article_count')}건 "
            f"| 모델: {data.analysis.get('model') or '-'} | 생성: {data.analysis.get('created_at')}"
        )
        lines.append("")
        lines.append("주요 트렌드" + (":" if not is_md else ""))
        lines.extend(_bullet_lines(result.trends))
        lines.append("")
        lines.append("핵심 키워드" + (":" if not is_md else ""))
        lines.append("- " + (", ".join(result.keywords) if result.keywords else "(없음)"))
        lines.append("")
        lines.append("공통점/차이점" + (":" if not is_md else ""))
        lines.extend(_bullet_lines(result.commonalities_differences))
        lines.append("")
        lines.append("시사점" + (":" if not is_md else ""))
        lines.extend(_bullet_lines(result.implications))
    else:
        lines.append("- 저장된 AI 분석 결과가 없습니다. 'analyze' 명령을 먼저 실행하세요.")
    lines.append("")
    if rule:
        lines.append(rule)

    lines.append(f"{h2}5. 생성된 차트")
    lines.append("")
    if data.chart_paths:
        for path in data.chart_paths:
            posix = Path(path).as_posix()
            lines.append(f"- ![{Path(path).stem}]({posix})" if is_md else f"- {path}")
    else:
        lines.append("- (차트가 생성되지 않았습니다)")
    lines.append("")

    return "\n".join(lines)


def default_report_path(fmt: str, directory: str | Path = DEFAULT_REPORT_DIR) -> Path:
    stamp = utc_now_iso().replace(":", "").replace("-", "")[:15]
    return Path(directory) / f"report_{stamp}.{fmt}"


def write_report(content: str, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    logger.info("리포트 저장: %s (%d자)", target, len(content))
    return target


def build_report(
    storage: Storage,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    category: str | None = None,
    fmt: str = "md",
    top_n: int = 5,
    output: str | Path | None = None,
    chart_dir: str | Path = "output/charts",
    make_charts: bool = True,
) -> tuple[str, Path]:
    """리포트를 만들고 파일로 저장한다. (본문, 저장 경로)를 돌려준다."""
    data = collect_report_data(
        storage,
        date_from=date_from,
        date_to=date_to,
        category=category,
        top_n=top_n,
        chart_dir=chart_dir,
        make_charts=make_charts,
    )
    content = render_report(data, fmt)
    target = Path(output) if output else default_report_path(fmt)
    write_report(content, target)
    return content, target