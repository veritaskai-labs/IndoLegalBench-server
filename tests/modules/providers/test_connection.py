"""SCRUM-75 SCRUM-116 / SCRUM-133: admin connection test and error category mapping.

Provider HTTP is httpx.MockTransport. No live network.
"""

import json
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from cryptography.fernet import Fernet

from app.modules.auth.seeds import ADMIN_SUB, AUTHOR_SUB
from app.modules.providers.adapters import http as provider_http
from app.modules.providers.adapters.anthropic_messages import AnthropicMessagesAdapter
from app.modules.providers.adapters.gemini_interactions import GeminiInteractionsAdapter
from app.modules.providers.adapters.http import classify_error
from app.modules.providers.adapters.openai_compatible import OpenAICompatibleAdapter
from app.modules.providers.models import AiProduct, ConnectionTestErrorCategory, LastTestStatus
from app.shared.config import get_settings
from tests.login import complete_login

_SECRET = "connection-test-token-4nQs"  # pragma: allowlist secret
_BASE = "https://api.example.test/v1/chat/completions"


@pytest.fixture
def encryption_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


def _payload(**override) -> dict:
    body = {
        "name": "AiYU",
        "provider_type": "openai_compatible",
        "base_url": _BASE,
        "model_name": "aiyu-1",
        "credential": _SECRET,
        "rate_limit_per_minute": 30,
        "monthly_budget_idr": "1500000.00",
    }
    body.update(override)
    return body


def _mock_transport(monkeypatch, handler):
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(provider_http.httpx, "Client", factory)


def test_openai_success_stores_ok_and_posts_the_saved_url(
    client, db_session, encryption_key, monkeypatch
):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})

    _mock_transport(monkeypatch, handler)
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload())
    assert created.status_code == 201
    product_id = created.json()["id"]

    response = client.post(f"/admin/providers/{product_id}/test-connection")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["latency_ms"], int)
    assert _SECRET not in response.text

    db_session.expire_all()
    stored = db_session.get(AiProduct, uuid.UUID(product_id))
    assert seen["url"] == stored.base_url
    assert stored.last_test_status == LastTestStatus.OK
    assert stored.last_test_at is not None
    assert seen["body"]["model"] == "aiyu-1"
    assert seen["body"]["max_tokens"] == 1
    assert seen["body"]["temperature"] == 0
    assert _SECRET.encode() not in json.dumps(seen["body"]).encode()


def test_provider_401_is_stored_as_failed(client, db_session, encryption_key, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    _mock_transport(monkeypatch, handler)
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload(name="Denied"))
    product_id = created.json()["id"]

    response = client.post(f"/admin/providers/{product_id}/test-connection")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    db_session.expire_all()
    stored = db_session.get(AiProduct, uuid.UUID(product_id))
    assert stored.last_test_status == LastTestStatus.FAILED
    assert stored.last_test_at is not None


def test_timeout_is_stored_as_failed(client, db_session, encryption_key, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    _mock_transport(monkeypatch, handler)
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload(name="Slow"))
    product_id = created.json()["id"]

    response = client.post(f"/admin/providers/{product_id}/test-connection")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    db_session.expire_all()
    stored = db_session.get(AiProduct, uuid.UUID(product_id))
    assert stored.last_test_status == LastTestStatus.FAILED


def test_credential_is_absent_from_response_and_stored_message(
    client, db_session, encryption_key, monkeypatch
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"rejected {_SECRET}")

    _mock_transport(monkeypatch, handler)
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload(name="Leaky"))
    product_id = created.json()["id"]

    response = client.post(f"/admin/providers/{product_id}/test-connection")

    assert response.status_code == 200
    assert _SECRET not in response.text
    db_session.expire_all()
    stored = db_session.get(AiProduct, uuid.UUID(product_id))
    assert stored.last_test_message is not None
    assert _SECRET not in stored.last_test_message


def test_unknown_id_is_404_and_author_is_403(client, db_session, encryption_key):
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload(name="Untested"))
    assert created.status_code == 201
    product_id = created.json()["id"]

    missing = client.post(f"/admin/providers/{uuid.uuid4()}/test-connection")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"

    complete_login(client, db_session, AUTHOR_SUB)
    forbidden = client.post(f"/admin/providers/{product_id}/test-connection")
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "FORBIDDEN"

    db_session.expire_all()
    stored = db_session.get(AiProduct, uuid.UUID(product_id))
    assert stored.last_test_status is None
    assert stored.last_test_at is None
    assert stored.last_test_message is None


