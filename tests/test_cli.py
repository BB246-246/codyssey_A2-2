"""CLI 파서/종료 코드 테스트."""

from __future__ import annotations

import json

import pytest

from news_cli import cli
from news_cli.cli import EXIT_ERROR, EXIT_NO_DATA, EXIT_OK, REQUIRED_COMMANDS, build_parser


def test_help_lists_all_required_commands(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--help"])
    assert excinfo.value.code == 0

    help_text = capsys.readouterr().out
    for command in REQUIRED_COMMANDS:
        assert command in help_text, f"도움말에 '{command}' 명령이 없습니다"


def test_all_required_subcommands_are_registered():
    parser = build_parser()
    subparsers_actions = [
        action for action in parser._actions if hasattr(action, "choices") and action.dest == "command"
    ]
    assert subparsers_actions, "서브커맨드가 등록되어 있지 않습니다"
    choices = set(subparsers_actions[0].choices)
    assert set(REQUIRED_COMMANDS).issubset(choices)


@pytest.mark.parametrize(
    "argv",
    [
        ["fetch", "--method", "rss", "--source", "s", "--limit", "-1"],
        ["fetch", "--method", "rss", "--source", "s", "--limit", "0"],
        ["clean", "--limit", "-5"],
        ["export", "--format", "csv", "--limit", "-3"],
    ],
)
def test_negative_or_zero_limit_is_rejected(argv):
    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(argv)
    assert excinfo.value.code == 2


def test_positive_limit_is_accepted():
    args = build_parser().parse_args(["fetch", "--method", "rss", "--source", "s", "--limit", "7"])
    assert args.limit == 7


def test_summarize_targets_are_mutually_exclusive():
    parser = build_parser()
    for argv in (
        ["summarize", "--all", "--unsummarized"],
        ["summarize", "--all", "--id", "3"],
        ["summarize", "--id", "3", "--unsummarized"],
    ):
        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args(argv)
        assert excinfo.value.code == 2


def test_summarize_requires_one_target():
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["summarize"])
    assert excinfo.value.code == 2


def test_summarize_accepts_single_target():
    args = build_parser().parse_args(["summarize", "--unsummarized", "--limit", "5"])
    assert args.unsummarized is True and args.limit == 5


def test_invalid_date_format_is_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["analyze", "--date-from", "2026/08/01"])


def test_date_bounds_conversion():
    lower, upper = cli.date_bounds("2026-08-01", "2026-08-10")
    assert lower == "2026-08-01T00:00:00+00:00"
    assert upper.startswith("2026-08-10T23:59:59")
    assert cli.date_bounds(None, None) == (None, None)


def test_main_without_command_prints_help(capsys):
    assert cli.main([]) == 2
    assert "usage" in capsys.readouterr().out.lower()


def test_main_with_missing_config_reports_error(tmp_path, capsys):
    code = cli.main(["--config", str(tmp_path / "nope.json"), "clean"])
    assert code == EXIT_ERROR
    assert "설정" in capsys.readouterr().out


