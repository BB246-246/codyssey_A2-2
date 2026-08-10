"""로깅 설정.

콘솔 핸들러 + rotating file handler를 구성하고, 비밀값이 로그에 남지 않도록
정규식 기반 redaction 필터를 모든 핸들러에 붙인다.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 3

# 로그에 절대 남으면 안 되는 패턴들.
_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(authorization)\s*[:=]\s*\S+"), r"\1: [REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9._\-]{8,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"(?i)\b(api[_-]?key|ai_api_key|token|secret)\s*[:=]\s*\S+"), r"\1=[REDACTED]"),
)


class RedactSecretsFilter(logging.Filter):
    """메시지에서 API 키/토큰처럼 보이는 문자열을 마스킹한다."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - 포맷 실패는 원본 유지
            return True

        redacted = redact(message)
        env_key = os.environ.get("AI_API_KEY")
        if env_key and len(env_key) >= 6 and env_key in redacted:
            redacted = redacted.replace(env_key, "[REDACTED_API_KEY]")

        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def redact(text: str) -> str:
    """알려진 비밀값 패턴을 마스킹한 문자열을 돌려준다."""
    for pattern, replacement in _REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def setup_logging(
    log_path: str | os.PathLike[str] | None = None,
    *,
    level: int | str = logging.INFO,
    console: bool = True,
) -> logging.Logger:
    """루트 로거를 구성하고 애플리케이션 로거를 돌려준다.

    반복 호출해도 핸들러가 중복되지 않도록 기존 핸들러를 정리한다.
    """
    if isinstance(level, str):
        level = logging.getLevelName(level.upper())
        if not isinstance(level, int):
            level = logging.INFO

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # pragma: no cover
            pass
    root.setLevel(level)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    redactor = RedactSecretsFilter()

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        stream.addFilter(redactor)
        stream.setLevel(level)
        root.addHandler(stream)

    if log_path:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        file_handler.setLevel(level)
        root.addHandler(file_handler)

    # 서드파티 라이브러리의 과도한 로그 억제
    for noisy in ("urllib3", "matplotlib", "matplotlib.font_manager", "openai", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logging.getLogger("news_cli")