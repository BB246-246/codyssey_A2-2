"""수집기 공통 HTTP 계층.

- 명시적 timeout
- 식별 가능한 User-Agent
- 요청 사이 delay
- robots.txt 확인 (기본 활성화, 차단 우회는 하지 않는다)
"""

from __future__ import annotations

import logging
import os
import time
import urllib.robotparser as robotparser
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

logger = logging.getLogger(__name__)


class FetchError(Exception):
    """네트워크/HTTP 단계에서 발생한 복구 가능한 오류."""


class RobotsDisallowed(FetchError):
    """robots.txt가 해당 URL 수집을 금지한 경우."""


@dataclass
class CollectedItem:
    """수집기가 돌려주는 정규화 이전의 항목."""

    url: str
    title: str | None = None
    body: str | None = None
    published_at: Any = None
    external_id: str | None = None
    category: str | None = None
    source_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self, source_name: str) -> dict[str, Any]:
        payload = {
            "source": source_name,
            "url": self.url,
            "title": self.title,
            "body": self.body,
            "published_at": self.published_at,
            "category": self.category,
            "external_id": self.external_id,
        }
        payload.update(self.extra)
        return payload


@dataclass
class CollectResult:
    """수집 결과 묶음."""

    items: list[CollectedItem] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (url, message)
    attempted: int = 0


