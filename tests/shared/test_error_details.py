"""Detail tambahan pada error domain (SCRUM-143).

CASE_NOT_READY perlu membawa daftar bagian yang kurang, bukan hanya
satu field. Handler di main.py menggabungkan `details` ke body JSON.
"""

import asyncio
import json

from app.main import domain_error_handler
from app.shared.exceptions import ValidationError


def _body(exc) -> tuple[int, dict]:
    response = asyncio.run(domain_error_handler(None, exc))
    return response.status_code, json.loads(response.body)


def test_detail_digabung_ke_body_error():
    # Arrange
    kurang = [{"field": "split_tag", "message": "Tag dev atau test wajib dipilih."}]
    exc = ValidationError("Belum siap", code="CASE_NOT_READY", details={"missing": kurang})

    # Act
    status, body = _body(exc)

    # Assert
    assert status == 422
    assert body == {"code": "CASE_NOT_READY", "message": "Belum siap", "missing": kurang}


def test_tanpa_detail_body_tetap_seperti_semula():
    # Act
    _, body = _body(ValidationError("Salah", code="X", field="judul"))

    # Assert
    assert body == {"code": "X", "message": "Salah", "field": "judul"}


def test_detail_tidak_boleh_menimpa_kode_dan_pesan():
    # Arrange
    exc = ValidationError("Asli", code="ASLI", details={"code": "PALSU", "message": "palsu"})

    # Act
    _, body = _body(exc)

    # Assert
    assert body["code"] == "ASLI"
    assert body["message"] == "Asli"