def test_gemini_adapter_sends_api_key_and_does_not_rewrite_url(monkeypatch):
    url = "https://generativelanguage.googleapis.com/v1beta/interactions"
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["x-goog-api-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "interaction-1"})

    _mock_transport(monkeypatch, handler)
    result = GeminiInteractionsAdapter(
        base_url=url,
        model_name="gemini-2.5-flash",
        api_key="gemini-test-key",  # pragma: allowlist secret
    ).test_connection()

    assert result.status == "ok"
    assert seen["url"] == url
    assert seen["key"] == "gemini-test-key"
    assert seen["body"] == {
        "model": "gemini-2.5-flash",
        "input": "ping",
        "generation_config": {"max_output_tokens": 1},
    }


def test_gemini_401_returns_access_denied_category(monkeypatch):
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_api_key"})

    _mock_transport(monkeypatch, handler)

    # Act
    result = GeminiInteractionsAdapter(
        base_url="https://generativelanguage.googleapis.com/v1beta/interactions",
        model_name="gemini-test",
        api_key="bad-key",  # pragma: allowlist secret
    ).test_connection()

    # Assert
    assert result.status == "failed"
    assert result.error_category == "access_denied"


def test_gemini_timeout_returns_timeout_category(monkeypatch):
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    _mock_transport(monkeypatch, handler)

    # Act
    result = GeminiInteractionsAdapter(
        base_url="https://generativelanguage.googleapis.com/v1beta/interactions",
        model_name="gemini-test",
        api_key="key",  # pragma: allowlist secret
    ).test_connection()

    # Assert
    assert result.status == "failed"
    assert result.error_category == "timeout"


def test_gemini_invalid_body_returns_unknown_category(monkeypatch):
    # Arrange: response 200 but not a valid JSON object
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    _mock_transport(monkeypatch, handler)

    # Act
    result = GeminiInteractionsAdapter(
        base_url="https://generativelanguage.googleapis.com/v1beta/interactions",
        model_name="gemini-test",
        api_key="key",  # pragma: allowlist secret
    ).test_connection()

    # Assert
    assert result.status == "failed"
    assert result.error_category == "unknown"


def test_anthropic_401_returns_access_denied_category(monkeypatch):
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"type": "authentication_error"}})

    _mock_transport(monkeypatch, handler)

    # Act
    result = AnthropicMessagesAdapter(
        base_url="https://api.anthropic.com/v1/messages",
        model_name="claude-test",
        api_key="bad-key",  # pragma: allowlist secret
    ).test_connection()

    # Assert
    assert result.status == "failed"
    assert result.error_category == "access_denied"


def test_anthropic_timeout_returns_timeout_category(monkeypatch):
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    _mock_transport(monkeypatch, handler)

    # Act
    result = AnthropicMessagesAdapter(
        base_url="https://api.anthropic.com/v1/messages",
        model_name="claude-test",
        api_key="key",  # pragma: allowlist secret
    ).test_connection()

    # Assert
    assert result.status == "failed"
    assert result.error_category == "timeout"


def test_anthropic_invalid_body_returns_unknown_category(monkeypatch):
    # Arrange: 200 but no "content" list
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "msg-1", "type": "message"})

    _mock_transport(monkeypatch, handler)

    # Act
    result = AnthropicMessagesAdapter(
        base_url="https://api.anthropic.com/v1/messages",
        model_name="claude-test",
        api_key="key",  # pragma: allowlist secret
    ).test_connection()

    # Assert
    assert result.status == "failed"
    assert result.error_category == "unknown"


