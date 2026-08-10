"""argparse 기반 CLI 진입점.

종료 코드
---------
0  성공
1  처리 가능한 오류(설정/네트워크/AI/저장소)
2  잘못된 명령행 사용 (argparse 기본)
3  조건에 맞는 데이터가 없음 (analyze)
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .ai_client import AIConfigError, AIError, build_ai_client, resolve_model
from .analyzer import AnalysisError, NoArticlesError, analysis_row_to_result, analyze
from .cleaner import CleanError, clean_articles
from .collectors import FetchError, run_fetch
from .config import DEFAULT_CONFIG_PATH, AppConfig, ConfigError, load_config
from .exporter import ExportError, export_articles
from .logging_config import setup_logging
from .models import METHOD_CRAWL, METHOD_RSS
from .reporter import build_report
from .storage import Storage, StorageError

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NO_DATA = 3

REQUIRED_COMMANDS = ("fetch", "clean", "summarize", "analyze", "report", "export")


# ---------------------------------------------------------------------------
# argparse 타입/헬퍼
# ---------------------------------------------------------------------------
def positive_int(value: str) -> int:
    """양의 정수만 허용한다 (0과 음수는 거부)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"정수가 필요합니다: {value!r}") from None
    if number <= 0:
        raise argparse.ArgumentTypeError(f"--limit은 1 이상의 정수여야 합니다 (입력값: {value})")
    return number


def iso_date(value: str) -> str:
    """YYYY-MM-DD 형식만 허용한다."""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"날짜는 YYYY-MM-DD 형식이어야 합니다 (입력값: {value!r})") from None
    return value


def date_bounds(date_from: str | None, date_to: str | None) -> tuple[str | None, str | None]:
    """YYYY-MM-DD를 DB 비교용 ISO 8601 경계값으로 바꾼다."""
    lower = (
        datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()
        if date_from
        else None
    )
    upper = (
        datetime.strptime(date_to, "%Y-%m-%d")
        .replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)
        .isoformat()
        if date_to
        else None
    )
    return lower, upper


def _echo(text: str = "") -> None:
    """콘솔 출력. Windows 콘솔 인코딩 문제로 죽지 않도록 방어한다."""
    try:
        print(text)
    except UnicodeEncodeError:  # pragma: no cover - 환경 의존
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def _add_filter_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date-from", type=iso_date, help="시작 날짜 (YYYY-MM-DD)")
    parser.add_argument("--date-to", type=iso_date, help="종료 날짜 (YYYY-MM-DD)")
    parser.add_argument("--category", help="카테고리 필터")