class HttpFetcher:
    """delay/timeout/robots를 지키는 얇은 HTTP 래퍼."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = 10.0,
        delay: float = 1.0,
        respect_robots: bool = True,
        session: requests.Session | None = None,
    ):
        self.user_agent = user_agent
        self.timeout = float(timeout)
        self.delay = float(delay)
        self.respect_robots = bool(respect_robots)
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self._last_request_at: float | None = None
        self._robots_cache: dict[str, robotparser.RobotFileParser | None] = {}

    # -- robots -------------------------------------------------------------
    def _robots_for(self, url: str) -> robotparser.RobotFileParser | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots_cache:
            return self._robots_cache[origin]

        parser: robotparser.RobotFileParser | None = None
        robots_url = urljoin(origin, "/robots.txt")
        try:
            response = self.session.get(robots_url, timeout=self.timeout)
            if response.status_code == 200:
                parser = robotparser.RobotFileParser()
                parser.parse(response.text.splitlines())
            else:
                logger.info("robots.txt 응답 %s (%s) - 제한 없음으로 간주", response.status_code, robots_url)
        except requests.RequestException as exc:
            logger.warning("robots.txt를 읽지 못했습니다(%s): %s", robots_url, exc)

        self._robots_cache[origin] = parser
        return parser

    def is_allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parser = self._robots_for(url)
        if parser is None:
            return True
        return bool(parser.can_fetch(self.user_agent, url))

    def robots_delay(self, url: str) -> float | None:
        if not self.respect_robots:
            return None
        parser = self._robots_for(url)
        if parser is None:
            return None
        try:
            return parser.crawl_delay(self.user_agent)
        except Exception:  # pragma: no cover - 표준 라이브러리 방어
            return None

    # -- 요청 ---------------------------------------------------------------
    def _sleep_if_needed(self, extra_delay: float = 0.0) -> None:
        delay = max(self.delay, extra_delay)
        if delay <= 0 or self._last_request_at is None:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = delay - elapsed
        if remaining > 0:
            logger.debug("요청 간 대기 %.2fs", remaining)
            time.sleep(remaining)

    def get(
        self,
        url: str,
        *,
        respect_delay: bool = True,
        headers: dict[str, str] | None = None,
        check_robots: bool = True,
    ) -> requests.Response:
        """GET 요청. 실패 시 FetchError를 던진다.

        `headers`는 인증 헤더처럼 요청별로 다른 값을 넘길 때 사용한다.
        값은 로그에 남기지 않는다(헤더 이름만 기록).

        `check_robots=False`는 robots.txt 확인을 건너뛴다. robots.txt는 크롤러를
        대상으로 한 규약이므로, 제공자가 발급한 자격증명으로 호출하는 공식 API
        엔드포인트에는 적용하지 않는다(그쪽은 API 이용약관을 따른다).
        크롤링 경로에서는 절대 False로 두지 않는다.
        """
        if check_robots and not self.is_allowed(url):
            raise RobotsDisallowed(f"robots.txt가 수집을 허용하지 않습니다: {url}")

        if respect_delay:
            self._sleep_if_needed()

        if headers:
            logger.debug("요청 헤더 추가: %s", ", ".join(sorted(headers)))
        logger.debug("GET %s (timeout=%.1fs)", url, self.timeout)
        try:
            response = self.session.get(url, timeout=self.timeout, headers=headers or None)
        except requests.Timeout as exc:
            raise FetchError(f"요청 시간 초과({self.timeout}s): {url}") from exc
        except requests.RequestException as exc:
            raise FetchError(f"HTTP 요청 실패: {url} ({exc.__class__.__name__}: {exc})") from exc
        finally:
            self._last_request_at = time.monotonic()

        if response.status_code >= 400:
            # API는 실패 원인을 본문에 담아 준다. 그대로 버리면 401만 보이고
            # 무엇이 잘못됐는지 알 수 없으므로 앞부분을 함께 알린다.
            detail = ""
            try:
                if not response.encoding or response.encoding.lower() == "iso-8859-1":
                    response.encoding = response.apparent_encoding or "utf-8"
                snippet = " ".join((response.text or "").split())[:200]
                if snippet:
                    detail = f" | 응답: {snippet}"
            except Exception:  # pragma: no cover - 본문 읽기 실패는 무시
                pass
            raise FetchError(f"HTTP {response.status_code} 응답: {url}{detail}")
        return response

    def get_text(
        self,
        url: str,
        *,
        respect_delay: bool = True,
        headers: dict[str, str] | None = None,
        check_robots: bool = True,
    ) -> str:
        response = self.get(
            url, respect_delay=respect_delay, headers=headers, check_robots=check_robots
        )
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        return response.text

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:  # pragma: no cover
            pass


def _looks_like_placeholder(value: str) -> bool:
    """'<Client ID>'처럼 문서의 예시 문구를 그대로 넣은 경우를 감지한다."""
    stripped = value.strip()
    if stripped.startswith("<") and stripped.endswith(">"):
        return True
    return stripped.upper().startswith(("REPLACE_WITH", "YOUR_", "<발급"))


def resolve_auth_headers(source: Any) -> dict[str, str]:
    """source.auth_header_env의 '환경변수 이름'을 실제 값으로 바꾼다.

    비밀값은 config가 아니라 환경변수에만 존재한다. 값이 없으면 어떤 변수를
    설정해야 하는지 알려주는 FetchError를 던진다(값 자체는 절대 로그·예외에 넣지 않는다).
    """
    mapping = getattr(source, "auth_header_env", None) or {}
    headers: dict[str, str] = {}
    missing: list[str] = []
    placeholder: list[str] = []

    for header, env_name in mapping.items():
        value = os.environ.get(env_name, "").strip()
        if not value:
            missing.append(env_name)
        elif _looks_like_placeholder(value):
            placeholder.append(env_name)
        else:
            headers[header] = value

    if placeholder:
        raise FetchError(
            f"환경변수에 실제 값 대신 예시 자리표시자가 들어 있습니다: {', '.join(placeholder)}\n"
            f"'<...>' 꺾쇠는 '여기에 발급받은 값을 넣으라'는 표시입니다. 꺾쇠 없이 값만 넣으세요.\n"
            f"  예)  setx {placeholder[0]} abcd1234EFGH5678ijkl"
        )

    if missing:
        names = ", ".join(missing)
        raise FetchError(
            f"소스 '{getattr(source, 'name', '?')}'에 필요한 환경변수가 설정되어 있지 않습니다: {names}\n"
            f"  PowerShell:  setx {missing[0]} 발급받은값     (꺾쇠 없이 실제 값만)\n"
            f"  bash:        export {missing[0]}=발급받은값\n"
            f"setx로 설정했다면 터미널만 새로 여는 것으로는 부족합니다. "
            f"VS Code 등 편집기를 쓰는 경우 편집기를 완전히 재시작해야 반영됩니다."
        )

    if headers:
        logger.info("인증 헤더 %d개를 환경변수에서 읽었습니다: %s", len(headers), ", ".join(sorted(headers)))
    return headers


def build_fetcher(config: Any, *, session: requests.Session | None = None) -> HttpFetcher:
    """AppConfig에서 HttpFetcher를 만든다."""
    return HttpFetcher(
        user_agent=config.user_agent,
        timeout=config.request_timeout_seconds,
        delay=config.request_delay_seconds,
        respect_robots=config.respect_robots_txt,
        session=session,
    )