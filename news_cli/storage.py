"""SQLite 영구 저장 계층.

- 앱 시작 시 `CREATE TABLE IF NOT EXISTS`로 스키마를 초기화한다.
- 쓰기는 트랜잭션으로 감싸고, 연결은 컨텍스트 매니저로 항상 닫는다.
- 모든 조회 결과는 평범한 dict로 돌려준다(호출부가 sqlite3.Row에 묶이지 않도록).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from .models import AnalysisResult, CleanArticle, FetchStats, RawArticle, utc_now_iso

logger = logging.getLogger(__name__)

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS raw_articles (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        external_id       TEXT,
        source_name       TEXT NOT NULL,
        source_url        TEXT,
        collection_method TEXT NOT NULL,
        collected_at      TEXT NOT NULL,
        canonical_url     TEXT NOT NULL,
        raw_payload       TEXT NOT NULL,
        status            TEXT NOT NULL,
        error_message     TEXT,
        UNIQUE(source_name, canonical_url)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS clean_articles (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_id         INTEGER NOT NULL UNIQUE,
        title          TEXT NOT NULL,
        body           TEXT,
        url            TEXT NOT NULL UNIQUE,
        source         TEXT NOT NULL,
        category       TEXT NOT NULL,
        published_at   TEXT,
        collected_at   TEXT NOT NULL,
        content_hash   TEXT,
        clean_status   TEXT NOT NULL,
        summary        TEXT,
        summary_model  TEXT,
        summarized_at  TEXT,
        original_length INTEGER,
        summary_length  INTEGER,
        FOREIGN KEY(raw_id) REFERENCES raw_articles(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_runs (
        id                              INTEGER PRIMARY KEY AUTOINCREMENT,
        date_from                       TEXT,
        date_to                         TEXT,
        category                        TEXT,
        article_count                   INTEGER NOT NULL,
        trends_json                     TEXT NOT NULL,
        keywords_json                   TEXT NOT NULL,
        commonalities_differences_json  TEXT,
        implications_json               TEXT NOT NULL,
        model                           TEXT,
        created_at                      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fetch_runs (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        source_name       TEXT,
        collection_method TEXT,
        attempted_count   INTEGER,
        success_count     INTEGER,
        failure_count     INTEGER,
        duplicate_count   INTEGER,
        started_at        TEXT,
        finished_at       TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_raw_canonical_url ON raw_articles(canonical_url)",
    "CREATE INDEX IF NOT EXISTS idx_clean_category ON clean_articles(category)",
    "CREATE INDEX IF NOT EXISTS idx_clean_collected_at ON clean_articles(collected_at)",
    "CREATE INDEX IF NOT EXISTS idx_clean_content_hash ON clean_articles(content_hash)",
)


class StorageError(Exception):
    """저장소 계층 오류."""


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Storage:
    """SQLite 저장소. `with Storage(path) as storage:` 형태로 사용한다."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    # -- 연결 관리 ---------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            if self.db_path != ":memory:":
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            self._conn = conn
            logger.debug("SQLite 연결: %s", self.db_path)
        return self._conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self.connect()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.commit()
            finally:
                self._conn.close()
                self._conn = None
                logger.debug("SQLite 연결 종료: %s", self.db_path)

    def __enter__(self) -> "Storage":
        self.connect()
        self.init_schema()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- 스키마 -------------------------------------------------------------
    def init_schema(self) -> None:
        conn = self.conn
        with conn:
            for statement in SCHEMA_STATEMENTS:
                conn.execute(statement)

    # -- raw ----------------------------------------------------------------
    def save_raw_article(self, article: RawArticle) -> tuple[str, int | None]:
        """raw 기사를 저장한다.

        Returns:
            ("inserted", id) 또는 ("duplicate", 기존 id)
        """
        conn = self.conn
        existing = conn.execute(
            "SELECT id FROM raw_articles WHERE source_name = ? AND canonical_url = ?",
            (article.source_name, article.canonical_url),
        ).fetchone()
        if existing is not None:
            logger.warning(
                "중복 raw 기사 skip: source=%s url=%s", article.source_name, article.canonical_url
            )
            return "duplicate", int(existing["id"])

        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO raw_articles
                        (external_id, source_name, source_url, collection_method,
                         collected_at, canonical_url, raw_payload, status, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        article.external_id,
                        article.source_name,
                        article.source_url,
                        article.collection_method,
                        article.collected_at,
                        article.canonical_url,
                        article.raw_payload,
                        article.status,
                        article.error_message,
                    ),
                )
        except sqlite3.IntegrityError:
            # 동시 실행 등으로 사이에 삽입된 경우
            row = conn.execute(
                "SELECT id FROM raw_articles WHERE source_name = ? AND canonical_url = ?",
                (article.source_name, article.canonical_url),
            ).fetchone()
            return "duplicate", (int(row["id"]) if row else None)

        article.id = int(cursor.lastrowid)
        return "inserted", article.id

    def list_raw_articles(
        self, *, limit: int | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM raw_articles"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_raw_article(self, raw_id: int) -> dict[str, Any] | None:
        return _row_to_dict(
            self.conn.execute("SELECT * FROM raw_articles WHERE id = ?", (raw_id,)).fetchone()
        )

    def mark_raw_status(self, raw_id: int, status: str, error_message: str | None = None) -> None:
        conn = self.conn
        with conn:
            conn.execute(
                "UPDATE raw_articles SET status = ?, error_message = ? WHERE id = ?",
                (status, error_message, raw_id),
            )

    def count_raw_articles(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS c FROM raw_articles").fetchone()["c"])

    # -- clean --------------------------------------------------------------
    def save_clean_article(self, article: CleanArticle, *, duplicate_policy: str = "skip") -> str:
        """clean 기사를 저장한다.

        raw_id 또는 url이 이미 있으면 정책에 따라 skip 또는 갱신한다.
        어떤 정책에서도 레코드 수가 증가하지 않으므로 재실행 멱등성이 보장된다.

        Returns: "inserted" | "updated" | "skipped"
        """
        conn = self.conn
        existing = conn.execute(
            "SELECT id FROM clean_articles WHERE raw_id = ? OR url = ? LIMIT 1",
            (article.raw_id, article.url),
        ).fetchone()

        if existing is not None:
            if duplicate_policy == "skip":
                logger.debug("clean 중복 skip: %s", article.url)
                article.id = int(existing["id"])
                return "skipped"
            with conn:
                conn.execute(
                    """
                    UPDATE clean_articles
                       SET raw_id = ?, title = ?, body = ?, url = ?, source = ?,
                           category = ?, published_at = ?, collected_at = ?,
                           content_hash = ?, clean_status = ?, original_length = ?
                     WHERE id = ?
                    """,
                    (
                        article.raw_id,
                        article.title,
                        article.body,
                        article.url,
                        article.source,
                        article.category,
                        article.published_at,
                        article.collected_at,
                        article.content_hash,
                        article.clean_status,
                        article.original_length,
                        int(existing["id"]),
                    ),
                )
            article.id = int(existing["id"])
            return "updated"

        with conn:
            cursor = conn.execute(
                """
                INSERT INTO clean_articles
                    (raw_id, title, body, url, source, category, published_at,
                     collected_at, content_hash, clean_status, summary, summary_model,
                     summarized_at, original_length, summary_length)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article.raw_id,
                    article.title,
                    article.body,
                    article.url,
                    article.source,
                    article.category,
                    article.published_at,
                    article.collected_at,
                    article.content_hash,
                    article.clean_status,
                    article.summary,
                    article.summary_model,
                    article.summarized_at,
                    article.original_length,
                    article.summary_length,
                ),
            )
        article.id = int(cursor.lastrowid)
        return "inserted"

    def get_clean_article(self, article_id: int) -> dict[str, Any] | None:
        return _row_to_dict(
            self.conn.execute("SELECT * FROM clean_articles WHERE id = ?", (article_id,)).fetchone()
        )

    def query_clean_articles(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        category: str | None = None,
        source: str | None = None,
        status: str = "all",
        keyword: str | None = None,
        require_body: bool = False,
        limit: int | None = None,
        offset: int = 0,
        order: str = "ASC",
    ) -> list[dict[str, Any]]:
        """필터 조건으로 clean 기사를 조회한다.

        날짜 필터는 published_at이 있으면 그것을, 없으면 collected_at을 기준으로 한다.
        `status`: all | summarized | unsummarized
        """
        where: list[str] = []
        params: list[Any] = []
        effective_date = "COALESCE(published_at, collected_at)"

        if date_from:
            where.append(f"{effective_date} >= ?")
            params.append(date_from)
        if date_to:
            where.append(f"{effective_date} <= ?")
            params.append(date_to)
        if category:
            where.append("category = ?")
            params.append(category)
        if source:
            where.append("source = ?")
            params.append(source)
        if status == "summarized":
            where.append("summary IS NOT NULL AND TRIM(summary) <> ''")
        elif status == "unsummarized":
            where.append("(summary IS NULL OR TRIM(summary) = '')")
        elif status not in ("all", None):
            raise StorageError(f"알 수 없는 status 필터: {status!r}")
        if require_body:
            where.append("body IS NOT NULL AND TRIM(body) <> ''")
        if keyword:
            where.append("(title LIKE ? OR COALESCE(body, '') LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like])

        sql = "SELECT * FROM clean_articles"
        if where:
            sql += " WHERE " + " AND ".join(where)
        direction = "DESC" if str(order).upper() == "DESC" else "ASC"
        sql += f" ORDER BY {effective_date} {direction}, id {direction}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
            if offset:
                sql += " OFFSET ?"
                params.append(int(offset))
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            params.append(int(offset))

        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def count_clean_articles(self, **filters: Any) -> int:
        """query_clean_articles와 동일한 필터로 개수만 센다."""
        filters.pop("limit", None)
        filters.pop("offset", None)
        return len(self.query_clean_articles(**filters))

    def update_summary(
        self,
        article_id: int,
        *,
        summary: str,
        model: str,
        original_length: int,
        summarized_at: str | None = None,
    ) -> None:
        conn = self.conn
        with conn:
            conn.execute(
                """
                UPDATE clean_articles
                   SET summary = ?, summary_model = ?, summarized_at = ?,
                       original_length = ?, summary_length = ?
                 WHERE id = ?
                """,
                (
                    summary,
                    model,
                    summarized_at or utc_now_iso(),
                    original_length,
                    len(summary),
                    article_id,
                ),
            )

    # -- 집계 ---------------------------------------------------------------
    def category_counts(self, **filters: Any) -> list[tuple[str, int]]:
        rows = self.query_clean_articles(**filters)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["category"] or "unknown"] = counts.get(row["category"] or "unknown", 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def source_counts(self, **filters: Any) -> list[tuple[str, int]]:
        rows = self.query_clean_articles(**filters)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["source"] or "unknown"] = counts.get(row["source"] or "unknown", 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def daily_counts(self, **filters: Any) -> list[tuple[str, int]]:
        """일자(YYYY-MM-DD)별 수집 건수."""
        rows = self.query_clean_articles(**filters)
        counts: dict[str, int] = {}
        for row in rows:
            stamp = row.get("collected_at") or row.get("published_at") or ""
            day = str(stamp)[:10]
            if not day:
                continue
            counts[day] = counts.get(day, 0) + 1
        return sorted(counts.items())

    # -- analysis -----------------------------------------------------------
    def save_analysis_run(
        self,
        result: AnalysisResult,
        *,
        article_count: int,
        date_from: str | None = None,
        date_to: str | None = None,
        category: str | None = None,
        model: str | None = None,
        created_at: str | None = None,
    ) -> int:
        conn = self.conn
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO analysis_runs
                    (date_from, date_to, category, article_count, trends_json, keywords_json,
                     commonalities_differences_json, implications_json, model, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    date_from,
                    date_to,
                    category,
                    article_count,
                    json.dumps(result.trends, ensure_ascii=False),
                    json.dumps(result.keywords, ensure_ascii=False),
                    json.dumps(result.commonalities_differences, ensure_ascii=False),
                    json.dumps(result.implications, ensure_ascii=False),
                    model,
                    created_at or utc_now_iso(),
                ),
            )
        return int(cursor.lastrowid)

    def latest_analysis_run(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any] | None:
        """조건이 일치하는 최신 분석을 찾고, 없으면 가장 최근 분석을 돌려준다."""
        where: list[str] = []
        params: list[Any] = []
        if date_from:
            where.append("date_from = ?")
            params.append(date_from)
        if date_to:
            where.append("date_to = ?")
            params.append(date_to)
        if category:
            where.append("category = ?")
            params.append(category)

        sql = "SELECT * FROM analysis_runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT 1"

        row = self.conn.execute(sql, params).fetchone()
        if row is None and where:
            row = self.conn.execute(
                "SELECT * FROM analysis_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return _row_to_dict(row)

    def count_analysis_runs(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS c FROM analysis_runs").fetchone()["c"])

    # -- fetch_runs ----------------------------------------------------------
    def save_fetch_run(self, stats: FetchStats) -> int:
        conn = self.conn
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO fetch_runs
                    (source_name, collection_method, attempted_count, success_count,
                     failure_count, duplicate_count, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stats.source_name,
                    stats.collection_method,
                    stats.attempted,
                    stats.succeeded,
                    stats.failed,
                    stats.duplicates,
                    stats.started_at,
                    stats.finished_at or utc_now_iso(),
                ),
            )
        return int(cursor.lastrowid)

    def list_fetch_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM fetch_runs ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- 편의 -----------------------------------------------------------------
    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        conn = self.conn
        with conn:
            conn.executemany(sql, rows)


def open_storage(db_path: str | Path) -> Storage:
    """스키마가 초기화된 Storage를 연다(호출부에서 close 책임)."""
    storage = Storage(db_path)
    storage.connect()
    storage.init_schema()
    return storage