# ---------------------------------------------------------------------------
# 파서 구성
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="AI 뉴스 트렌드 및 종합 분석 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  python main.py fetch --method rss --source default_rss --limit 20\n"
            "  python main.py clean --duplicate-policy skip\n"
            "  python main.py summarize --unsummarized --limit 10\n"
            "  python main.py analyze --date-from 2026-08-01 --date-to 2026-08-10 --category IT\n"
            "  python main.py report --format md --top-n 5\n"
            "  python main.py export --format csv --status summarized\n"
        ),
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="설정 파일 경로 (기본: config.json)")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="로그 레벨 (기본: INFO)",
    )
    parser.add_argument("--version", action="version", version=f"ai-news-cli {__version__}")

    subparsers = parser.add_subparsers(dest="command", metavar="{fetch,clean,summarize,analyze,report,export,list,show}")

    # -- fetch --------------------------------------------------------------
    fetch_p = subparsers.add_parser("fetch", help="RSS 또는 웹 크롤링으로 뉴스를 수집해 raw 저장소에 적재")
    fetch_p.add_argument("--method", required=True, choices=[METHOD_RSS, METHOD_CRAWL], help="수집 방법")
    fetch_p.add_argument("--source", required=True, help="config의 소스 이름")
    fetch_p.add_argument("--limit", type=positive_int, default=20, help="최대 수집 건수 (기본: 20)")

    # -- clean --------------------------------------------------------------
    clean_p = subparsers.add_parser("clean", help="raw 기사를 정제해 clean 저장소에 적재")
    clean_p.add_argument(
        "--duplicate-policy", choices=["skip", "upsert"], help="중복 처리 정책 (기본: config 값)"
    )
    clean_p.add_argument("--limit", type=positive_int, help="처리할 최대 raw 기사 수")

    # -- summarize ----------------------------------------------------------
    sum_p = subparsers.add_parser("summarize", help="기사별 AI 요약 생성")
    target = sum_p.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true", help="모든 clean 기사")
    target.add_argument("--id", type=int, help="특정 clean 기사 id")
    target.add_argument("--unsummarized", action="store_true", help="아직 요약되지 않은 기사만")
    sum_p.add_argument("--limit", type=positive_int, help="최대 요약 건수")
    sum_p.add_argument("--force", action="store_true", help="기존 요약을 덮어쓴다")
    sum_p.add_argument("--model", help="AI 모델 override (기본: AI_MODEL 또는 config)")

    # -- analyze ------------------------------------------------------------
    an_p = subparsers.add_parser("analyze", help="기간/카테고리별 AI 종합 분석")
    _add_filter_args(an_p)
    an_p.add_argument("--limit", type=positive_int, help="분석에 사용할 최대 기사 수")
    an_p.add_argument("--model", help="AI 모델 override")

    # -- report -------------------------------------------------------------
    rep_p = subparsers.add_parser("report", help="통계·품질 지표·AI 인사이트 리포트 생성")
    _add_filter_args(rep_p)
    rep_p.add_argument("--format", choices=["txt", "md"], default="md", help="리포트 형식 (기본: md)")
    rep_p.add_argument("--top-n", type=positive_int, default=5, help="TOP N 집계 개수 (기본: 5)")
    rep_p.add_argument("--output", help="저장 경로 (기본: output/reports/report_*.md)")
    rep_p.add_argument("--no-charts", action="store_true", help="차트 생성을 건너뛴다")

    # -- export -------------------------------------------------------------
    exp_p = subparsers.add_parser("export", help="CSV/JSONL/Excel로 내보내기")
    exp_p.add_argument("--format", required=True, choices=["csv", "jsonl", "xlsx"], help="내보내기 형식")
    exp_p.add_argument(
        "--status", choices=["all", "summarized", "unsummarized"], default="all", help="요약 상태 필터"
    )
    _add_filter_args(exp_p)
    exp_p.add_argument("--output", help="저장 경로 (기본: output/exports/news_*.<ext>)")
    exp_p.add_argument("--limit", type=positive_int, help="최대 행 수")

    # -- list (보너스) --------------------------------------------------------
    list_p = subparsers.add_parser("list", help="[보너스] 저장된 기사 목록 조회")
    _add_filter_args(list_p)
    list_p.add_argument("--keyword", help="제목/본문 키워드 검색")
    list_p.add_argument("--status", choices=["all", "summarized", "unsummarized"], default="all")
    list_p.add_argument("--page", type=positive_int, default=1, help="페이지 번호 (1부터)")
    list_p.add_argument("--page-size", type=positive_int, default=10, help="페이지 크기 (기본: 10)")

    # -- show (보너스) --------------------------------------------------------
    show_p = subparsers.add_parser("show", help="[보너스] 기사 상세 보기")
    show_p.add_argument("--id", type=int, required=True, help="clean 기사 id")

    return parser


# ---------------------------------------------------------------------------
# 명령 구현
# ---------------------------------------------------------------------------
def cmd_fetch(args: argparse.Namespace, config: AppConfig, storage: Storage) -> int:
    source = config.get_source(args.source)
    stats = run_fetch(storage, config, source=source, method=args.method, limit=args.limit)

    _echo(f"[fetch] 소스={stats.source_name} 방법={stats.collection_method}")
    _echo(
        f"  시도 {stats.attempted}건 / 성공 {stats.succeeded}건 / "
        f"실패 {stats.failed}건 / 중복 {stats.duplicates}건"
    )
    if stats.succeeded == 0 and stats.failed > 0:
        _echo("  수집된 신규 기사가 없습니다. 로그에서 오류 원인을 확인하세요.")
    return EXIT_OK


