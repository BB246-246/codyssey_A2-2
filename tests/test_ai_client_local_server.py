"""OpenAI 호환 SDK 경로를 로컬 stub 서버로 검증한다.

외부 네트워크와 실제 API 키 없이(127.0.0.1 전용) `OpenAICompatibleClient`가
정말로 HTTP 요청을 만들고 응답을 파싱하는지 확인한다.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from news_cli.ai_client import AIError, ENV_API_KEY, ENV_BASE_URL, build_ai_client

_received: list[dict] = []
_mode = {"value": "ok"}


class _StubHandler(BaseHTTPRequestHandler):
    """최소한의 /v1/chat/completions 구현."""

    def log_message(self, *args):  # 테스트 출력 오염 방지
        pass

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        _received.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})

        if _mode["value"] == "error":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error": {"message": "boom"}}')
            return

        content = "요약된 한국어 문장입니다." if _mode["value"] == "ok" else ""
        payload = {
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "stub"),
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def stub_server():
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def client(stub_server, monkeypatch, app_config):
    _received.clear()
    _mode["value"] = "ok"
    monkeypatch.setenv(ENV_API_KEY, "sk-test-local-only")
    monkeypatch.setenv(ENV_BASE_URL, stub_server)
    return build_ai_client(app_config)


def test_real_sdk_path_returns_content(client):
    result = client.complete("시스템 프롬프트", "사용자 프롬프트")
    assert result == "요약된 한국어 문장입니다."

    request = _received[-1]
    assert request["path"].endswith("/chat/completions")
    assert request["body"]["model"] == "test-model"
    assert request["body"]["messages"][0]["role"] == "system"
    assert request["auth"] == "Bearer sk-test-local-only"


def test_json_mode_sets_response_format(client):
    client.complete("s", "u", json_mode=True)
    assert _received[-1]["body"]["response_format"] == {"type": "json_object"}


def test_server_error_becomes_ai_error(client):
    _mode["value"] = "error"
    with pytest.raises(AIError):
        client.complete("s", "u")


def test_empty_content_becomes_ai_error(client):
    _mode["value"] = "empty"
    with pytest.raises(AIError) as excinfo:
        client.complete("s", "u")
    assert "빈 응답" in str(excinfo.value)


def test_summarize_end_to_end_over_http(client, storage):
    """summarize 파이프라인 전체가 실제 HTTP 왕복으로 동작하는지 확인."""
    from news_cli.cleaner import clean_articles
    from news_cli.summarizer import summarize_articles
    from tests.conftest import make_raw

    make_raw(storage, url="https://example.test/http", title="기사", body="본문 내용")
    clean_articles(storage, duplicate_policy="skip")

    stats = summarize_articles(storage, client, mode="unsummarized")
    assert stats.succeeded == 1
    row = storage.query_clean_articles(status="summarized")[0]
    assert row["summary"] == "요약된 한국어 문장입니다."
    assert row["summary_model"] == "test-model"