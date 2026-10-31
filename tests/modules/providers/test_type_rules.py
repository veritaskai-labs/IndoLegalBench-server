"""Isian wajib dan terlarang per jenis pengukuran. PBI-23 AC1-AC3, SCRUM-201.

Unit test aturan murni di app/modules/providers/rules.py, tanpa database dan tanpa
HTTP. Sumbernya Jira AC2/AC3 dan constraint C1 di D1: produk Otomatis wajib punya
kredensial, batas panggilan, dan batas biaya; produk Manual wajib nama penyedia dan
URL situs; isian milik jenis lain tidak boleh ada. Constraint C1 di database tetap
jadi pengaman terakhir; aturan ini membuat pelanggarannya jadi 422 yang jelas.

Label di docstring: Positif (diterima), Negatif (ditolak), Edge (batas).
"""

from decimal import Decimal

import pytest

from app.modules.providers.models import MeasurementType, ProviderType
from app.modules.providers.rules import (
    ACCESS_NOTE_SECRET,
    FIELD_NOT_ALLOWED,
    FIELD_REQUIRED,
    access_note_problem,
    type_field_problem,
)


def _otomatis(**ubah) -> dict:
    """Isian Otomatis lengkap; kunci yang diberi None berarti tidak diisi."""
    isi = {
        "provider_type": ProviderType.OPENAI_COMPATIBLE,
        "base_url": "https://api.example.test/v1",
        "model_name": "model-uji",
        "credential": "rahasia-uji",
        "rate_limit_per_minute": 30,
        "monthly_budget_idr": Decimal("1500000.00"),
    }
    isi.update(ubah)
    return isi


def _manual(**ubah) -> dict:
    isi = {"vendor_name": "Penyedia Uji", "site_url": "https://contoh.test"}
    isi.update(ubah)
    return isi


def _masalah(jenis, isian, *, catatan_wajib=False):
    return type_field_problem(jenis, isian, access_note_required=catatan_wajib)


# --- Positif: isian lengkap sesuai jenis ---------------------------------------------------


def test_otomatis_lengkap_diterima():
    """Positif: semua isian wajib Otomatis terisi, tanpa isian Manual (AC2)."""
    # Act + Assert
    assert _masalah(MeasurementType.AUTOMATIC, _otomatis()) is None


def test_otomatis_boleh_tanpa_isian_opsional():
    """Edge: parameter model dan harga opsional; kosong berarti default penyedia (D1)."""
    # Act + Assert
    assert (
        _masalah(
            MeasurementType.AUTOMATIC,
            _otomatis(model_params=None, price_input_usd_per_mtok=None),
        )
        is None
    )


def test_manual_cukup_nama_penyedia_dan_url():
    """Positif: produk Manual tanpa kredensial, batas, maupun uji koneksi (AC3)."""
    # Act + Assert
    assert _masalah(MeasurementType.MANUAL, _manual(access_note=None)) is None


# --- Negatif: isian wajib kosong -----------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "provider_type",
        "base_url",
        "model_name",
        "credential",
        "rate_limit_per_minute",
        "monthly_budget_idr",
    ],
)
def test_otomatis_tanpa_isian_wajib_ditolak(field):
    """Negatif: setiap isian wajib Otomatis yang kosong dilaporkan dengan nama field-nya."""
    # Act
    masalah = _masalah(MeasurementType.AUTOMATIC, _otomatis(**{field: None}))

    # Assert
    assert (masalah.code, masalah.field) == (FIELD_REQUIRED, field)


@pytest.mark.parametrize("field", ["vendor_name", "site_url"])
def test_manual_tanpa_isian_wajib_ditolak(field):
    """Negatif: nama penyedia dan URL situs wajib untuk produk Manual (AC3, C1)."""
    # Act
    masalah = _masalah(MeasurementType.MANUAL, _manual(**{field: None}))

    # Assert
    assert (masalah.code, masalah.field) == (FIELD_REQUIRED, field)


# --- Negatif: isian milik jenis lain --------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "nilai"),
    [
        ("credential", "rahasia-uji"),
        ("base_url", "https://api.example.test/v1"),
        ("rate_limit_per_minute", 30),
        ("model_params", {"temperature": 0.3}),
    ],
)
def test_manual_dengan_isian_otomatis_ditolak(field, nilai):
    """Negatif: produk Manual tidak boleh menyimpan kredensial atau isian Otomatis (AC3)."""
    # Act
    masalah = _masalah(MeasurementType.MANUAL, _manual(**{field: nilai}))

    # Assert
    assert (masalah.code, masalah.field) == (FIELD_NOT_ALLOWED, field)