def test_main_clean_and_export_end_to_end(tmp_path, config_file, capsys):
    """clean → export가 실제 CLI 경로로 동작하는지 확인한다."""
    from news_cli.config import load_config
    from news_cli.storage import Storage
    from tests.conftest import make_raw

    config = load_config(config_file)
    with Storage(config.database_path) as storage:
        make_raw(storage, title="한글 제목 테스트", url="https://example.test/news/9")

    assert cli.main(["--config", str(config_file), "clean"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "[clean]" in out

    output = tmp_path / "out.jsonl"
    assert (
        cli.main(["--config", str(config_file), "export", "--format", "jsonl", "--output", str(output)])
        == EXIT_OK
    )
    lines = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["title"] == "한글 제목 테스트"


def test_show_missing_article_returns_no_data(config_file, capsys):
    assert cli.main(["--config", str(config_file), "show", "--id", "999"]) == EXIT_NO_DATA
    assert "찾을 수 없습니다" in capsys.readouterr().out

# ---------------------------------------------------------------------------
# 서브커맨드 통합 (fake AI + fake HTTP, 실제 네트워크/AI 없음)
# ---------------------------------------------------------------------------
@pytest.fixture
def cli_env(tmp_path, config_file, monkeypatch):
    """CLI 통합 테스트용 환경: 출력물이 tmp_path 아래에 생기도록 chdir."""
    monkeypatch.chdir(tmp_path)
    return config_file


def _fake_ai(monkeypatch, responses):
    from news_cli.ai_client import FakeAIClient

    client = FakeAIClient(responses)
    monkeypatch.setattr("news_cli.cli.build_ai_client", lambda config, model=None: client)
    return client


def test_cli_fetch_with_mocked_http(cli_env, monkeypatch, capsys, sample_feed_text):
    from tests.test_collectors import FakeResponse, make_fetcher

    fetcher = make_fetcher({"https://example.test/feed": FakeResponse(sample_feed_text)})
    real_build = cli.run_fetch

    def patched(storage, config, *, source, method, limit, fetcher=None):
        return real_build(storage, config, source=source, method=method, limit=limit, fetcher=fetcher)

    monkeypatch.setattr(
        "news_cli.cli.run_fetch",
        lambda storage, config, **kw: patched(storage, config, **{**kw, "fetcher": fetcher}),
    )

    code = cli.main(["--config", str(cli_env), "fetch", "--method", "rss", "--source", "default_rss"])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "[fetch]" in out and "성공 3건" in out


def test_cli_summarize_uses_fake_client(cli_env, monkeypatch, capsys):
    from news_cli.cleaner import clean_articles
    from news_cli.config import load_config
    from news_cli.storage import Storage
    from tests.conftest import make_raw

    config = load_config(cli_env)
    with Storage(config.database_path) as storage:
        make_raw(storage, url="https://example.test/s1", title="요약 대상", body="본문입니다")
        clean_articles(storage, duplicate_policy="skip")

    _fake_ai(monkeypatch, ["한국어 요약문"])
    assert cli.main(["--config", str(cli_env), "summarize", "--unsummarized"]) == EXIT_OK
    assert "성공 1건" in capsys.readouterr().out

    with Storage(config.database_path) as storage:
        assert storage.query_clean_articles(status="summarized")[0]["summary"] == "한국어 요약문"


def test_cli_analyze_no_data_returns_exit_3(cli_env, monkeypatch, capsys):
    _fake_ai(monkeypatch, ["{}"])
    code = cli.main(["--config", str(cli_env), "analyze", "--category", "없는것"])
    assert code == EXIT_NO_DATA
    assert "조건에 맞는 기사가 없습니다" in capsys.readouterr().out


def test_cli_analyze_report_export_flow(cli_env, monkeypatch, capsys):
    from news_cli.cleaner import clean_articles
    from news_cli.config import load_config
    from news_cli.storage import Storage
    from tests.conftest import make_raw
    from tests.test_analyzer import VALID_RESPONSE

    config = load_config(cli_env)
    with Storage(config.database_path) as storage:
        for i in range(3):
            make_raw(
                storage,
                url=f"https://example.test/a{i}",
                title=f"AI 기사 {i}",
                body=f"본문 {i}",
                category="IT",
                published_at=f"2026-08-0{i + 1}T00:00:00+00:00",
            )
        clean_articles(storage, duplicate_policy="skip")

    _fake_ai(monkeypatch, [VALID_RESPONSE])
    code = cli.main(
        [
            "--config", str(cli_env), "analyze",
            "--date-from", "2026-08-01", "--date-to", "2026-08-10",
            "--category", "IT", "--limit", "10",
        ]
    )
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "주요 트렌드" in out and "생성형 AI 확산" in out

    # 사용자가 입력한 날짜 문자열이 analysis_runs에 기록되어야 report에서 매칭된다
    with Storage(config.database_path) as storage:
        row = storage.latest_analysis_run(date_from="2026-08-01", date_to="2026-08-10", category="IT")
        assert row is not None and row["date_from"] == "2026-08-01"

    assert cli.main(["--config", str(cli_env), "report", "--format", "md", "--top-n", "5"]) == EXIT_OK
    report_out = capsys.readouterr().out
    assert "품질 지표" in report_out and "생성형 AI 확산" in report_out
    charts = sorted((tmp_charts := (cli_env.parent / "output" / "charts")).glob("*.png"))
    assert {p.name for p in charts} == {"category_counts.png", "daily_collection_trend.png"}
    assert (cli_env.parent / "output" / "reports").exists()

    for fmt in ("csv", "jsonl", "xlsx"):
        assert cli.main(["--config", str(cli_env), "export", "--format", fmt, "--status", "all"]) == EXIT_OK
        assert "3행" in capsys.readouterr().out


def test_cli_list_and_show(cli_env, capsys):
    from news_cli.cleaner import clean_articles
    from news_cli.config import load_config
    from news_cli.storage import Storage
    from tests.conftest import make_raw

    config = load_config(cli_env)
    with Storage(config.database_path) as storage:
        for i in range(5):
            make_raw(storage, url=f"https://example.test/l{i}", title=f"목록 기사 {i}", body="본문")
        clean_articles(storage, duplicate_policy="skip")

    assert cli.main(["--config", str(cli_env), "list", "--page-size", "2", "--page", "1"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "총 5건" in out and out.count("목록 기사") == 2

    assert cli.main(["--config", str(cli_env), "list", "--keyword", "목록 기사 3"]) == EXIT_OK
    assert "목록 기사 3" in capsys.readouterr().out

    assert cli.main(["--config", str(cli_env), "show", "--id", "1"]) == EXIT_OK
    shown = capsys.readouterr().out
    assert "[본문]" in shown and "목록 기사 0" in shown


def test_cli_summarize_without_api_key_reports_config_error(cli_env, monkeypatch, capsys):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    code = cli.main(["--config", str(cli_env), "summarize", "--unsummarized"])
    assert code == EXIT_ERROR
    assert "AI_API_KEY" in capsys.readouterr().out
