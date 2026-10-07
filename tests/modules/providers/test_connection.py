"""SCRUM-75 SCRUM-116 / SCRUM-133: admin connection test and error category mapping.

Provider HTTP is httpx.MockTransport. No live network.
"""

import json
import uuid

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
        base_url=_BASE, model_name="gpt-test", api_key="bad-key"  # pragma: allowlist secret
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
        base_url=_BASE, model_name="gpt-test", api_key="bad-key"  # pragma: allowlist secret
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
        base_url=_BASE, model_name="no-such-model", api_key="key"  # pragma: allowlist secret
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
        base_url=_BASE, model_name="gpt-test", api_key="key"  # pragma: allowlist secret
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
        base_url=_BASE, model_name="gpt-test", api_key="key"  # pragma: allowlist secret
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
