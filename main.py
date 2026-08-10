#!/usr/bin/env python3
"""AI 뉴스 트렌드 및 종합 분석 CLI 진입점.

사용법:
    python main.py --config config.json <subcommand> [options]
"""

from __future__ import annotations

import sys


def _force_utf8_console() -> None:
    """Windows 기본 콘솔 코드페이지에서 한글 출력이 깨지지 않도록 한다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 환경 의존
            pass


def main() -> int:
    _force_utf8_console()
    from news_cli.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())