def cmd_clean(args: argparse.Namespace, config: AppConfig, storage: Storage) -> int:
    policy = args.duplicate_policy or config.duplicate_policy
    stats = clean_articles(storage, duplicate_policy=policy, limit=args.limit)

    _echo(f"[clean] 정책={policy}")
    _echo(
        f"  처리 {stats['processed']}건 / 신규 {stats['inserted']}건 / "
        f"갱신 {stats['updated']}건 / skip {stats['skipped']}건 / 무효 {stats['invalid']}건"
    )
    return EXIT_OK


def cmd_summarize(args: argparse.Namespace, config: AppConfig, storage: Storage) -> int:
    mode = "id" if args.id is not None else ("all" if args.all else "unsummarized")
    client = build_ai_client(config, model=args.model)

    stats = summarize_with_client(
        storage,
        client,
        config,
        mode=mode,
        article_id=args.id,
        limit=args.limit,
        force=args.force,
    )
    _echo(f"[summarize] 모드={mode} 모델={getattr(client, 'model', '-')}")
    _echo(
        f"  시도 {stats.attempted}건 / 성공 {stats.succeeded}건 / "
        f"실패 {stats.failed}건 / skip {stats.skipped}건"
    )
    return EXIT_OK


def summarize_with_client(storage, client, config, **kwargs):
    """summarize 실행부 (테스트에서 fake client로 재사용)."""
    from .summarizer import summarize_articles

    return summarize_articles(
        storage, client, max_body_chars=config.max_body_chars_for_ai, **kwargs
    )


def cmd_analyze(args: argparse.Namespace, config: AppConfig, storage: Storage) -> int:
    lower, upper = date_bounds(args.date_from, args.date_to)
    client = build_ai_client(config, model=args.model)

    try:
        run_id, result, used = analyze(
            storage,
            client,
            date_from=lower,
            date_to=upper,
            category=args.category,
            limit=args.limit,
            max_articles=config.max_analysis_articles,
            max_chars=config.max_analysis_chars,
        )
    except NoArticlesError as exc:
        _echo(f"[analyze] {exc}")
        logger.warning("분석 대상 기사가 없습니다.")
        return EXIT_NO_DATA

    # 조회 편의를 위해 사용자가 입력한 원본 날짜 문자열도 기록해 둔다.
    storage.conn.execute(
        "UPDATE analysis_runs SET date_from = ?, date_to = ? WHERE id = ?",
        (args.date_from, args.date_to, run_id),
    )
    storage.conn.commit()

    _echo(f"[analyze] 분석 id={run_id} (기사 {used}건, 모델={getattr(client, 'model', '-')})")
    _echo("  주요 트렌드:")
    for item in result.trends:
        _echo(f"    - {item}")
    _echo("  핵심 키워드: " + ", ".join(result.keywords))
    if result.commonalities_differences:
        _echo("  공통점/차이점:")
        for item in result.commonalities_differences:
            _echo(f"    - {item}")
    _echo("  시사점:")
    for item in result.implications:
        _echo(f"    - {item}")
    return EXIT_OK


def cmd_report(args: argparse.Namespace, config: AppConfig, storage: Storage) -> int:
    lower, upper = date_bounds(args.date_from, args.date_to)
    content, path = build_report(
        storage,
        date_from=lower,
        date_to=upper,
        category=args.category,
        fmt=args.format,
        top_n=args.top_n,
        output=args.output,
        make_charts=not args.no_charts,
    )
    _echo(content)
    _echo(f"[report] 저장 완료: {path}")
    return EXIT_OK


def cmd_export(args: argparse.Namespace, config: AppConfig, storage: Storage) -> int:
    lower, upper = date_bounds(args.date_from, args.date_to)
    path, rows = export_articles(
        storage,
        fmt=args.format,
        status=args.status,
        date_from=lower,
        date_to=upper,
        category=args.category,
        output=args.output,
        limit=args.limit,
    )
    _echo(f"[export] {rows}행을 {args.format}로 내보냈습니다 -> {path}")
    return EXIT_OK


