"""Body POST /measurements. SCRUM-76 SCRUM-207.

PBI-11 AC1: sesi wajib diberi label dan wajib ditandai Pengukuran Resmi atau
Uji Coba. Kolomnya mengikuti D2 measurement_sessions: label varchar(200) dan
purpose enum('official', 'trial'). Isian ringkasan (suite, produk, filter,
pengulangan) diwarisi dari SummaryRequest SCRUM-206, jadi aturannya tidak
ditulis dua kali.

Label: Positif = isian diterima, Negatif = isian ditolak, Edge = batas nilai.
"""

import uuid

import pytest
from pydantic import ValidationError

from app.modules.runs import schemas

SUITE_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
PRODUCT = uuid.UUID("00000000-0000-0000-0000-0000000000c3")


def _body(**ubah) -> dict:
    """Body minimum yang valid: satu suite, satu produk, label, dan tujuan."""
    body = {
        "suite_ids": [str(SUITE_A)],
        "product_id": str(PRODUCT),
        "label": "Run Oktober DeepSeek",
        "purpose": "official",
    }
    body.update(ubah)
    return body


def test_label_dan_tujuan_tersimpan_dengan_bawaan_ringkasan():
    """Positif: body minimum diterima, filter dan pengulangan memakai bawaan SCRUM-206."""
    # Arrange
    body = _body()

    # Act
    request = schemas.StartRequest(**body)

    # Assert
    assert request.label == "Run Oktober DeepSeek"
    assert request.purpose == schemas.MeasurementPurpose.OFFICIAL
    assert request.case_filter == schemas.CaseFilter.BOTH
    assert request.repeat_count == 3


@pytest.mark.parametrize("tujuan", ["official", "trial"])
def test_kedua_tujuan_diterima(tujuan):
    """Positif: Pengukuran Resmi dan Uji Coba sama-sama boleh (AC1)."""
    # Arrange
    body = _body(purpose=tujuan)

    # Act
    request = schemas.StartRequest(**body)

    # Assert
    assert request.purpose.value == tujuan


def test_spasi_di_tepi_label_dibuang():
    """Edge: label disimpan tanpa spasi tepi, supaya daftar sesi rapi."""
    # Arrange
    body = _body(label="  Uji coba Gemini  ")

    # Act
    request = schemas.StartRequest(**body)

    # Assert
    assert request.label == "Uji coba Gemini"


def test_label_tepat_200_karakter_diterima():
    """Edge: 200 karakter adalah batas kolom label di D2."""
    # Arrange
    body = _body(label="a" * 200)

    # Act
    request = schemas.StartRequest(**body)

    # Assert
    assert len(request.label) == 200


@pytest.mark.parametrize(
    "ubah",
    [
        # Negatif: label wajib (AC1).
        {"label": None},
        {"label": ""},
        {"label": "   "},
        {"label": "a" * 201},
        # Negatif: tujuan wajib dan hanya dua pilihan (AC1).
        {"purpose": None},
        {"purpose": "resmi"},
        # Negatif: aturan ringkasan tetap berlaku karena diwarisi.
        {"repeat_count": 6},
        {"suite_ids": []},
    ],
)
def test_isian_tidak_valid_ditolak(ubah):
    """Negatif: label kosong, tujuan tak dikenal, atau isian ringkasan salah menjadi 422."""
    # Arrange
    body = _body(**ubah)

    # Act + Assert
    with pytest.raises(ValidationError):
        schemas.StartRequest(**body)


@pytest.mark.parametrize("kolom", ["label", "purpose"])
def test_label_dan_tujuan_tidak_boleh_dihilangkan(kolom):
    """Negatif: tanpa label atau tujuan, sesi tidak bisa dibuat (AC1)."""
    # Arrange
    body = _body()
    del body[kolom]

    # Act + Assert
    with pytest.raises(ValidationError):
        schemas.StartRequest(**body)


JUDGE = uuid.UUID("00000000-0000-0000-0000-0000000000f8")


def test_provider_penilai_opsional_dan_kosong_bila_tidak_dikirim():
    """Edge: judge_providers baru ada di PBI-14, jadi Mulai tetap bisa tanpa penilai."""
    # Arrange
    body = _body()

    # Act
    request = schemas.StartRequest(**body)

    # Assert
    assert request.judge_provider_id is None


def test_provider_penilai_yang_dipilih_diterima():
    """Positif: AC1, D3 langkah 19, penilai LLM dipilih di halaman Buat Pengukuran."""
    # Arrange
    body = _body(judge_provider_id=str(JUDGE))

    # Act
    request = schemas.StartRequest(**body)

    # Assert
    assert request.judge_provider_id == JUDGE


def test_provider_penilai_bukan_uuid_ditolak():
    """Negatif: id penilai yang rusak menjadi 422 sebelum data apa pun disentuh."""
    # Arrange
    body = _body(judge_provider_id="bukan-uuid")

    # Act + Assert
    with pytest.raises(ValidationError):
        schemas.StartRequest(**body)