def test_claude_adapter_sends_version_header_and_does_not_rewrite_url(monkeypatch):
    url = "https://api.anthropic.com/v1/messages"
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["x-api-key"]
        seen["version"] = request.headers["anthropic-version"]
        seen["content_type"] = request.headers["content-type"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": []})

    _mock_transport(monkeypatch, handler)
    result = AnthropicMessagesAdapter(
        base_url=url,
        model_name="claude-sonnet",
        api_key="claude-test-key",  # pragma: allowlist secret
    ).test_connection()

    assert result.status == "ok"
    assert seen["url"] == url
    assert seen["key"] == "claude-test-key"
    assert seen["version"] == "2023-06-01"
    assert seen["content_type"].startswith("application/json")
    assert seen["body"] == {
        "model": "claude-sonnet",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }


# --- SCRUM-133: error category mapping ---


@pytest.mark.parametrize(
    "transport_error,expected",
    [
        ("connection timed out", "timeout"),
        ("read timed out", "timeout"),
        ("could not connect", "unreachable"),
        ("connection failed", "unknown"),
        (None, "unknown"),
    ],
)
def test_classify_error_transport(transport_error, expected):
    # Arrange / Act
    category = classify_error(transport_error=transport_error)
    # Assert
    assert category == expected


@pytest.mark.parametrize(
    "status_code,expected",
    [
        (401, "access_denied"),
        (403, "access_denied"),
        (404, "model_not_found"),
        (500, "unknown"),
        (429, "unknown"),
    ],
)
def test_classify_error_http_status(status_code, expected):
    # Arrange / Act
    category = classify_error(status_code=status_code)
    # Assert
    assert category == expected


def test_openai_401_returns_access_denied_category(monkeypatch):
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_api_key"})

    _mock_transport(monkeypatch, handler)

    # Act
    result = OpenAICompatibleAdapter(
        base_url=_BASE,
        model_name="gpt-test",
        api_key="bad-key",  # pragma: allowlist secret
    ).test_connection()

    # Assert
    assert result.status == "failed"
    assert result.error_category == "access_denied"


def test_openai_403_returns_access_denied_category(monkeypatch):
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    _mock_transport(monkeypatch, handler)

    # Act
    result = OpenAICompatibleAdapter(
        base_url=_BASE,
        model_name="gpt-test",
        api_key="bad-key",  # pragma: allowlist secret
    ).test_connection()

    # Assert
    assert result.status == "failed"
    assert result.error_category == "access_denied"


def test_openai_404_returns_model_not_found_category(monkeypatch):
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model_not_found"})

    _mock_transport(monkeypatch, handler)

    # Act
    result = OpenAICompatibleAdapter(
        base_url=_BASE,
        model_name="no-such-model",
        api_key="key",  # pragma: allowlist secret
    ).test_connection()

    # Assert
    assert result.status == "failed"
    assert result.error_category == "model_not_found"


def test_openai_timeout_returns_timeout_category(monkeypatch):
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    _mock_transport(monkeypatch, handler)

    # Act
    result = OpenAICompatibleAdapter(
        base_url=_BASE,
        model_name="gpt-test",
        api_key="key",  # pragma: allowlist secret
    ).test_connection()

    # Assert
    assert result.status == "failed"
    assert result.error_category == "timeout"


def test_openai_connect_error_returns_unreachable_category(monkeypatch):
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("could not connect")

    _mock_transport(monkeypatch, handler)

    # Act
    result = OpenAICompatibleAdapter(
        base_url=_BASE,
        model_name="gpt-test",
        api_key="key",  # pragma: allowlist secret
    ).test_connection()

    # Assert
    assert result.status == "failed"
    assert result.error_category == "unreachable"


def test_connection_test_endpoint_returns_error_category(
    client, db_session, encryption_key, monkeypatch
):
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    _mock_transport(monkeypatch, handler)
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload(name="CategoryTest"))
    product_id = created.json()["id"]

    # Act
    response = client.post(f"/admin/providers/{product_id}/test-connection")

    # Assert
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "failed"
    assert body["error_category"] == "access_denied"

    # kategori juga tersimpan di DB dan muncul di GET produk
    db_session.expire_all()
    stored = db_session.get(AiProduct, uuid.UUID(product_id))
    assert stored.last_test_error_category == ConnectionTestErrorCategory.ACCESS_DENIED

    get_resp = client.get(f"/admin/providers/{product_id}")
    assert get_resp.json()["last_test_error_category"] == "access_denied"