def cmd_list(args: argparse.Namespace, config: AppConfig, storage: Storage) -> int:
    lower, upper = date_bounds(args.date_from, args.date_to)
    filters: dict[str, Any] = {
        "date_from": lower,
        "date_to": upper,
        "category": args.category,
        "keyword": args.keyword,
        "status": args.status,
    }
    total = storage.count_clean_articles(**filters)
    offset = (args.page - 1) * args.page_size
    rows = storage.query_clean_articles(limit=args.page_size, offset=offset, order="DESC", **filters)

    pages = max(1, -(-total // args.page_size))
    _echo(f"[list] 총 {total}건 / {args.page}페이지 (전체 {pages}페이지, 페이지당 {args.page_size}건)")
    if not rows:
        _echo("  표시할 기사가 없습니다.")
        return EXIT_OK
    for row in rows:
        marker = "요약O" if (row.get("summary") or "").strip() else "요약X"
        date = (row.get("published_at") or row.get("collected_at") or "")[:10]
        _echo(f"  #{row['id']:>4} [{marker}] {date} ({row['category']}/{row['source']}) {row['title']}")
    return EXIT_OK


def cmd_show(args: argparse.Namespace, config: AppConfig, storage: Storage) -> int:
    row = storage.get_clean_article(args.id)
    if row is None:
        _echo(f"[show] id={args.id}인 기사를 찾을 수 없습니다.")
        return EXIT_NO_DATA

    _echo(f"제목      : {row['title']}")
    _echo(f"URL       : {row['url']}")
    _echo(f"출처/분류 : {row['source']} / {row['category']}")
    _echo(f"발행/수집 : {row.get('published_at') or '-'} / {row['collected_at']}")
    _echo(f"상태      : {row['clean_status']}")
    _echo(f"모델/요약 : {row.get('summary_model') or '-'} / {row.get('summary_length') or 0}자")
    _echo("")
    _echo("[요약]")
    _echo(row.get("summary") or "(요약 없음)")
    _echo("")
    _echo("[본문]")
    body = row.get("body") or "(본문 없음)"
    _echo(body if len(body) <= 2000 else body[:2000] + "\n...(생략)")
    return EXIT_OK


COMMANDS = {
    "fetch": cmd_fetch,
    "clean": cmd_clean,
    "summarize": cmd_summarize,
    "analyze": cmd_analyze,
    "report": cmd_report,
    "export": cmd_export,
    "list": cmd_list,
    "show": cmd_show,
}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return EXIT_USAGE

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        _echo(f"[설정 오류] {exc}")
        return EXIT_ERROR

    setup_logging(config.log_path, level=args.log_level)
    logger.info("명령 시작: %s (config=%s)", args.command, config.config_path)

    handler = COMMANDS[args.command]
    storage = Storage(config.database_path)
    try:
        with storage:
            return handler(args, config, storage)
    except ConfigError as exc:
        _echo(f"[설정 오류] {exc}")
        logger.error("설정 오류: %s", exc)
        return EXIT_ERROR
    except AIConfigError as exc:
        _echo(f"[AI 설정 오류] {exc}")
        logger.error("AI 설정 오류: %s", exc)
        return EXIT_ERROR
    except (FetchError, CleanError, AIError, AnalysisError, ExportError, StorageError) as exc:
        _echo(f"[오류] {exc}")
        logger.error("명령 실패(%s): %s", args.command, exc)
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover
        _echo("사용자에 의해 중단되었습니다.")
        return EXIT_ERROR
    except Exception as exc:  # 예기치 못한 오류도 스택트레이스는 로그로만
        _echo(f"[예기치 못한 오류] {exc.__class__.__name__}: {exc}")
        logger.exception("예기치 못한 오류: %s", exc)
        return EXIT_ERROR
    finally:
        logger.info("명령 종료: %s", args.command)