"""Author mengajukan kasus untuk direview. PBI-6 AC1, SCRUM-143.

Alur mengikuti D3 langkah 1-12: guard D2 (kelengkapan 100%, tag terisi,
rujukan hukum valid), versi jadi in_review, dan round baru dibuat dalam
satu transaksi.
"""

import uuid

import pytest

from app.modules.cases.models import Case, CaseStatus
from app.modules.reviews.models import ReviewRound, RoundStatus
from app.shared.security import Role
from tests.modules.cases.test_cases import AUTHOR_LAIN, _buat, _ganti, _setujui, _suite


def _ajukan(client, case_id: str):
    return client.post(f"/cases/{case_id}/submit-review")


def _rounds(db_session, case_id: str) -> list[ReviewRound]:
    return (
        db_session.query(ReviewRound)
        .filter(ReviewRound.case_id == uuid.UUID(case_id))
        .order_by(ReviewRound.round_no)
        .all()
    )


def _status_versi(db_session, case_id: str) -> CaseStatus:
    db_session.expire_all()
    return db_session.get(Case, uuid.UUID(case_id)).current_version.status


def test_kasus_lengkap_diajukan_jadi_in_review_dengan_round_satu(as_role, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]

    # Act
    response = _ajukan(client, case_id)

    # Assert
    assert response.status_code == 200
    assert response.json() == {
        "case_id": case_id,
        "version": 1,
        "status": "in_review",
        "round_no": 1,
        "round_status": "awaiting_assignment",
    }
    assert _status_versi(db_session, case_id) == CaseStatus.IN_REVIEW
    [ronde] = _rounds(db_session, case_id)
    assert ronde.round_no == 1
    assert ronde.status == RoundStatus.AWAITING_ASSIGNMENT


def test_kasus_belum_lengkap_ditolak_dengan_daftar_kekurangan(as_role, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(
        client, _suite(client), answer_criteria={"must_contain": [], "must_not_contain": []}
    ).json()["id"]

    # Act
    response = _ajukan(client, case_id)

    # Assert
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "CASE_NOT_READY"
    assert body["missing"] == [
        {"field": "answer_criteria", "message": "Butuh minimal satu kriteria jawaban."}
    ]
    assert _status_versi(db_session, case_id) == CaseStatus.DRAFT
    assert _rounds(db_session, case_id) == []


def test_rujukan_rusak_di_versi_tersimpan_ditolak(as_role, db_session):
    """Rujukan rusak tidak bisa lewat PUT, jadi ditanam langsung ke versi."""
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    versi = db_session.get(Case, uuid.UUID(case_id)).current_version
    rujukan = versi.content["legal_refs"][0]
    versi.content = {**versi.content, "legal_refs": [rujukan, {**rujukan, "pasal": ""}]}
    db_session.commit()

    # Act
    response = _ajukan(client, case_id)

    # Assert
    assert response.status_code == 422
    assert [item["field"] for item in response.json()["missing"]] == ["legal_refs[1].pasal"]


def test_author_lain_tidak_boleh_mengajukan(as_role, buat_pengguna, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    _ganti(client, buat_pengguna(Role.AUTHOR, user_id=AUTHOR_LAIN))

    # Act
    response = _ajukan(client, case_id)

    # Assert
    assert response.status_code == 403
    assert _status_versi(db_session, case_id) == CaseStatus.DRAFT


@pytest.mark.parametrize("role", [Role.ADMIN, Role.REVIEWER, Role.VIEWER])
def test_hanya_author_yang_boleh_mengajukan(as_role, buat_pengguna, role):
    """D5a: ajukan kasus ke review hanya untuk Author, kasus sendiri."""
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    _ganti(client, buat_pengguna(role))

    # Act
    response = _ajukan(client, case_id)

    # Assert
    assert response.status_code == 403


def test_tanpa_sesi_ditolak(client):
    # Act + Assert
    assert _ajukan(client, str(uuid.uuid4())).status_code == 401


def test_kasus_tidak_dikenal(as_role):
    # Arrange
    client = as_role(Role.AUTHOR)

    # Act
    response = _ajukan(client, str(uuid.uuid4()))

    # Assert
    assert response.status_code == 404


def test_pengajuan_ganda_ditolak(as_role, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    assert _ajukan(client, case_id).status_code == 200

    # Act
    response = _ajukan(client, case_id)

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_NOT_SUBMITTABLE"
    assert len(_rounds(db_session, case_id)) == 1


def test_versi_yang_sudah_disetujui_tidak_bisa_diajukan(as_role, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    _setujui(db_session, case_id)

    # Act
    response = _ajukan(client, case_id)

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_NOT_SUBMITTABLE"


def test_pengajuan_ulang_setelah_revisi_membuka_round_dua(as_role, db_session):
    """D3 langkah 43-44: versi yang sama, nomor round naik."""
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    assert _ajukan(client, case_id).status_code == 200
    versi = db_session.get(Case, uuid.UUID(case_id)).current_version
    versi.status = CaseStatus.NEEDS_REVISION
    db_session.commit()

    # Act
    response = _ajukan(client, case_id)

    # Assert
    assert response.status_code == 200
    assert response.json()["round_no"] == 2
    assert [ronde.round_no for ronde in _rounds(db_session, case_id)] == [1, 2]


def test_versi_baru_dari_kasus_approved_bisa_diajukan(as_role, db_session):
    """PBI-8: draf v2 diajukan, v1 tetap yang berlaku sampai v2 disetujui."""
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    _setujui(db_session, case_id)
    assert client.post(f"/cases/{case_id}/versions").status_code == 201

    # Act
    response = _ajukan(client, case_id)

    # Assert
    assert response.status_code == 200
    assert response.json()["version"] == 2
    assert client.get(f"/cases/{case_id}").json()["status"] == "approved"


def test_kasus_di_suite_arsip_tidak_bisa_diajukan(as_role):
    # Arrange
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    assert client.post(f"/suites/{suite_id}/archive").status_code == 200

    # Act
    response = _ajukan(client, case_id)

    # Assert
    assert response.status_code == 422
    assert response.json()["code"] == "SUITE_NOT_ACTIVE"


def test_kasus_yang_diajukan_tidak_bisa_diubah(as_role):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    assert _ajukan(client, case_id).status_code == 200
    badan = client.get(f"/cases/{case_id}").json()

    # Act
    response = client.put(f"/cases/{case_id}", json={**badan, "traps": []})

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_LOCKED"
