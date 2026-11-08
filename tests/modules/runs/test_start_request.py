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
JUDGE = uuid.UUID("00000000-0000-0000-0000-0000000000f8")


def _body(**ubah) -> dict:
    """Body minimum yang valid: satu suite, satu produk, label, dan tujuan."""
    body = {
        "suite_ids": [str(SUITE_A)],
        "product_id": str(PRODUCT),
        "label": "Run Oktober DeepSeek",
        "purpose": "official",
        "judge_provider_id": str(JUDGE),
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


def test_provider_penilai_wajib_dikirim():
    """Negatif: PBI-14, sesi tanpa penilai menghasilkan skor kosong, jadi kuncinya wajib ada."""
    # Arrange
    body = _body()
    del body["judge_provider_id"]

    # Act
    with pytest.raises(ValidationError) as galat:
        schemas.StartRequest(**body)

    # Assert
    assert galat.value.errors()[0]["loc"] == ("judge_provider_id",)
    assert galat.value.errors()[0]["type"] == "missing"


def test_provider_penilai_null_ditolak():
    """Negatif: null eksplisit sama dengan tidak memilih, dan ditolak."""
    # Arrange
    body = _body(judge_provider_id=None)

    # Act
    with pytest.raises(ValidationError) as galat:
        schemas.StartRequest(**body)

    # Assert
    assert galat.value.errors()[0]["loc"] == ("judge_provider_id",)


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


# SCRUM-214, catatan Adra di #89: label yang hanya berisi karakter kosong tak terlihat.
LABEL_KOSONG = {
    "tab-baris": "\t\n",
    "nbsp": "\u00a0",
    "zwsp": "\u200b\u200b",
    "bom": "\ufeff",
    "spasi-ideografik": "\u3000",
    "campuran": " \u00a0\u200b\t",
}


@pytest.mark.parametrize("label", list(LABEL_KOSONG.values()), ids=list(LABEL_KOSONG))
def test_label_yang_hanya_berisi_karakter_kosong_ditolak(label):
    """Negatif: tab, baris baru, NBSP, zero-width, atau BOM saja bukan nama sesi (AC1)."""
    # Arrange
    body = _body(label=label)

    # Act + Assert
    with pytest.raises(ValidationError):
        schemas.StartRequest(**body)


def test_karakter_kosong_tak_terlihat_di_tepi_label_dibuang():
    """Edge: zero-width dan NBSP di tepi ikut dibuang, isi di tengah tetap."""
    # Arrange
    body = _body(label="\u200b\u00a0Run\u00a0November\ufeff")

    # Act
    request = schemas.StartRequest(**body)

    # Assert
    assert request.label == "Run\u00a0November"
