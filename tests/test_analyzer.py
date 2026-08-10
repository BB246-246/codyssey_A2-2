"""AI 종합 분석 테스트."""

from __future__ import annotations

import json

import pytest

from news_cli.ai_client import AIError, FakeAIClient
from news_cli.analyzer import (
    AnalysisError,
    NoArticlesError,
    analysis_row_to_result,
    analyze,
    build_analysis_input,
    extract_json_object,
    parse_analysis_response,
)
from news_cli.cleaner import clean_articles
from tests.conftest import make_raw

VALID_RESPONSE = json.dumps(
    {
        "trends": ["생성형 AI 확산", "AI 반도체 투자"],
        "keywords": ["AI", "반도체", "LLM"],
        "commonalities_differences": ["모두 투자 확대를 다룸"],
        "implications": ["국내 기업의 대응 필요"],
    },
    ensure_ascii=False,
)


@pytest.fixture
def seeded(storage):
    for i in range(3):
        make_raw(
            storage,
            url=f"https://example.test/{i}",
            title=f"AI 기사 {i}",
            body=f"AI 관련 본문 {i}",
            category="IT",
            published_at=f"2026-08-0{i + 1}T00:00:00+00:00",
        )
    clean_articles(storage, duplicate_policy="skip")
    return storage


# ---------------------------------------------------------------------------
# 응답 파싱
# ---------------------------------------------------------------------------
def test_parse_valid_response():
    result = parse_analysis_response(VALID_RESPONSE)
    assert result.trends == ["생성형 AI 확산", "AI 반도체 투자"]
    assert result.keywords == ["AI", "반도체", "LLM"]
    assert result.implications == ["국내 기업의 대응 필요"]
    assert result.commonalities_differences == ["모두 투자 확대를 다룸"]


def test_parse_response_with_code_fence():
    fenced = f"```json\n{VALID_RESPONSE}\n```"
    assert parse_analysis_response(fenced).trends


def test_parse_response_with_surrounding_text():
    noisy = f"분석 결과입니다.\n{VALID_RESPONSE}\n감사합니다."
    assert parse_analysis_response(noisy).keywords


def test_invalid_json_raises():
    with pytest.raises(AnalysisError) as excinfo:
        parse_analysis_response("이건 JSON이 아닙니다")
    assert "JSON" in str(excinfo.value)


def test_broken_json_raises():
    with pytest.raises(AnalysisError):
        parse_analysis_response('{"trends": ["a", }')


@pytest.mark.parametrize("missing", ["trends", "keywords", "implications"])
def test_missing_required_key_raises(missing):
    data = json.loads(VALID_RESPONSE)
    data.pop(missing)
    with pytest.raises(AnalysisError) as excinfo:
        parse_analysis_response(json.dumps(data, ensure_ascii=False))
    assert missing in str(excinfo.value)


def test_empty_required_list_raises():
    data = json.loads(VALID_RESPONSE)
    data["trends"] = []
    with pytest.raises(AnalysisError):
        parse_analysis_response(json.dumps(data, ensure_ascii=False))


def test_commonalities_is_optional():
    data = json.loads(VALID_RESPONSE)
    data.pop("commonalities_differences")
    result = parse_analysis_response(json.dumps(data, ensure_ascii=False))
    assert result.commonalities_differences == []


def test_non_object_json_raises():
    with pytest.raises(AnalysisError):
        extract_json_object("[1, 2, 3]")


def test_wrong_type_for_required_key_raises():
    data = json.loads(VALID_RESPONSE)
    data["trends"] = {"a": 1}
    with pytest.raises(AnalysisError):
        parse_analysis_response(json.dumps(data, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 입력 구성
# ---------------------------------------------------------------------------
def test_build_input_prefers_summary_over_body():
    articles = [{"title": "제목", "summary": "요약문", "body": "긴 본문", "source": "s", "category": "IT"}]
    text, used = build_analysis_input(articles)
    assert "요약문" in text and "긴 본문" not in text
    assert used == 1


def test_build_input_falls_back_to_truncated_body():
    articles = [{"title": "제목", "summary": "", "body": "본" * 2000, "source": "s", "category": "IT"}]
    text, used = build_analysis_input(articles, max_body_chars=50)
    assert used == 1
    assert "…" in text
    assert len(text) < 500


def test_build_input_respects_char_budget():
    articles = [
        {"title": f"제목{i}", "summary": "요" * 500, "body": "", "source": "s", "category": "IT"}
        for i in range(20)
    ]
    text, used = build_analysis_input(articles, max_chars=2000)
    assert used < 20
    assert len(text) <= 2600  # 마지막 블록 포함 여유


# ---------------------------------------------------------------------------
# analyze 통합
# ---------------------------------------------------------------------------
def test_analyze_saves_run(seeded):
    client = FakeAIClient([VALID_RESPONSE])
    run_id, result, used = analyze(seeded, client, category="IT")

    assert run_id > 0
    assert used == 3
    assert result.trends

    row = seeded.latest_analysis_run(category="IT")
    assert row["article_count"] == 3
    assert row["model"] == "fake-model"
    assert json.loads(row["keywords_json"]) == ["AI", "반도체", "LLM"]
    assert row["created_at"]

    restored = analysis_row_to_result(row)
    assert restored.trends == result.trends


def test_analyze_requests_json_mode(seeded):
    client = FakeAIClient([VALID_RESPONSE])
    analyze(seeded, client)
    assert client.calls[0]["json_mode"] is True


def test_analyze_with_no_articles_raises(storage):
    with pytest.raises(NoArticlesError) as excinfo:
        analyze(storage, FakeAIClient([VALID_RESPONSE]))
    assert "조건에 맞는 기사가 없습니다" in str(excinfo.value)


def test_analyze_with_filter_matching_nothing(seeded):
    with pytest.raises(NoArticlesError):
        analyze(seeded, FakeAIClient([VALID_RESPONSE]), category="없는카테고리")


def test_analyze_propagates_bad_json(seeded):
    with pytest.raises(AnalysisError):
        analyze(seeded, FakeAIClient(["완전히 잘못된 응답"]))
    assert seeded.count_analysis_runs() == 0


def test_analyze_wraps_ai_error(seeded):
    with pytest.raises(AnalysisError) as excinfo:
        analyze(seeded, FakeAIClient([AIError("rate limit")]))
    assert "AI 분석 호출 실패" in str(excinfo.value)


def test_analyze_limit_is_capped_by_max_articles(seeded):
    client = FakeAIClient([VALID_RESPONSE])
    _, _, used = analyze(seeded, client, limit=100, max_articles=2)
    assert used == 2


def test_analyze_date_filter(seeded):
    client = FakeAIClient([VALID_RESPONSE])
    _, _, used = analyze(
        seeded, client, date_from="2026-08-02T00:00:00+00:00", date_to="2026-08-31T23:59:59+00:00"
    )
    assert used == 2