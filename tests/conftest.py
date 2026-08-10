"""공용 pytest fixture."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from news_cli.config import SourceConfig, parse_config  # noqa: E402
from news_cli.models import RawArticle, utc_now_iso  # noqa: E402
from news_cli.storage import Storage  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolate_ai_env(monkeypatch):
    """실행 환경의 AI_* 환경변수가 테스트에 새어 들어오지 않게 한다.

    개발자 머신에 실제 키/모델이 설정돼 있어도 테스트 결과가 달라지면 안 되므로
    모든 테스트에서 기본적으로 제거하고, 필요한 테스트만 명시적으로 설정한다.
    """
    for name in ("AI_API_KEY", "AI_BASE_URL", "AI_MODEL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def sample_feed_text() -> str:
    return (FIXTURES / "sample_feed.xml").read_text(encoding="utf-8")


@pytest.fixture
def sample_article_html() -> str:
    return (FIXTURES / "sample_article.html").read_text(encoding="utf-8")


@pytest.fixture
def sample_list_html() -> str:
    return (FIXTURES / "sample_list.html").read_text(encoding="utf-8")


@pytest.fixture
def rss_source() -> SourceConfig:
    return SourceConfig(
        name="test_rss",
        type="rss",
        category="AI",
        url="https://example.test/feed",
    )


@pytest.fixture
def web_source() -> SourceConfig:
    return SourceConfig(
        name="test_web",
        type="web",
        category="IT",
        list_url="https://example.test/list",
        base_url="https://example.test",
        article_link_selector="#mw-pages .mw-category-group li a",
        title_selector="h1#firstHeading",
        body_selector="#mw-content-text .mw-parser-output p",
        date_selector="strong.published",
    )


@pytest.fixture
def config_dict(tmp_path: Path) -> dict:
    return {
        "database_path": str(tmp_path / "data" / "test.db"),
        "log_path": str(tmp_path / "logs" / "test.log"),
        "request_timeout_seconds": 5,
        "request_delay_seconds": 0,
        "duplicate_policy": "skip",
        "default_ai_model": "test-model",
        "user_agent": "ai-news-cli-test/1.0",
        "respect_robots_txt": False,
        "sources": {
            "default_rss": {"type": "rss", "url": "https://example.test/feed", "category": "AI"},
            "default_web": {
                "type": "web",
                "list_url": "https://example.test/list",
                "base_url": "https://example.test",
                "article_link_selector": "#mw-pages .mw-category-group li a",
                "title_selector": "h1#firstHeading",
                "body_selector": "#mw-content-text .mw-parser-output p",
                "date_selector": "strong.published",
                "category": "IT",
            },
        },
    }


@pytest.fixture
def config_file(tmp_path: Path, config_dict: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config_dict, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def app_config(config_dict: dict):
    return parse_config(config_dict, config_path="test-config")


@pytest.fixture
def storage(tmp_path: Path):
    """스키마가 준비된 임시 파일 기반 저장소."""
    db_path = tmp_path / "data" / "test.db"
    store = Storage(db_path)
    with store:
        yield store


def make_raw(
    storage: Storage,
    *,
    title: str = "테스트 기사 제목",
    url: str = "https://example.test/news/1",
    body: str | None = "테스트 본문 내용입니다.",
    category: str = "IT",
    source_name: str = "test_rss",
    published_at: str | None = "2026-08-05T00:00:00+00:00",
    collected_at: str | None = None,
    status: str = "ok",
) -> int:
    """테스트용 raw 기사를 넣고 id를 돌려준다."""
    payload = {
        "source": source_name,
        "url": url,
        "title": title,
        "body": body,
        "category": category,
        "published_at": published_at,
    }
    raw = RawArticle(
        source_name=source_name,
        collection_method="rss",
        canonical_url=url,
        raw_payload=json.dumps(payload, ensure_ascii=False),
        status=status,
        collected_at=collected_at or utc_now_iso(),
    )
    _, raw_id = storage.save_raw_article(raw)
    assert raw_id is not None
    return raw_id


@pytest.fixture
def make_raw_article():
    return make_raw