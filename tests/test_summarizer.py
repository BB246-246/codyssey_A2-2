"""AI 요약 테스트 (FakeAIClient만 사용, 실제 API 호출 없음)."""

from __future__ import annotations

import logging

import pytest

from news_cli.ai_client import (
    AIConfigError,
    AIError,
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MODEL,
    FakeAIClient,
    build_ai_client,
    resolve_model,
)
from news_cli.cleaner import clean_articles
from news_cli.summarizer import (
    SummarizeError,
    build_prompt,
    select_targets,
    summarize_articles,
)
from tests.conftest import make_raw


@pytest.fixture
def seeded(storage):
    """clean 기사 3건이 들어 있는 저장소."""
    for i in range(3):
        make_raw(storage, url=f"https://example.test/{i}", title=f"기사 {i}", body=f"본문 {i}")
    clean_articles(storage, duplicate_policy="skip")
    return storage


def test_prompt_requests_korean_3_to_5_sentences():
    from news_cli.summarizer import SYSTEM_PROMPT

    assert "한국어" in SYSTEM_PROMPT
    assert "3~5" in SYSTEM_PROMPT
    assert "추가하지" in SYSTEM_PROMPT  # 사실 추가 금지


def test_build_prompt_truncates_long_body():
    article = {"title": "제목", "body": "가" * 5000, "source": "s", "category": "IT"}
    prompt = build_prompt(article, max_body_chars=100)
    assert "이하 생략" in prompt
    assert len(prompt) < 1000


def test_summarize_success(seeded):
    client = FakeAIClient(["요약1", "요약2", "요약3"])
    stats = summarize_articles(seeded, client, mode="unsummarized")

    assert stats.attempted == 3
    assert stats.succeeded == 3
    assert stats.failed == 0
    assert len(seeded.query_clean_articles(status="summarized")) == 3

    row = seeded.query_clean_articles(status="summarized")[0]
    assert row["summary_model"] == "fake-model"
    assert row["summary_length"] == len(row["summary"])
    assert row["original_length"] > 0
    assert row["summarized_at"]


def test_summarize_continues_after_api_failure(seeded, caplog):
    client = FakeAIClient(["요약1", AIError("일시적 API 오류"), "요약3"])
    with caplog.at_level(logging.ERROR):
        stats = summarize_articles(seeded, client, mode="unsummarized")

    assert stats.attempted == 3
    assert stats.succeeded == 2
    assert stats.failed == 1
    assert len(seeded.query_clean_articles(status="summarized")) == 2
    assert any("요약 실패" in record.message for record in caplog.records)


def test_summarize_continues_after_unexpected_error(seeded):
    client = FakeAIClient(["요약1", RuntimeError("예기치 못한 오류"), "요약3"])
    stats = summarize_articles(seeded, client, mode="unsummarized")
    assert stats.succeeded == 2 and stats.failed == 1


def test_article_body_is_not_logged(seeded, caplog):
    secret_body = "절대로로그에남으면안되는본문내용"
    make_raw(seeded, url="https://example.test/secret", title="비밀", body=secret_body)
    clean_articles(seeded, duplicate_policy="skip")

    client = FakeAIClient(["요약"] * 10)
    with caplog.at_level(logging.DEBUG, logger="news_cli.summarizer"):
        summarize_articles(seeded, client, mode="unsummarized")

    assert all(secret_body not in record.getMessage() for record in caplog.records)


def test_already_summarized_is_skipped_by_default(seeded):
    summarize_articles(seeded, FakeAIClient(["요약"] * 3), mode="unsummarized")

    client = FakeAIClient(["새 요약"] * 3)
    stats = summarize_articles(seeded, client, mode="all")
    assert stats.attempted == 0
    assert stats.succeeded == 0
    assert client.calls == []


def test_force_overwrites_existing_summary(seeded):
    summarize_articles(seeded, FakeAIClient(["원래 요약"] * 3), mode="unsummarized")

    stats = summarize_articles(seeded, FakeAIClient(["덮어쓴 요약"] * 3), mode="all", force=True)
    assert stats.succeeded == 3
    assert all(r["summary"] == "덮어쓴 요약" for r in seeded.query_clean_articles())


def test_summarize_by_id(seeded):
    target = seeded.query_clean_articles()[1]
    stats = summarize_articles(seeded, FakeAIClient(["단건 요약"]), mode="id", article_id=int(target["id"]))
    assert stats.succeeded == 1
    assert seeded.get_clean_article(int(target["id"]))["summary"] == "단건 요약"
    assert len(seeded.query_clean_articles(status="summarized")) == 1


def test_summarize_by_missing_id_raises(seeded):
    with pytest.raises(SummarizeError):
        summarize_articles(seeded, FakeAIClient(["x"]), mode="id", article_id=99999)


def test_summarize_respects_limit(seeded):
    stats = summarize_articles(seeded, FakeAIClient(["a", "b", "c"]), mode="unsummarized", limit=2)
    assert stats.succeeded == 2


def test_select_targets_unknown_mode(seeded):
    with pytest.raises(SummarizeError):
        select_targets(seeded, mode="weird")


# ---------------------------------------------------------------------------
# ai_client
# ---------------------------------------------------------------------------
def test_build_ai_client_requires_env_key(monkeypatch):
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    with pytest.raises(AIConfigError) as excinfo:
        build_ai_client(None)
    assert ENV_API_KEY in str(excinfo.value)


def test_resolve_model_priority(monkeypatch, app_config):
    monkeypatch.delenv(ENV_MODEL, raising=False)
    assert resolve_model(app_config) == "test-model"

    monkeypatch.setenv(ENV_MODEL, "env-model")
    assert resolve_model(app_config) == "env-model"
    assert resolve_model(app_config, "cli-model") == "cli-model"


def test_fake_client_records_calls():
    client = FakeAIClient(handler=lambda s, u, j: f"json={j}")
    assert client.complete("sys", "user", json_mode=True) == "json=True"
    assert client.calls[0]["system"] == "sys"


def test_fake_client_exhausted_raises():
    with pytest.raises(AIError):
        FakeAIClient([]).complete("s", "u")


def test_api_key_never_appears_in_logs(monkeypatch, caplog, seeded):
    """redaction 필터가 sk- 키를 마스킹하는지 확인."""
    from news_cli.logging_config import redact

    assert "sk-abcdef123456" not in redact("Authorization: Bearer sk-abcdef123456")
    assert "[REDACTED" in redact("api_key=sk-abcdef123456")
    assert ENV_BASE_URL  # 상수 존재 확인