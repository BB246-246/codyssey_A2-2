"""설정 로딩/검증 테스트."""

from __future__ import annotations

import json

import pytest

from news_cli.config import ConfigError, load_config, parse_config


def test_load_valid_config(config_file):
    config = load_config(config_file)
    assert config.database_path.endswith("test.db")
    assert config.duplicate_policy == "skip"
    assert "default_rss" in config.sources
    assert config.get_source("default_web").type == "web"


def test_missing_file_raises_readable_error(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path / "absent.json")
    assert "찾을 수 없습니다" in str(excinfo.value)


def test_invalid_json_raises_readable_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert "JSON" in str(excinfo.value)


@pytest.mark.parametrize("missing", ["database_path", "log_path", "sources"])
def test_required_top_level_fields(config_dict, missing):
    config_dict.pop(missing)
    with pytest.raises(ConfigError) as excinfo:
        parse_config(config_dict)
    assert missing in str(excinfo.value)


def test_invalid_duplicate_policy(config_dict):
    config_dict["duplicate_policy"] = "merge"
    with pytest.raises(ConfigError) as excinfo:
        parse_config(config_dict)
    assert "duplicate_policy" in str(excinfo.value)


def test_invalid_timeout(config_dict):
    config_dict["request_timeout_seconds"] = "십초"
    with pytest.raises(ConfigError) as excinfo:
        parse_config(config_dict)
    assert "request_timeout_seconds" in str(excinfo.value)


def test_unknown_source_type(config_dict):
    config_dict["sources"]["bad"] = {"type": "graphql", "url": "https://example.test"}
    with pytest.raises(ConfigError) as excinfo:
        parse_config(config_dict)
    assert "type" in str(excinfo.value)


def test_rss_source_requires_url(config_dict):
    config_dict["sources"]["default_rss"].pop("url")
    with pytest.raises(ConfigError):
        parse_config(config_dict)


def test_web_source_requires_selectors(config_dict):
    config_dict["sources"]["default_web"].pop("article_link_selector")
    with pytest.raises(ConfigError) as excinfo:
        parse_config(config_dict)
    assert "article_link_selector" in str(excinfo.value)


def test_placeholder_values_are_rejected(config_dict):
    config_dict["sources"]["default_rss"]["url"] = "REPLACE_WITH_ALLOWED_RSS_URL"
    with pytest.raises(ConfigError) as excinfo:
        parse_config(config_dict)
    assert "자리표시자" in str(excinfo.value)


def test_unknown_source_name_error_lists_available(app_config):
    with pytest.raises(ConfigError) as excinfo:
        app_config.get_source("nope")
    message = str(excinfo.value)
    assert "default_rss" in message and "default_web" in message


def test_shipped_example_config_is_valid():
    """저장소에 커밋되는 config.example.json이 실제로 유효해야 한다."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "config.example.json"
    data = json.loads(example.read_text(encoding="utf-8"))
    config = parse_config(data, config_path=str(example))
    assert config.sources, "예시 설정에 소스가 없습니다"
    assert "default_rss" in config.sources and "default_web" in config.sources


def test_api_key_is_not_read_from_config(config_dict):
    """비밀값은 config가 아니라 환경변수에서만 읽는다."""
    config_dict["api_key"] = "sk-should-be-ignored"
    config = parse_config(config_dict)
    assert not hasattr(config, "api_key")