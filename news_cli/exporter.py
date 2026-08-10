"""CSV / JSONL / Excel 내보내기.

- CSV는 Excel 한글 호환을 위해 utf-8-sig 인코딩
- JSONL은 한 줄에 유효한 JSON 객체 하나 (ensure_ascii=False로 한글 보존)
- XLSX는 openpyxl 엔진
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from .models import utc_now_iso
from .storage import Storage

logger = logging.getLogger(__name__)

DEFAULT_EXPORT_DIR = Path("output/exports")
SUPPORTED_FORMATS = ("csv", "jsonl", "xlsx")

EXPORT_COLUMNS: tuple[str, ...] = (
    "id",
    "title",
    "url",
    "source",
    "category",
    "published_at",
    "collected_at",
    "clean_status",
    "summary",
    "summary_model",
    "summarized_at",
    "original_length",
    "summary_length",
    "content_hash",
    "body",
)


class ExportError(Exception):
    """내보내기 오류."""


def default_export_path(fmt: str, directory: str | Path = DEFAULT_EXPORT_DIR) -> Path:
    stamp = utc_now_iso().replace(":", "").replace("-", "")[:15]
    return Path(directory) / f"news_{stamp}.{fmt}"


def rows_to_frame(rows: Sequence[dict[str, Any]]) -> pd.DataFrame:
    """조회 결과를 고정 컬럼 순서의 DataFrame으로 변환한다."""
    frame = pd.DataFrame(list(rows), columns=list(EXPORT_COLUMNS))
    return frame.where(pd.notnull(frame), None)


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    # utf-8-sig: Excel에서 한글이 깨지지 않도록 BOM 포함
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def write_jsonl(rows: Sequence[dict[str, Any]], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            record = {key: row.get(key) for key in EXPORT_COLUMNS}
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_xlsx(frame: pd.DataFrame, path: Path) -> None:
    try:
        frame.to_excel(path, index=False, engine="openpyxl", sheet_name="news")
    except ImportError as exc:  # pragma: no cover - 의존성 안내
        raise ExportError("openpyxl이 설치되어 있지 않습니다. requirements.txt를 설치하세요.") from exc


def export_rows(
    rows: Sequence[dict[str, Any]],
    *,
    fmt: str,
    output: str | Path | None = None,
) -> tuple[Path, int]:
    """행 목록을 지정 포맷으로 저장한다. (경로, 행 수)를 돌려준다."""
    if fmt not in SUPPORTED_FORMATS:
        raise ExportError(f"지원하지 않는 형식: {fmt!r} (지원: {', '.join(SUPPORTED_FORMATS)})")

    target = Path(output) if output else default_export_path(fmt)
    target.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "jsonl":
        write_jsonl(rows, target)
    else:
        frame = rows_to_frame(rows)
        if fmt == "csv":
            write_csv(frame, target)
        else:
            write_xlsx(frame, target)

    logger.info("내보내기 완료: %s (%d행, format=%s)", target, len(rows), fmt)
    return target, len(rows)


def export_articles(
    storage: Storage,
    *,
    fmt: str,
    status: str = "all",
    date_from: str | None = None,
    date_to: str | None = None,
    category: str | None = None,
    output: str | Path | None = None,
    limit: int | None = None,
) -> tuple[Path, int]:
    """필터 조건으로 clean 기사를 조회해 내보낸다."""
    if status not in ("all", "summarized", "unsummarized"):
        raise ExportError(f"지원하지 않는 status: {status!r}")

    rows = storage.query_clean_articles(
        date_from=date_from,
        date_to=date_to,
        category=category,
        status=status,
        limit=limit,
    )
    if not rows:
        logger.warning("내보낼 데이터가 없습니다. 빈 파일을 생성합니다 (status=%s).", status)
    return export_rows(rows, fmt=fmt, output=output)