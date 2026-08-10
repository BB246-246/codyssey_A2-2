"""AI 공급자 추상화.

공급자 종속 코드는 이 모듈에만 둔다. 다른 모듈은 `BaseAIClient.complete()`만 안다.
API 키는 오직 환경변수에서만 읽으며, 로그에 남기지 않는다.

환경변수
--------
AI_API_KEY   (필수) OpenAI 호환 API 키
AI_BASE_URL  (선택) OpenAI 호환 엔드포인트. 없으면 SDK 기본값
AI_MODEL     (선택) 모델명. 없으면 config의 default_ai_model
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Sequence

logger = logging.getLogger(__name__)

ENV_API_KEY = "AI_API_KEY"
ENV_BASE_URL = "AI_BASE_URL"
ENV_MODEL = "AI_MODEL"


class AIError(Exception):
    """AI 호출 실패."""


class AIConfigError(AIError):
    """AI 설정(키 등) 누락."""


class BaseAIClient:
    """모든 AI 클라이언트의 최소 인터페이스."""

    model: str = "unknown"

    def complete(self, system_prompt: str, user_prompt: str, *, json_mode: bool = False) -> str:
        raise NotImplementedError


class OpenAICompatibleClient(BaseAIClient):
    """OpenAI Python SDK를 사용하는 OpenAI 호환 클라이언트."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 60.0,
        temperature: float = 0.2,
    ):
        try:
            from openai import OpenAI  # 지연 임포트: 테스트는 SDK 없이도 돈다
        except ImportError as exc:  # pragma: no cover - 의존성 설치 안내
            raise AIConfigError(
                "openai 패키지가 설치되어 있지 않습니다. 'pip install -r requirements.txt'를 실행하세요."
            ) from exc

        if not api_key:
            raise AIConfigError(f"환경변수 {ENV_API_KEY}가 설정되어 있지 않습니다.")

        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
        if base_url:
            kwargs["base_url"] = base_url

        self._client = OpenAI(**kwargs)
        self.model = model
        self.temperature = temperature
        # 키 자체는 절대 로그에 남기지 않는다.
        logger.info("AI 클라이언트 준비 완료 (model=%s, base_url=%s)", model, base_url or "SDK 기본값")

    def complete(self, system_prompt: str, user_prompt: str, *, json_mode: bool = False) -> str:
        request: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if json_mode:
            request["response_format"] = {"type": "json_object"}

        try:
            response = self._client.chat.completions.create(**request)
        except Exception as exc:
            # 예외 메시지에 키가 섞이지 않도록 타입과 요약만 남긴다.
            raise AIError(f"AI 호출 실패 ({exc.__class__.__name__}): {exc}") from exc

        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, KeyError) as exc:
            raise AIError(f"AI 응답 형식이 예상과 다릅니다: {exc}") from exc

        if not content or not content.strip():
            raise AIError("AI가 빈 응답을 반환했습니다.")
        return content.strip()


class FakeAIClient(BaseAIClient):
    """테스트용 클라이언트. 실제 네트워크를 사용하지 않는다.

    `responses`: 순서대로 반환할 문자열 목록. 항목이 Exception이면 raise한다.
    `handler`: (system, user, json_mode) -> str 콜러블. 지정하면 responses보다 우선한다.
    """

    def __init__(
        self,
        responses: Sequence[str | Exception] | None = None,
        *,
        handler: Callable[[str, str, bool], str] | None = None,
        model: str = "fake-model",
    ):
        self._responses = list(responses or [])
        self._handler = handler
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def complete(self, system_prompt: str, user_prompt: str, *, json_mode: bool = False) -> str:
        self.calls.append(
            {"system": system_prompt, "user": user_prompt, "json_mode": json_mode}
        )
        if self._handler is not None:
            return self._handler(system_prompt, user_prompt, json_mode)
        if not self._responses:
            raise AIError("FakeAIClient에 남은 응답이 없습니다.")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def resolve_model(config: Any = None, override: str | None = None) -> str:
    """모델명 결정 우선순위: CLI 인자 > AI_MODEL > config.default_ai_model."""
    if override:
        return override
    env_model = os.environ.get(ENV_MODEL)
    if env_model:
        return env_model
    if config is not None and getattr(config, "default_ai_model", None):
        return str(config.default_ai_model)
    return "gpt-4o-mini"


def build_ai_client(config: Any = None, *, model: str | None = None) -> BaseAIClient:
    """환경변수를 읽어 실제 AI 클라이언트를 만든다.

    키가 없으면 AIConfigError를 던진다(사람이 읽을 수 있는 안내 포함).
    """
    api_key = os.environ.get(ENV_API_KEY, "").strip()
    if not api_key:
        raise AIConfigError(
            f"환경변수 {ENV_API_KEY}가 필요합니다.\n"
            f"  PowerShell:  $env:{ENV_API_KEY} = 'sk-...'\n"
            f"  bash:        export {ENV_API_KEY}='sk-...'\n"
            f"선택적으로 {ENV_BASE_URL}, {ENV_MODEL}도 설정할 수 있습니다."
        )

    base_url = os.environ.get(ENV_BASE_URL, "").strip() or None
    timeout = float(getattr(config, "request_timeout_seconds", 10.0) or 10.0) * 6
    return OpenAICompatibleClient(
        api_key=api_key,
        model=resolve_model(config, model),
        base_url=base_url,
        timeout=max(timeout, 30.0),
    )