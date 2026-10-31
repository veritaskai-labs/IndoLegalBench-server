"""SCRUM-76 SCRUM-208: one mapper from generic model parameters to provider bodies."""

import pytest

from app.modules.providers.adapters.params import (
    ANTHROPIC_DEFAULT_MAX_TOKENS,
    ModelParameters,
    build_request_extras,
)


def test_none_parameters_send_nothing_except_anthropic_required_max_tokens():
    assert build_request_extras("openai_compatible", None) == {}
    assert build_request_extras("gemini_interactions", None) == {}
    assert build_request_extras("anthropic_messages", None) == {
        "max_tokens": ANTHROPIC_DEFAULT_MAX_TOKENS
    }


def test_empty_fields_are_omitted_and_gemini_sends_temperature():
    params = ModelParameters(temperature=0.3, effort=None, thinking=None, web_search=None)
    openai = build_request_extras("openai_compatible", params)
    gemini = build_request_extras("gemini_interactions", params)
    anthropic = build_request_extras("anthropic_messages", params)

    assert openai == {"temperature": 0.3}
    assert gemini == {"generation_config": {"temperature": 0.3}}
    assert anthropic["temperature"] == 0.3
    assert "effort" not in openai


def test_known_effort_maps_only_for_openai_and_invalid_effort_is_dropped():
    valid = ModelParameters(effort="low")
    invalid = ModelParameters(effort="turbo")

    assert build_request_extras("openai_compatible", valid) == {"reasoning_effort": "low"}
    assert "reasoning_effort" not in build_request_extras("gemini_interactions", valid)
    assert "reasoning_effort" not in build_request_extras("anthropic_messages", valid)
    assert build_request_extras("openai_compatible", invalid) == {}


@pytest.mark.parametrize(
    "provider",
    ["openai_compatible", "gemini_interactions", "anthropic_messages"],
)
def test_unsupported_thinking_and_web_search_are_dropped(provider):
    params = ModelParameters(thinking=True, web_search=True)
    extras = build_request_extras(provider, params)
    assert "thinking" not in extras
    assert "web_search" not in extras
    assert "tools" not in extras


def test_explicit_max_tokens_is_sent_and_anthropic_none_uses_the_constant():
    params = ModelParameters(max_tokens=128)
    assert build_request_extras("openai_compatible", params) == {"max_tokens": 128}
    assert build_request_extras("gemini_interactions", params) == {
        "generation_config": {"max_output_tokens": 128}
    }
    both = ModelParameters(temperature=0.3, max_tokens=128)
    assert build_request_extras("gemini_interactions", both) == {
        "generation_config": {"temperature": 0.3, "max_output_tokens": 128}
    }
    assert build_request_extras("anthropic_messages", params)["max_tokens"] == 128
    assert (
        build_request_extras("anthropic_messages", ModelParameters())["max_tokens"]
        == ANTHROPIC_DEFAULT_MAX_TOKENS
    )


def test_unknown_provider_drops_every_field():
    params = ModelParameters(temperature=0.3, effort="high", max_tokens=10, thinking=True)
    assert build_request_extras("aiyu_unknown", params) == {}
