"""Test kontrak adapter produk AI, PBI-10.

ProviderAdapter adalah kontrak untuk setiap produk AI yang diukur. Test
ini menjaga dua janji di docstring-nya: adapter baru WAJIB
mengimplementasikan test_connection dan ask, dan respons menyimpan data
mentah beserta error-nya.
"""

import pytest

from app.modules.providers.adapters.base import ProviderAdapter, ProviderResponse


class AdapterLengkap(ProviderAdapter):
    name = "lengkap"

    def test_connection(self) -> bool:
        return True

    def ask(self, prompt: str) -> ProviderResponse:
        return ProviderResponse(text=prompt, model_version="v1", latency_ms=5, raw={})


class AdapterTanpaAsk(ProviderAdapter):
    name = "tanpa-ask"

    def test_connection(self) -> bool:
        return True


def test_kontrak_tidak_bisa_dipakai_langsung():
    with pytest.raises(TypeError):
        ProviderAdapter()


def test_adapter_yang_belum_lengkap_ditolak():
    with pytest.raises(TypeError):
        AdapterTanpaAsk()


def test_adapter_lengkap_bisa_dipakai():
    adapter = AdapterLengkap()

    assert adapter.test_connection() is True
    assert adapter.ask("Apa itu PKWT?").text == "Apa itu PKWT?"


def test_respons_tanpa_error_secara_bawaan():
    respons = ProviderResponse(text="jawaban", model_version="v1", latency_ms=10, raw={"id": 1})

    assert respons.error is None
    assert respons.raw == {"id": 1}


def test_respons_gagal_tetap_menyimpan_data_mentah():
    respons = ProviderResponse(
        text="", model_version="v1", latency_ms=0, raw={"status": 500}, error="timeout"
    )

    assert respons.error == "timeout"
    assert respons.raw == {"status": 500}
