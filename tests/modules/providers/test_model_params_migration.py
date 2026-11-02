"""Migrasi parameter lama ke JSON asli penyedia. SCRUM-177 SCRUM-247.

Sebelum SCRUM-247 parameter tersimpan dalam bentuk generik (temperature, effort,
thinking, web_search) dan adaptor menerjemahkannya per penyedia. Sekarang JSON
dikirim apa adanya, jadi produk yang sudah terdaftar harus diubah ke body yang
sama persis dengan yang dulu dikirim, supaya pengukuran berikutnya tidak berubah.
Pemetaan murni dites di sini; penulisan ke tabel dites di
tests/integration/test_model_params_migration_pg.py.

Label di docstring: Positif, Negatif, Edge.
"""

import importlib.util
from pathlib import Path

import pytest

MIGRASI = (
    Path(__file__).resolve().parents[3]
    / "migrations"
    / "versions"
    / "b7e2c9d41a58_model_params_provider_native_json.py"
)


@pytest.fixture(scope="module")
def migrasi():
    spec = importlib.util.spec_from_file_location("migrasi_model_params", MIGRASI)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.mark.parametrize(
    ("penyedia", "lama", "baru"),
    [
        (
            "openai_compatible",
            {"temperature": 0.3, "effort": "high"},
            {"temperature": 0.3, "reasoning_effort": "high"},
        ),
        ("gemini_interactions", {"temperature": 0.3}, {"generation_config": {"temperature": 0.3}}),
        ("anthropic_messages", {"temperature": 0.7}, {"temperature": 0.7}),
    ],
)
def test_bentuk_lama_menjadi_body_yang_dulu_dikirim(migrasi, penyedia, lama, baru):
    """Positif: hasilnya sama dengan keluaran pemetaan SCRUM-208 untuk penyedia itu."""
    assert migrasi.ke_json_penyedia(penyedia, lama) == (baru, [])


@pytest.mark.parametrize("penyedia", ["gemini_interactions", "anthropic_messages"])
def test_effort_tidak_pernah_dikirim_selain_openai_jadi_dibuang(migrasi, penyedia):
    """Edge: effort dulu dijatuhkan untuk Gemini dan Anthropic, jadi tetap tidak dikirim."""
    baru, dibuang = migrasi.ke_json_penyedia(penyedia, {"effort": "low"})
    assert baru is None
    assert dibuang == ["effort"]


@pytest.mark.parametrize(
    "penyedia", ["openai_compatible", "gemini_interactions", "anthropic_messages"]
)
def test_thinking_dan_web_search_dibuang_karena_dulu_tidak_dikirim(migrasi, penyedia):
    """Negatif: kalau dipertahankan, kunci ini mulai terkirim dan bisa ditolak penyedia."""
    baru, dibuang = migrasi.ke_json_penyedia(
        penyedia, {"temperature": 0.3, "thinking": True, "web_search": False}
    )
    assert "thinking" not in (baru or {})
    assert "web_search" not in (baru or {})
    assert dibuang == ["thinking", "web_search"]


def test_effort_tidak_dikenal_dibuang_seperti_dulu(migrasi):
    """Edge: SCRUM-208 menjatuhkan effort di luar low/medium/high."""
    assert migrasi.ke_json_penyedia("openai_compatible", {"effort": "turbo"}) == (None, ["effort"])


@pytest.mark.parametrize("lama", [None, {}, {"temperature": None}])
def test_kosong_tetap_kosong(migrasi, lama):
    """Edge: tidak ada yang dikirim, jadi kolom NULL."""
    assert migrasi.ke_json_penyedia("openai_compatible", lama) == (None, [])


def test_penyedia_tidak_dikenal_tidak_mengirim_apa_pun(migrasi):
    """Edge: SCRUM-208 tidak mengirim parameter untuk penyedia di luar tabelnya."""
    assert migrasi.ke_json_penyedia(None, {"temperature": 0.3}) == (None, ["temperature"])


@pytest.mark.parametrize(
    ("penyedia", "baru", "lama"),
    [
        (
            "openai_compatible",
            {"temperature": 0.3, "reasoning_effort": "medium"},
            {"temperature": 0.3, "effort": "medium"},
        ),
        ("gemini_interactions", {"generation_config": {"temperature": 0.3}}, {"temperature": 0.3}),
        ("anthropic_messages", {"temperature": 1.0}, {"temperature": 1.0}),
    ],
)
def test_rollback_mengembalikan_bentuk_lama(migrasi, penyedia, baru, lama):
    """Positif: upgrade lalu downgrade kembali ke data semula."""
    assert migrasi.ke_bentuk_lama(penyedia, baru) == (lama, [])


def test_rollback_membuang_yang_tidak_muat_di_skema_lama(migrasi):
    """Negatif: skema lama menolak kunci lain (extra=forbid), jadi kunci itu dibuang dan dilaporkan."""
    baru = {
        "temperature": 5,
        "reasoning_effort": "turbo",
        "top_p": 0.9,
        "thinking": {"type": "enabled"},
    }
    lama, dibuang = migrasi.ke_bentuk_lama("openai_compatible", baru)
    assert lama is None
    assert dibuang == ["reasoning_effort", "temperature", "thinking", "top_p"]


def test_rollback_gemini_dengan_isian_lain_di_generation_config(migrasi):
    """Edge: hanya temperature yang punya padanan di skema lama."""
    baru = {"generation_config": {"temperature": 0.2, "top_k": 40}}
    assert migrasi.ke_bentuk_lama("gemini_interactions", baru) == (
        {"temperature": 0.2},
        ["generation_config.top_k"],
    )