def test_api_key_is_not_stored_in_last_test_message(
    client, db_session, encryption_key, monkeypatch
):
    # Arrange: provider returns the API key verbatim in the error body
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"rejected token {_SECRET}")

    _mock_transport(monkeypatch, handler)
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload(name="KeyLeakCheck"))
    product_id = created.json()["id"]

    # Act
    response = client.post(f"/admin/providers/{product_id}/test-connection")

    # Assert: API key absent from HTTP response
    assert response.status_code == 200
    assert _SECRET not in response.text

    # Assert: API key absent from what is stored in last_test_message
    db_session.expire_all()
    stored = db_session.get(AiProduct, uuid.UUID(product_id))
    assert stored.last_test_message is not None
    assert _SECRET not in stored.last_test_message
    # category is still set correctly despite sanitization
    assert stored.last_test_error_category == ConnectionTestErrorCategory.ACCESS_DENIED


def test_successful_test_clears_error_category(client, db_session, encryption_key, monkeypatch):
    # Arrange: gunakan satu mock transport dengan handler yang bisa diganti
    responses = {"handler": lambda req: httpx.Response(401, json={"error": "unauthorized"})}

    def dispatch(request: httpx.Request) -> httpx.Response:
        return responses["handler"](request)

    _mock_transport(monkeypatch, dispatch)
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload(name="ClearCategory"))
    product_id = created.json()["id"]

    # tes pertama gagal
    client.post(f"/admin/providers/{product_id}/test-connection")

    # ganti handler: tes sekarang berhasil
    responses["handler"] = lambda req: httpx.Response(
        200, json={"choices": [{"message": {"content": ""}}]}
    )

    # Act
    response = client.post(f"/admin/providers/{product_id}/test-connection")

    # Assert
    body = response.json()
    assert body["status"] == "ok"
    assert body.get("error_category") is None

    db_session.expire_all()
    stored = db_session.get(AiProduct, uuid.UUID(product_id))
    assert stored.last_test_error_category is None


def _wrong_key(request: httpx.Request) -> httpx.Response:
    return httpx.Response(401, json={"error": "invalid api key"})


def _wrong_model(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, json={"error": "model not found"})


def _host_down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("name or service not known", request=request)


def _slow_server(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("read timed out", request=request)


@pytest.mark.parametrize(
    "provider_type", ["openai_compatible", "gemini_interactions", "anthropic_messages"]
)
@pytest.mark.parametrize(
    ("provider_reply", "category"),
    [
        pytest.param(_wrong_key, "access_denied", id="api-key-salah"),
        pytest.param(_wrong_model, "model_not_found", id="model-salah"),
        pytest.param(_host_down, "unreachable", id="url-host-mati"),
        pytest.param(_slow_server, "timeout", id="server-lambat"),
    ],
)
def test_failed_connection_test_reports_the_scenario_category(
    client, db_session, encryption_key, monkeypatch, provider_type, provider_reply, category
):
    _mock_transport(monkeypatch, provider_reply)
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post(
        "/admin/providers",
        json=_payload(name=f"Retest {provider_type}", provider_type=provider_type),
    )
    product_id = created.json()["id"]

    tested = client.post(f"/admin/providers/{product_id}/test-connection")
    reopened = client.get(f"/admin/providers/{product_id}")

    assert tested.status_code == 200
    assert tested.json()["status"] == "failed"
    assert tested.json()["error_category"] == category
    assert reopened.json()["last_test_status"] == "failed"
    assert reopened.json()["last_test_error_category"] == category


_SLOW_REPLY_SECONDS = 2.0
_SHORT_READ_TIMEOUT = httpx.Timeout(15.0, read=0.3)


class _SilentUntilTooLate(BaseHTTPRequestHandler):
    def do_POST(self):
        time.sleep(_SLOW_REPLY_SECONDS)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def slow_provider_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SilentUntilTooLate)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    server.shutdown()
    server.server_close()


@pytest.fixture
def closed_port_url():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/v1/chat/completions"


def _run_connection_test(client, db_session, base_url: str) -> dict:
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload(name="Real", base_url=base_url))
    return client.post(f"/admin/providers/{created.json()['id']}/test-connection").json()


def test_real_provider_answering_too_late_reads_as_timeout(
    client, db_session, encryption_key, monkeypatch, slow_provider_url
):
    monkeypatch.setattr(provider_http, "TIMEOUT", _SHORT_READ_TIMEOUT)

    result = _run_connection_test(client, db_session, slow_provider_url)

    assert result["status"] == "failed"
    assert result["error_category"] == "timeout"


