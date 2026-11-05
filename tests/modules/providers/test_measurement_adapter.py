"""Worker secrets stay inside providers.service and are scrubbed from all evidence."""

from types import SimpleNamespace

import pytest

from app.modules.providers import service
from app.modules.providers.adapters import dispatch
from app.modules.providers.adapters.base import ProviderResponse
from app.modules.providers.crypto import CredentialError
from app.shared.exceptions import InternalError


def test_adapter_uses_frozen_settings_and_scrubs_response(monkeypatch):
    config = SimpleNamespace(
        product_id="product",
        provider_type="openai_compatible",
        base_url="https://private.example",
        model_name="frozen-model",
    )
    monkeypatch.setattr(
        service, "_wajib_ada", lambda *_: SimpleNamespace(credential_encrypted=b"encrypted")
    )
    monkeypatch.setattr(service, "decrypt_credential", lambda *_: "secret-token")

    class Adapter:
        def __init__(self, **kwargs):
            assert kwargs == {
                "base_url": config.base_url,
                "model_name": "frozen-model",
                "api_key": "secret-token",  # pragma: allowlist secret — synthetic scrub-test value
            }

        def ask(self, prompt, **kwargs):
            return ProviderResponse(
                text="secret-token text",
                model_version="https://private.example v1",
                latency_ms=1,
                raw={"nested": ["secret-token", config.base_url]},
                error="secret-token " + config.base_url,
            )

    monkeypatch.setitem(dispatch._ADAPTERS, "openai_compatible", Adapter)
    response = service.build_measurement_adapter(None, config).ask("question")
    assert "secret-token" not in repr(response) and config.base_url not in repr(response)
    assert response.text == " text"


def test_decryption_failure_is_safe(monkeypatch):
    monkeypatch.setattr(
        service, "_wajib_ada", lambda *_: SimpleNamespace(credential_encrypted=b"bad")
    )

    def fail(*_):
        raise CredentialError("secret-token")

    monkeypatch.setattr(service, "decrypt_credential", fail)
    with pytest.raises(InternalError, match="credential unavailable") as error:
        service.build_measurement_adapter(None, SimpleNamespace(product_id="id"))
    assert "secret-token" not in str(error.value)


def test_unexpected_provider_exception_is_recorded_without_secret():
    class Broken:
        def ask(self, *_, **kwargs):
            raise RuntimeError("secret-token https://private.example")

    result = service._SafeMeasurementAdapter(
        Broken(), "secret-token", "https://private.example"
    ).ask("q")
    assert result.failure_kind == "provider_error"
    assert "secret-token" not in repr(result) and "private.example" not in repr(result)


def test_unknown_provider_is_rejected_without_constructing_adapter():
    with pytest.raises(ValueError, match="unsupported"):
        dispatch.build_adapter(provider_type="unknown", base_url="", model_name="", api_key="")
