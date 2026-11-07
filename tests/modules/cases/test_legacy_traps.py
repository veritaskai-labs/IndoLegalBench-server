"""Field traps dicabut dari kontrak kasus aktif (SCRUM-256).

Klien menyatakan jebakan tidak dipakai dan tidak mengurangi skor (PBI-14 AC4).
Request lama yang masih mengirim traps tetap diterima, nilainya dibuang, dan
server mencatat peringatan. Data traps di versi yang sudah tersimpan tidak
disentuh.
"""

import logging
import uuid

import pytest
from pydantic import ValidationError

from app.modules.cases.models import Case
from app.modules.cases.schemas import (
    CaseCompleteness,
    CaseRead,
    CaseWrite,
    ReviewCopy,
    VersionSections,
)
from app.shared.security import Role
from tests.modules.cases.legacy_traps import TRAPS_LAMA, tanam_traps_lama
from tests.modules.cases.test_cases import _badan, _buat, _setujui, _suite

LOGGER_LEGACY = "app.modules.cases.legacy_payload"


def _isi_versi(db_session, case_id: str) -> dict:
    db_session.expire_all()
    return dict(db_session.get(Case, uuid.UUID(case_id)).current_version.content)


# --- Kontrak: schema ----------------------------------------------------------


@pytest.mark.parametrize("model", [CaseRead, CaseCompleteness, ReviewCopy, VersionSections])
def test_respons_kasus_tidak_lagi_punya_traps(model):
    assert "traps" not in model.model_fields
    assert "trap_count" not in model.model_fields


def test_openapi_tidak_lagi_memuat_traps_sama_sekali(as_role):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)

    # Act
    spec = client.get("/openapi.json").json()["components"]["schemas"]

    # Assert: FE tidak melihat traps, dan generator zod tidak menerima kata kunci baru
    assert "traps" not in spec["CaseWrite"]["properties"]
    assert "Trap" not in spec
    assert "traps" not in spec["CaseRead"]["properties"]
    assert "trap_count" not in spec["CaseCompleteness"]["properties"]


def test_case_write_menerima_traps_lama_tetapi_tidak_membawanya():
    # Arrange
    badan = _badan(traps=TRAPS_LAMA)

    # Act
    payload = CaseWrite.model_validate(badan)

    # Assert
    assert "traps" in payload.model_fields_set
    assert "traps" not in payload.model_dump(mode="json")


def test_case_write_tanpa_traps_tidak_menandai_field_lama():
    badan = _badan()
    badan.pop("traps", None)

    payload = CaseWrite.model_validate(badan)

    assert "traps" not in payload.model_fields_set


@pytest.mark.parametrize("traps", [[], None, [{"description": ""}], [{"sembarang": 1}]])
def test_case_write_tidak_memvalidasi_isi_traps_lama(traps):
    payload = CaseWrite.model_validate(_badan(traps=traps))

    assert "traps" not in payload.model_dump(mode="json")


def test_case_write_menolak_traps_yang_bukan_daftar():
    with pytest.raises(ValidationError) as galat:
        CaseWrite.model_validate(_badan(traps="bukan daftar"))

    assert galat.value.errors()[0]["loc"] == ("traps",)


# --- Perilaku endpoint --------------------------------------------------------


def test_buat_kasus_dengan_traps_lama_dibuang_dan_tidak_disimpan(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)

    # Act
    response = _buat(client, suite_id, traps=TRAPS_LAMA)

    # Assert
    assert response.status_code == 201
    assert "traps" not in response.json()
    assert "traps" not in _isi_versi(db_session, response.json()["id"])


def test_traps_lama_yang_rusak_tidak_lagi_menghasilkan_422(as_role):
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)

    response = _buat(client, suite_id, traps=[{"description": "", "expected_model_behavior": None}])

    assert response.status_code == 201


def test_traps_bukan_daftar_ditolak_422_menunjuk_field_traps(as_role):
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)

    response = _buat(client, suite_id, traps="bukan daftar")

    assert response.status_code == 422
    assert response.json()["field"] == "traps"


def test_ubah_kasus_dengan_traps_lama_tetap_berhasil(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    case_id = _buat(client, _suite(client)).json()["id"]

    # Act
    response = client.put(f"/cases/{case_id}", json=_badan(traps=TRAPS_LAMA))

    # Assert
    assert response.status_code == 200
    assert "traps" not in response.json()
    assert "traps" not in _isi_versi(db_session, case_id)


def test_kasus_lama_berisi_traps_bisa_dibuka_diedit_dan_diajukan(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    tanam_traps_lama(db_session, case_id, TRAPS_LAMA)

    # Act
    dibuka = client.get(f"/cases/{case_id}")
    diedit = client.put(
        f"/cases/{case_id}",
        json=_badan(identity={"title": "Judul baru", "question": "Sah?", "category": None}),
    )
    diajukan = client.post(f"/cases/{case_id}/submit-review")

    # Assert
    assert dibuka.status_code == 200
    assert "traps" not in dibuka.json()
    assert diedit.status_code == 200
    assert diedit.json()["identity"]["title"] == "Judul baru"
    assert diajukan.status_code == 200


def test_kasus_lama_berisi_traps_tetap_lengkap_di_daftar_dan_completeness(as_role, db_session):
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    tanam_traps_lama(db_session, case_id, TRAPS_LAMA)

    ringkasan = client.get(f"/suites/{suite_id}/cases").json()[0]
    kelengkapan = client.get(f"/cases/{case_id}/completeness").json()

    assert ringkasan["completeness_pct"] == 100
    assert kelengkapan["is_complete"] is True
    assert "trap_count" not in kelengkapan


def test_versi_approved_lama_menyimpan_traps_utuh_setelah_draf_baru(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    tanam_traps_lama(db_session, case_id, TRAPS_LAMA)
    _setujui(db_session, case_id)

    # Act
    draf = client.post(f"/cases/{case_id}/versions")
    ubah = client.put(f"/cases/{case_id}", json=_badan())

    # Assert
    assert draf.status_code == 201
    assert "traps" not in draf.json()
    assert ubah.status_code == 200
    kasus = db_session.get(Case, uuid.UUID(case_id))
    db_session.refresh(kasus)
    disetujui = db_session.get(type(kasus.current_version), kasus.latest_approved_version_id)
    assert disetujui.content["traps"] == TRAPS_LAMA


# --- Peringatan untuk payload lama --------------------------------------------


def test_payload_dengan_traps_lama_dicatat_peringatan_tanpa_isinya(as_role, caplog):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)

    # Act
    with caplog.at_level(logging.WARNING, logger=LOGGER_LEGACY):
        _buat(client, suite_id, traps=TRAPS_LAMA)

    # Assert
    catatan = [r for r in caplog.records if r.name == LOGGER_LEGACY]
    assert len(catatan) == 1
    assert catatan[0].levelno == logging.WARNING
    assert "traps" in catatan[0].getMessage()
    assert "Mencampur upah" not in caplog.text
    assert "pasal yang dicabut" not in caplog.text


def test_payload_tanpa_traps_tidak_dicatat_peringatan(as_role, caplog):
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    badan = _badan()
    badan.pop("traps", None)

    with caplog.at_level(logging.WARNING, logger=LOGGER_LEGACY):
        response = client.post(f"/suites/{suite_id}/cases", json=badan)

    assert response.status_code == 201
    assert [r for r in caplog.records if r.name == LOGGER_LEGACY] == []
