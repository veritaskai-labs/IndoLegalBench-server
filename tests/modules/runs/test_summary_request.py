"""Request body of POST /measurements/summary. SCRUM-76 SCRUM-206.

Validation rules live once, in the schema: repeat_count 1 to 5 with a
default of 3 (PBI-11 AC3), a dev, test or both filter (AC2), at least one
suite and exactly one product.
"""

import uuid

import pytest
from pydantic import ValidationError

from app.modules.runs import schemas

SUITE_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
SUITE_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
PRODUCT = uuid.UUID("00000000-0000-0000-0000-0000000000c3")


def _body(**ubah) -> dict:
    body = {"suite_ids": [str(SUITE_A)], "product_id": str(PRODUCT)}
    body.update(ubah)
    return body


def test_nilai_bawaan_tiga_kali_dan_both_saat_tidak_dikirim():
    # Arrange
    body = _body()

    # Act
    request = schemas.SummaryRequest(**body)

    # Assert
    assert request.repeat_count == 3
    assert request.case_filter == schemas.CaseFilter.BOTH
    assert request.suite_ids == [SUITE_A]
    assert request.product_id == PRODUCT


@pytest.mark.parametrize("repeat", [1, 2, 5])
def test_repeat_count_dalam_rentang_diterima(repeat):
    # Arrange + Act
    request = schemas.SummaryRequest(**_body(repeat_count=repeat))

    # Assert
    assert request.repeat_count == repeat


@pytest.mark.parametrize("repeat", [0, -1, 6, 100, 2.5, "abc"])
def test_repeat_count_di_luar_rentang_atau_bukan_bilangan_bulat_ditolak(repeat):
    # Arrange
    body = _body(repeat_count=repeat)

    # Act
    with pytest.raises(ValidationError) as galat:
        schemas.SummaryRequest(**body)

    # Assert
    assert galat.value.errors()[0]["loc"] == ("repeat_count",)


def test_repeat_count_null_eksplisit_memakai_nilai_bawaan():
    # Arrange
    body = _body(repeat_count=None)

    # Act
    request = schemas.SummaryRequest(**body)

    # Assert
    assert request.repeat_count == 3


def test_case_filter_null_eksplisit_memakai_both():
    # Arrange
    body = _body(case_filter=None)

    # Act
    request = schemas.SummaryRequest(**body)

    # Assert
    assert request.case_filter == schemas.CaseFilter.BOTH


@pytest.mark.parametrize("nilai", ["dev", "test", "both"])
def test_case_filter_yang_dikenal_diterima(nilai):
    # Arrange + Act
    request = schemas.SummaryRequest(**_body(case_filter=nilai))

    # Assert
    assert request.case_filter.value == nilai


def test_case_filter_tidak_dikenal_ditolak():
    # Arrange
    body = _body(case_filter="all")

    # Act
    with pytest.raises(ValidationError) as galat:
        schemas.SummaryRequest(**body)

    # Assert
    assert galat.value.errors()[0]["loc"] == ("case_filter",)


def test_suite_ids_kosong_ditolak():
    # Arrange
    body = _body(suite_ids=[])

    # Act
    with pytest.raises(ValidationError) as galat:
        schemas.SummaryRequest(**body)

    # Assert
    assert galat.value.errors()[0]["loc"] == ("suite_ids",)


def test_suite_ids_ganda_digabung_dengan_urutan_pertama_dipertahankan():
    # Arrange
    body = _body(suite_ids=[str(SUITE_B), str(SUITE_A), str(SUITE_B)])

    # Act
    request = schemas.SummaryRequest(**body)

    # Assert
    assert request.suite_ids == [SUITE_B, SUITE_A]


def test_product_id_wajib_dan_harus_uuid():
    # Arrange
    tanpa_produk = {"suite_ids": [str(SUITE_A)]}
    bukan_uuid = _body(product_id="bukan-uuid")

    # Act + Assert
    with pytest.raises(ValidationError) as hilang:
        schemas.SummaryRequest(**tanpa_produk)
    with pytest.raises(ValidationError) as salah:
        schemas.SummaryRequest(**bukan_uuid)
    assert hilang.value.errors()[0]["loc"] == ("product_id",)
    assert salah.value.errors()[0]["loc"] == ("product_id",)
