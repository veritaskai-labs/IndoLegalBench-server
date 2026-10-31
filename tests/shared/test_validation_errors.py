"""Detail error validasi yang selalu bisa ditulis sebagai JSON. SCRUM-76 SCRUM-206."""

import json

import pytest

from app.shared.validation_errors import detail_aman


def _error(nilai) -> dict:
    return {"type": "finite_number", "loc": ("body", "x"), "msg": "salah", "input": nilai}


@pytest.mark.parametrize(
    ("nilai", "tulisan"),
    [(float("inf"), "inf"), (float("-inf"), "-inf"), (float("nan"), "nan")],
)
def test_angka_tak_hingga_dan_nan_ditulis_sebagai_teks(nilai, tulisan):
    # Arrange
    errors = [_error(nilai)]

    # Act
    hasil = detail_aman(errors)

    # Assert
    assert hasil[0]["input"] == tulisan
    json.dumps(hasil, allow_nan=False)


def test_nilai_di_dalam_list_dan_objek_ikut_dibersihkan():
    # Arrange
    errors = [_error({"a": [1, float("inf")], "b": (float("nan"),)})]

    # Act
    hasil = detail_aman(errors)

    # Assert
    assert hasil[0]["input"] == {"a": [1, "inf"], "b": ["nan"]}


def test_nilai_biasa_tidak_berubah_dan_loc_menjadi_list():
    # Arrange
    errors = [_error(3.5), _error("teks"), _error(None)]

    # Act
    hasil = detail_aman(errors)

    # Assert
    assert [item["input"] for item in hasil] == [3.5, "teks", None]
    assert hasil[0]["loc"] == ["body", "x"]
    assert hasil[0]["msg"] == "salah"


def test_daftar_kosong_tetap_kosong():
    # Arrange: tidak ada error

    # Act
    hasil = detail_aman([])

    # Assert
    assert hasil == []