@pytest.mark.parametrize("field", ["vendor_name", "site_url", "access_note", "manual_params"])
def test_otomatis_dengan_isian_manual_ditolak(field):
    """Negatif: satu produk satu jenis; isian Manual tidak ikut di produk Otomatis (AC1)."""
    # Act
    masalah = _masalah(MeasurementType.AUTOMATIC, _otomatis(**{field: "isi"}))

    # Assert
    assert (masalah.code, masalah.field) == (FIELD_NOT_ALLOWED, field)


def test_isian_jenis_lain_dilaporkan_sebelum_isian_wajib():
    """Edge: kalau dua-duanya salah, isian yang tidak boleh ada dilaporkan dulu.

    Biasanya itu tanda jenis yang dipilih keliru, jadi pesan itu lebih membantu
    daripada daftar isian wajib dari jenis yang salah.
    """
    # Act
    masalah = _masalah(MeasurementType.MANUAL, {"credential": "rahasia-uji"})

    # Assert
    assert (masalah.code, masalah.field) == (FIELD_NOT_ALLOWED, "credential")


# --- Catatan akses lewat flag konfigurasi (TBA klien) --------------------------------------


def test_catatan_akses_opsional_secara_bawaan():
    """Edge: tanpa flag, produk Manual tanpa catatan akses tetap diterima."""
    # Act + Assert
    assert _masalah(MeasurementType.MANUAL, _manual(access_note=None)) is None


def test_catatan_akses_wajib_bila_flag_menyala():
    """Negatif: dengan flag menyala, catatan akses ikut jadi isian wajib Manual."""
    # Act
    masalah = _masalah(MeasurementType.MANUAL, _manual(access_note=None), catatan_wajib=True)

    # Assert
    assert (masalah.code, masalah.field) == (FIELD_REQUIRED, "access_note")


def test_pesan_menyebut_isian_dan_jenisnya():
    """Positif: pesan bisa ditampilkan apa adanya di form (bahasa yang sama dengan label UI)."""
    # Act
    masalah = _masalah(MeasurementType.AUTOMATIC, _otomatis(credential=None))

    # Assert
    assert masalah.message == "Kredensial wajib diisi untuk produk Otomatis."


# --- Catatan akses tanpa kata sandi (AC3, review #81 poin 1) -------------------------------
# Catatan akses tersalin apa adanya ke audit_logs yang tidak bisa dihapus, jadi pola
# "kata sandi: ..." ditolak sebelum tersimpan. Penyaring kasar, bukan jaminan.


@pytest.mark.parametrize(
    "catatan",
    [
        None,
        "Login lewat akun tim legal, minta akses ke Admin.",
        "PIN dikirim lewat SMS ke nomor kantor",
        "Simulasi pinjaman: pakai paket Pro",
        "Token harian habis jam 12 malam",
    ],
    ids=["kosong", "biasa", "pin-tanpa-nilai", "pinjaman", "token-tanpa-nilai"],
)
def test_catatan_akses_tanpa_rahasia_diterima(catatan):
    """Positif/Edge: kata seperti PIN atau token boleh disebut selama tidak diikuti nilai
    ("PIN: 1234"), dan kata yang hanya mengandung "pin" (pinjaman) tidak ikut tertolak."""
    # Act + Assert
    assert access_note_problem(catatan) is None


@pytest.mark.parametrize(
    "catatan",
    [
        "login legal@veritask.example / password: Hunter2!2026",
        "Kata sandi: rahasia123",
        "sandi=abc",
        "PWD : abc",
        "pin: 1234",
        "token=sk-abc",
        "API key: sk-abc",
        "api_key=sk-abc",
    ],
)
def test_catatan_akses_berisi_rahasia_ditolak(catatan):
    """Negatif: pola "<kata rahasia>: nilai" ditolak dengan code dan field yang jelas,
    tanpa peduli huruf besar kecil atau spasi sebelum titik dua."""
    # Act
    masalah = access_note_problem(catatan)

    # Assert
    assert (masalah.code, masalah.field) == (ACCESS_NOTE_SECRET, "access_note")
    assert "kata sandi" in masalah.message
