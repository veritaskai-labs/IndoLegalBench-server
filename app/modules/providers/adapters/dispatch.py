"""Pilih adapter dari nilai string provider_type.

Kuncinya nilai ProviderType (openai_compatible, gemini_interactions,
anthropic_messages). Tipe yang tidak dikenal menjadi hasil "failed", bukan 500.
"""

from app.modules.providers.adapters.anthropic_messages import AnthropicMessagesAdapter
from app.modules.providers.adapters.base import ConnectionTestResult, ProviderAdapter
from app.modules.providers.adapters.gemini_interactions import GeminiInteractionsAdapter
from app.modules.providers.adapters.openai_compatible import OpenAICompatibleAdapter

_ADAPTERS: dict[str, type[ProviderAdapter]] = {
    "openai_compatible": OpenAICompatibleAdapter,
    "gemini_interactions": GeminiInteractionsAdapter,
    "anthropic_messages": AnthropicMessagesAdapter,
}

UNSUPPORTED = "connection test is not available for this provider type"


def build_adapter(*, provider_type: str, base_url: str, model_name: str, api_key: str):
    adapter_cls = _ADAPTERS.get(provider_type)
    if adapter_cls is None:
        raise ValueError("unsupported measurement provider type")
    return adapter_cls(base_url=base_url, model_name=model_name, api_key=api_key)


def run_connection_test(
    *,
    provider_type: str,
    base_url: str,
    model_name: str,
    api_key: str,
) -> ConnectionTestResult:
    adapter_cls = _ADAPTERS.get(provider_type)
    if adapter_cls is None:
        return ConnectionTestResult(status="failed", message=UNSUPPORTED)
    return adapter_cls(base_url=base_url, model_name=model_name, api_key=api_key).test_connection()
