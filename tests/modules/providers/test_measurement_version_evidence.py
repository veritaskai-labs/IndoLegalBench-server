"""Configured aliases cannot stand in for a provider-observed version."""

from types import SimpleNamespace

import pytest

from app.modules.providers.adapters.base import ProviderResponse
from app.modules.providers.service import _SafeMeasurementAdapter


@pytest.mark.parametrize("model", [None, "", "  ", 42])
def test_missing_provider_model_stays_unknown(model):
    response = ProviderResponse(
        text="answer", model_version="configured-alias", latency_ms=1, raw={"model": model}
    )
    adapter = _SafeMeasurementAdapter(
        SimpleNamespace(ask=lambda *_: response), "synthetic-key", "https://private.test"
    )
    assert adapter.ask("q").model_version == ""


def test_provider_model_is_the_version_evidence():
    response = ProviderResponse(
        text="answer", model_version="configured-alias", latency_ms=1, raw={"model": "actual-v2"}
    )
    adapter = _SafeMeasurementAdapter(
        SimpleNamespace(ask=lambda *_: response), "synthetic-key", "https://private.test"
    )
    assert adapter.ask("q").model_version == "actual-v2"