def test_real_closed_port_reads_as_unreachable(client, db_session, encryption_key, closed_port_url):
    result = _run_connection_test(client, db_session, closed_port_url)

    assert result["status"] == "failed"
    assert result["error_category"] == "unreachable"


def test_timeout_matches_the_15_seconds_the_admin_is_told():
    assert httpx.Timeout(15.0) == provider_http.TIMEOUT


def _error_status_echoing_key(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, text=f"model not found for key {_SECRET}")


def _unknown_body_echoing_key(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=f"<html>key {_SECRET} accepted</html>")


@pytest.mark.parametrize(
    "provider_type", ["openai_compatible", "gemini_interactions", "anthropic_messages"]
)
@pytest.mark.parametrize(
    "provider_reply",
    [
        pytest.param(_error_status_echoing_key, id="status-gagal"),
        pytest.param(_unknown_body_echoing_key, id="badan-tak-dikenal"),
    ],
)
def test_key_echoed_by_the_provider_never_leaves_the_server(
    client, db_session, encryption_key, monkeypatch, provider_type, provider_reply
):
    _mock_transport(monkeypatch, provider_reply)
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post(
        "/admin/providers",
        json=_payload(name=f"Echo {provider_type}", provider_type=provider_type),
    )
    product_id = created.json()["id"]

    tested = client.post(f"/admin/providers/{product_id}/test-connection")
    reopened = client.get(f"/admin/providers/{product_id}")
    listed = client.get("/admin/providers")

    db_session.expire_all()
    stored = db_session.get(AiProduct, uuid.UUID(product_id))
    assert tested.json()["status"] == "failed"
    assert "key" in stored.last_test_message
    for leaked_into in (tested.text, reopened.text, listed.text, stored.last_test_message):
        assert _SECRET not in leaked_into


class _KeyEchoingProvider(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        sent = (
            self.headers.get("Authorization")
            or self.headers.get("x-api-key")
            or self.headers.get("x-goog-api-key")
            or ""
        )
        token = sent.removeprefix("Bearer").strip()
        body = json.dumps({"error": {"invalid_key": token}}).encode()
        self.send_response(401)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def key_echoing_provider_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _KeyEchoingProvider)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    server.shutdown()
    server.server_close()


def _registered_with(client, base_url: str, provider_type: str, key: str) -> str:
    created = client.post(
        "/admin/providers",
        json=_payload(
            name=f"Pasted {provider_type}",
            provider_type=provider_type,
            base_url=base_url,
            credential=key,
        ),
    )
    return created.json()["id"]


def _replaced_with(client, base_url: str, provider_type: str, key: str) -> str:
    product_id = _registered_with(client, base_url, provider_type, _SECRET)
    replaced = client.patch(f"/admin/providers/{product_id}", json={"credential": key})
    assert replaced.status_code == 200
    return product_id


@pytest.mark.parametrize(
    "provider_type", ["openai_compatible", "gemini_interactions", "anthropic_messages"]
)
@pytest.mark.parametrize(
    "pasted_key",
    [
        pytest.param(f"{_SECRET} ", id="spasi-belakang"),
        pytest.param(f" {_SECRET}", id="spasi-depan"),
        pytest.param(f"\t{_SECRET}", id="tab-depan"),
    ],
)
@pytest.mark.parametrize(
    "enter_key",
    [
        pytest.param(_registered_with, id="daftar"),
        pytest.param(_replaced_with, id="ubah"),
    ],
)
def test_key_pasted_with_surrounding_whitespace_behaves_like_the_clean_key(
    client,
    db_session,
    encryption_key,
    key_echoing_provider_url,
    provider_type,
    pasted_key,
    enter_key,
):
    complete_login(client, db_session, ADMIN_SUB)
    product_id = enter_key(client, key_echoing_provider_url, provider_type, pasted_key)

    tested = client.post(f"/admin/providers/{product_id}/test-connection")
    reopened = client.get(f"/admin/providers/{product_id}")

    db_session.expire_all()
    stored = db_session.get(AiProduct, uuid.UUID(product_id))
    assert tested.json()["error_category"] == "access_denied"
    for leaked_into in (tested.text, reopened.text, stored.last_test_message):
        assert _SECRET not in leaked_into
