"""SCRUM-177 SCRUM-247: model parameters are free-form JSON sent to the provider as-is.

The client asked at the PBI-23 UAT (2 Nov 2026) for parameters as JSON text, so a new
model or a renamed parameter needs no code change. These replace the SCRUM-208 tests
for the fixed temperature/effort/thinking/web_search mapping.
"""

import pytest

from app.modules.providers.adapters.params import (
    ANTHROPIC_DEFAULT_MAX_TOKENS,
    build_request_extras,
)

_PROVIDERS = ["openai_compatible", "gemini_interactions", "anthropic_messages"]


def test_none_parameters_send_nothing_except_anthropic_required_max_tokens():
    assert build_request_extras("openai_compatible", None) == {}
    assert build_request_extras("gemini_interactions", None) == {}
    assert build_request_extras("anthropic_messages", None) == {
        "max_tokens": ANTHROPIC_DEFAULT_MAX_TOKENS
    }


@pytest.mark.parametrize("provider", ["openai_compatible", "gemini_interactions"])
def test_empty_object_sends_nothing(provider):
    assert build_request_extras(provider, {}) == {}


@pytest.mark.parametrize("provider", _PROVIDERS)
def test_every_key_is_sent_as_is_including_nested_and_unknown_keys(provider):
    params = {
        "thinking": True,
        "web_search": True,
        "reasoning_effort": "high",
        "generation_config": {"temperature": 0.3, "thinking_config": {"budget": 1024}},
    }
    extras = build_request_extras(provider, params)
    for key, value in params.items():
        assert extras[key] == value


def test_anthropic_keeps_an_explicit_max_tokens():
    assert build_request_extras("anthropic_messages", {"max_tokens": 32})["max_tokens"] == 32


def test_other_providers_get_no_default_max_tokens():
    assert "max_tokens" not in build_request_extras("openai_compatible", {"temperature": 0.3})


def test_extras_are_a_deep_copy_of_the_stored_parameters():
    """Corner: an adapter changing its body must not change the session copy."""
    params = {"generation_config": {"temperature": 0.3}}
    extras = build_request_extras("gemini_interactions", params)
    extras["generation_config"]["temperature"] = 1.0
    assert params == {"generation_config": {"temperature": 0.3}}


def test_unknown_provider_also_passes_parameters_through():
    assert build_request_extras("aiyu_unknown", {"temperature": 0.3}) == {"temperature": 0.3}
