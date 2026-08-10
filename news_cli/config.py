"""설정 파일 로딩과 검증.

API 키 같은 비밀값은 절대 이 파일에서 읽지 않는다. 비밀값은 환경변수 전용이다
(`news_cli.ai_client` 참고).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = "config.json"
DUPLICATE_POLICIES = ("skip", "upsert")
SOURCE_TYPES = ("rss", "web")

DEFAULT_USER_AGENT = "ai-news-cli/1.0 (+educational CLI project)"

# source 타입별 필수 키
_REQUIRED_SOURCE_KEYS: dict[str, tuple[str, ...]] = {
    "rss": ("url",),
    "web": ("list_url", "article_link_selector", "title_selector", "body_selector"),
}


class ConfigError(Exception):
    """사람이 읽을 수 있는 설정 오류."""


@dataclass(frozen=True)
class SourceConfig:
    """단일 뉴스 소스 설정.

    `params`와 `auth_header_env`는 인증이 필요한 공개 검색 API(예: 네이버 뉴스 검색
    API)를 rss 타입으로 다루기 위한 항목이다. RSS 2.0 XML을 돌려주므로 파싱은
    동일하고, 차이는 쿼리 파라미터와 인증 헤더뿐이다.

    비밀값은 config에 두지 않는다. `auth_header_env`에는 헤더 이름과
    **환경변수 이름**만 적고, 실제 값은 수집 시점에 환경변수에서 읽는다.
    """

    name: str
    type: str
    category: str = "unknown"
    url: str | None = None
    list_url: str | None = None
    base_url: str | None = None
    article_link_selector: str | None = None
    title_selector: str | None = None
    body_selector: str | None = None
    date_selector: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    auth_header_env: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AppConfig:
    """애플리케이션 전역 설정."""

    database_path: str
    log_path: str
    request_timeout_seconds: float = 10.0
    request_delay_seconds: float = 1.0
    duplicate_policy: str = "skip"
    default_ai_model: str = "gpt-4o-mini"
    user_agent: str = DEFAULT_USER_AGENT
    respect_robots_txt: bool = True
    summary_language: str = "Korean"
    max_analysis_articles: int = 50
    max_analysis_chars: int = 20000
    max_body_chars_for_ai: int = 4000
    sources: dict[str, SourceConfig] = field(default_factory=dict)
    config_path: str | None = None

    def get_source(self, name: str) -> SourceConfig:
        """이름으로 소스를 찾는다. 없으면 사용 가능한 목록을 알려준다."""
        try:
            return self.sources[name]
        except KeyError:
            available = ", ".join(sorted(self.sources)) or "(정의된 소스 없음)"
            raise ConfigError(
                f"'{name}' 소스를 설정에서 찾을 수 없습니다. "
                f"사용 가능한 소스: {available}"
            ) from None


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping or mapping[key] in (None, ""):
        raise ConfigError(f"{where}에 필수 항목 '{key}'가 없습니다.")
    return mapping[key]


def _as_number(value: Any, key: str, where: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}의 '{key}'는 숫자여야 합니다 (현재 값: {value!r}).")
    if value < minimum:
        raise ConfigError(f"{where}의 '{key}'는 {minimum} 이상이어야 합니다 (현재 값: {value!r}).")
    return float(value)


def _as_int(value: Any, key: str, where: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}의 '{key}'는 정수여야 합니다 (현재 값: {value!r}).")
    if value < minimum:
        raise ConfigError(f"{where}의 '{key}'는 {minimum} 이상이어야 합니다 (현재 값: {value!r}).")
    return value


def _parse_source(name: str, raw: Any) -> SourceConfig:
    where = f"sources.{name}"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}는 객체(JSON object)여야 합니다.")

    stype = _require(raw, "type", where)
    if stype not in SOURCE_TYPES:
        raise ConfigError(
            f"{where}의 'type'은 {SOURCE_TYPES} 중 하나여야 합니다 (현재 값: {stype!r})."
        )

    for key in _REQUIRED_SOURCE_KEYS[stype]:
        value = raw.get(key)
        if not value or (isinstance(value, str) and value.startswith("REPLACE_WITH")):
            raise ConfigError(
                f"{where}의 '{key}'가 비어 있거나 예시 자리표시자입니다. "
                f"config.example.json을 복사한 뒤 실제 값으로 바꾸세요."
            )

    params = raw.get("params") or {}
    if not isinstance(params, dict):
        raise ConfigError(f"{where}의 'params'는 객체(JSON object)여야 합니다.")

    auth_header_env = raw.get("auth_header_env") or {}
    if not isinstance(auth_header_env, dict):
        raise ConfigError(
            f"{where}의 'auth_header_env'는 '헤더이름: 환경변수이름' 형태의 객체여야 합니다."
        )
    for header, env_name in auth_header_env.items():
        if not isinstance(env_name, str) or not env_name.strip():
            raise ConfigError(
                f"{where}.auth_header_env.{header}에는 환경변수 '이름'을 적어야 합니다 "
                f"(비밀값 자체를 적으면 안 됩니다)."
            )

    known = {
        "type",
        "category",
        "url",
        "list_url",
        "base_url",
        "article_link_selector",
        "title_selector",
        "body_selector",
        "date_selector",
        "params",
        "auth_header_env",
    }
    return SourceConfig(
        name=name,
        type=stype,
        category=str(raw.get("category") or "unknown"),
        url=raw.get("url"),
        list_url=raw.get("list_url"),
        base_url=raw.get("base_url"),
        article_link_selector=raw.get("article_link_selector"),
        title_selector=raw.get("title_selector"),
        body_selector=raw.get("body_selector"),
        date_selector=raw.get("date_selector"),
        params={str(k): v for k, v in params.items()},
        auth_header_env={str(k): str(v).strip() for k, v in auth_header_env.items()},
        extra={k: v for k, v in raw.items() if k not in known},
    )


def parse_config(data: Any, *, config_path: str | None = None) -> AppConfig:
    """이미 읽어들인 dict를 AppConfig로 검증/변환한다."""
    where = "config"
    if not isinstance(data, dict):
        raise ConfigError("설정 파일 최상위는 JSON 객체여야 합니다.")

    database_path = str(_require(data, "database_path", where))
    log_path = str(_require(data, "log_path", where))

    sources_raw = data.get("sources")
    if not isinstance(sources_raw, dict) or not sources_raw:
        raise ConfigError("설정에 최소 하나 이상의 'sources' 항목이 필요합니다.")

    duplicate_policy = str(data.get("duplicate_policy", "skip"))
    if duplicate_policy not in DUPLICATE_POLICIES:
        raise ConfigError(
            f"'duplicate_policy'는 {DUPLICATE_POLICIES} 중 하나여야 합니다 "
            f"(현재 값: {duplicate_policy!r})."
        )

    timeout = _as_number(data.get("request_timeout_seconds", 10), "request_timeout_seconds", where, minimum=0.1)
    delay = _as_number(data.get("request_delay_seconds", 1.0), "request_delay_seconds", where, minimum=0.0)

    sources = {name: _parse_source(name, raw) for name, raw in sources_raw.items()}

    return AppConfig(
        database_path=database_path,
        log_path=log_path,
        request_timeout_seconds=timeout,
        request_delay_seconds=delay,
        duplicate_policy=duplicate_policy,
        default_ai_model=str(data.get("default_ai_model") or "gpt-4o-mini"),
        user_agent=str(data.get("user_agent") or DEFAULT_USER_AGENT),
        respect_robots_txt=bool(data.get("respect_robots_txt", True)),
        summary_language=str(data.get("summary_language") or "Korean"),
        max_analysis_articles=_as_int(data.get("max_analysis_articles", 50), "max_analysis_articles", where),
        max_analysis_chars=_as_int(data.get("max_analysis_chars", 20000), "max_analysis_chars", where),
        max_body_chars_for_ai=_as_int(data.get("max_body_chars_for_ai", 4000), "max_body_chars_for_ai", where),
        sources=sources,
        config_path=config_path,
    )


def load_config(path: str | os.PathLike[str] = DEFAULT_CONFIG_PATH) -> AppConfig:
    """설정 파일을 읽고 검증한다."""
    config_file = Path(path)
    if not config_file.is_file():
        raise ConfigError(
            f"설정 파일을 찾을 수 없습니다: {config_file}\n"
            f"'config.example.json'을 '{config_file}'로 복사한 뒤 값을 채우세요."
        )
    try:
        text = config_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"설정 파일을 읽을 수 없습니다: {config_file} ({exc})") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"설정 파일이 올바른 JSON이 아닙니다: {config_file} "
            f"(line {exc.lineno}, column {exc.colno}: {exc.msg})"
        ) from exc

    return parse_config(data, config_path=str(config_file))