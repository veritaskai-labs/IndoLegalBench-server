"""Satu aturan teks dan URL untuk produk AI dan provider penilai. SCRUM-258, review #118.

Sebelumnya providers/schemas.py dan judge_schemas.py masing-masing punya salinan
pembersih teks dan URL, dan keduanya sudah berbeda: produk memakai str.strip(), yang
tidak membuang zero-width space atau BOM (SCRUM-214), penilai memakai strip_blank.
Akibatnya nama produk AI yang tampak kosong di layar tetap tersimpan. Aturan sekarang
ada di providers/validators.py dan dipakai kedua schema, jadi pengetatan berikutnya
(misalnya menolak http:// polos untuk kredensial) cukup di satu tempat.

Kredensial penilai di-strip dan minimal 8 karakter (review #118, non-blocking): kunci
yang ditempel dengan spasi atau baris baru di ujung tidak lagi menghasilkan hint
berisi spasi dan gagal autentikasi, dan hint 4 karakter tidak pernah sama dengan
seluruh kunci. Produk AI menyusul di follow-up karena mengubah kontrak form yang
sudah dipakai FE.

Label: Positif, Negatif, Edge.
"""

import importlib

import pytest
from cryptography.fernet import Fernet

from app.shared.config import get_settings
from app.shared.security import Role

_ZW = "​﻿ "


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def validators():
    """Diimpor per tes supaya tes produk tetap berjalan (dan gagal karena perilakunya)
    selama modul bersama belum ada."""
    return importlib.import_module("app.modules.providers.validators")


@pytest.fixture
def admin(as_role):
    return as_role(Role.ADMIN)


# --- Modul bersama ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("isi", "hasil"),
    [("  Nama  ", "Nama"), (f"{_ZW}Nama{_ZW}", "Nama"), ("A‍B", "A‍B")],
    ids=["spasi", "zero-width-tepi", "zwj-tengah-utuh"],
)
def test_teks_wajib_dibersihkan_di_tepi_saja(validators, isi, hasil):
    assert validators.required_text(isi) == hasil


@pytest.mark.parametrize("isi", ["", "   ", _ZW], ids=["kosong", "spasi", "zero-width"])
def test_teks_wajib_yang_kosong_ditolak(validators, isi):
    with pytest.raises(ValueError, match="tidak boleh kosong"):
        validators.required_text(isi)


@pytest.mark.parametrize(
    "isi", ["https://api.example.test/v1", f"{_ZW}http://api.example.test{_ZW}"]
)
def test_url_http_atau_https_diterima_dan_dibersihkan(validators, isi):
    assert validators.http_url(isi).startswith("http")
    assert _ZW[0] not in validators.http_url(isi)


@pytest.mark.parametrize(
    "isi", ["ftp://api.example.test", "api.example.test", "https://", "bukan url"]
)
def test_url_selain_http_ditolak(validators, isi):
    with pytest.raises(ValueError, match="URL http atau https"):
        validators.http_url(isi)


@pytest.mark.parametrize(
    ("isi", "hasil"),
    [("kunci-123", "kunci-123"), (" kunci-123\n", "kunci-123"), ("12345678", "12345678")],
    ids=["biasa", "spasi-dan-baris-baru", "tepat-8"],
)
def test_kredensial_penilai_di_strip(validators, isi, hasil):
    assert validators.judge_credential(isi) == hasil


@pytest.mark.parametrize(
    "isi", ["1234567", " 1234567 \n", " " * 12], ids=["tujuh", "tujuh-setelah-strip", "spasi"]
)
def test_kredensial_penilai_kurang_dari_8_ditolak(validators, isi):
    with pytest.raises(ValueError, match="minimal 8 karakter"):
        validators.judge_credential(isi)


# --- Produk AI memakai aturan yang sama (dulu str.strip) -------------------------


def _otomatis(**ubah) -> dict:
    body = {
        "name": "Otomatis Validator",
        "provider_type": "openai_compatible",
        "base_url": "https://api.example.test/v1",
        "model_name": "model-uji",
        "credential": "provider-token-validator",  # pragma: allowlist secret
        "rate_limit_per_minute": 30,
        "monthly_budget_idr": "1500000.00",
    }
    return body | ubah


def _manual(**ubah) -> dict:
    body = {
        "name": "Manual Validator",
        "measurement_type": "manual",
        "vendor_name": "Penyedia Uji",
        "site_url": "https://contoh.test",
    }
    return body | ubah


@pytest.mark.parametrize(
    "body",
    [
        _otomatis(name=_ZW),
        _otomatis(model_name=_ZW),
        _manual(vendor_name=_ZW),
        _manual(name=f" {_ZW} "),
    ],
    ids=["nama", "model", "vendor", "nama-campur"],
)
def test_produk_dengan_teks_yang_hanya_zero_width_ditolak(admin, body):
    """Negatif: dulu lolos dan tersimpan sebagai nama yang tampak kosong."""
    assert admin.post("/admin/providers", json=body).status_code == 422


def test_produk_menyimpan_nama_dan_url_tanpa_zero_width_di_tepi(admin):
    """Positif: sama dengan penilai, karakter tak terlihat di tepi dibuang."""
    body = admin.post(
        "/admin/providers",
        json=_otomatis(name=f"{_ZW}AiYU{_ZW}", base_url=f"{_ZW}https://api.example.test/v1"),
    ).json()

    assert body["name"] == "AiYU"
    assert body["base_url"] == "https://api.example.test/v1"


def test_ubah_nama_produk_menjadi_zero_width_ditolak(admin):
    produk = admin.post("/admin/providers", json=_otomatis()).json()

    response = admin.patch(f"/admin/providers/{produk['id']}", json={"name": _ZW})

    assert response.status_code == 422
