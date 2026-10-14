"""Versi kasus. SCRUM-136.

Versi yang disetujui tidak boleh ditimpa. POST menyalinnya menjadi draf,
dan GET tetap mengembalikan versi yang masih berlaku.
"""

import uuid

import pytest

from app.modules.cases.models import Case, CaseStatus
from app.shared.exceptions import ForbiddenError
from app.shared.security import Role
from tests.modules.cases.test_cases import (
    ADMIN_LAIN,
    AUTHOR_LAIN,
    _badan,
    _buat,
    _ganti,
    _setujui,
    _suite,
)


def test_put_versi_approved_ditolak_dan_isinya_tetap(as_role, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    dibuat = _buat(client, suite_id).json()
    _setujui(db_session, dibuat["id"])

    response = client.put(
        f"/cases/{dibuat['id']}",
        json=_badan(
            identity={
                "title": "Judul yang tidak boleh masuk",
                "question": "Apakah PHK tanpa surat sah?",
                "category": "ketenagakerjaan",
            }
        ),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_LOCKED"
    tetap = client.get(f"/cases/{dibuat['id']}").json()
    assert tetap["identity"]["title"] == dibuat["identity"]["title"]
    assert tetap["status"] == "approved"
    assert tetap["version"] == 1


def test_pembuat_membuat_versi_draf_tanpa_mengganti_yang_berlaku(as_role, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    dibuat = _buat(client, suite_id).json()
    _setujui(db_session, dibuat["id"])

    response = client.post(f"/cases/{dibuat['id']}/versions")

    assert response.status_code == 201
    draf = response.json()
    assert draf["status"] == "draft"
    assert draf["version"] == 2
    assert draf["identity"]["title"] == dibuat["identity"]["title"]
    assert draf["legal_refs"] == dibuat["legal_refs"]
    assert draf["traps"] == dibuat["traps"]
    berlaku = client.get(f"/cases/{dibuat['id']}").json()
    assert berlaku["status"] == "approved"
    assert berlaku["version"] == 1
    assert berlaku["identity"]["title"] == dibuat["identity"]["title"]


def test_admin_boleh_membuat_versi_baru(as_role, buat_pengguna, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _setujui(db_session, case_id)
    _ganti(client, buat_pengguna(Role.ADMIN, user_id=ADMIN_LAIN))

    response = client.post(f"/cases/{case_id}/versions")

    assert response.status_code == 201
    assert response.json()["version"] == 2
    assert response.json()["status"] == "draft"


@pytest.mark.parametrize("status", ["draft", "in_review", "needs_revision"])
def test_versi_baru_ditolak_selama_masih_berjalan(as_role, db_session, status):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _setujui(db_session, case_id)
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    if status != "draft":
        kasus = db_session.get(Case, uuid.UUID(case_id))
        kasus.current_version.status = CaseStatus(status)
        db_session.commit()

    response = client.post(f"/cases/{case_id}/versions")

    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_IN_PROGRESS"


def test_versi_baru_sebelum_disetujui_ditolak(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]

    response = client.post(f"/cases/{case_id}/versions")

    assert response.status_code == 409
    assert response.json()["code"] == "NO_APPROVED_VERSION"


def test_author_lain_tidak_boleh_membuat_versi(as_role, buat_pengguna, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _setujui(db_session, case_id)
    _ganti(client, buat_pengguna(Role.AUTHOR, user_id=AUTHOR_LAIN))

    response = client.post(f"/cases/{case_id}/versions")

    assert response.status_code == 403
    assert response.json()["code"] == ForbiddenError.code


@pytest.mark.parametrize("role", [Role.VIEWER, Role.REVIEWER])
def test_reviewer_dan_viewer_ditolak(as_role, buat_pengguna, db_session, role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _setujui(db_session, case_id)
    _ganti(client, buat_pengguna(role, user_id=uuid.uuid4()))

    response = client.post(f"/cases/{case_id}/versions")

    assert response.status_code == 403
    assert response.json()["code"] == ForbiddenError.code


def test_put_draf_baru_tidak_mengubah_versi_yang_berlaku(as_role, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    dibuat = _buat(client, suite_id).json()
    _setujui(db_session, dibuat["id"])
    assert client.post(f"/cases/{dibuat['id']}/versions").status_code == 201

    diubah = client.put(
        f"/cases/{dibuat['id']}",
        json=_badan(
            identity={
                "title": "Judul draf",
                "question": "Apakah PHK tanpa surat sah?",
                "category": "ketenagakerjaan",
            }
        ),
    )

    assert diubah.status_code == 200
    assert diubah.json()["identity"]["title"] == "Judul draf"
    assert diubah.json()["status"] == "draft"
    assert diubah.json()["version"] == 2
    berlaku = client.get(f"/cases/{dibuat['id']}").json()
    assert berlaku["identity"]["title"] == dibuat["identity"]["title"]
    assert berlaku["status"] == "approved"
    assert berlaku["version"] == 1


def test_kode_kasus_tidak_berubah_saat_draf_baru(as_role, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    dibuat = _buat(client, suite_id).json()
    _setujui(db_session, dibuat["id"])
    assert client.post(f"/cases/{dibuat['id']}/versions").status_code == 201

    response = client.put(f"/cases/{dibuat['id']}", json=_badan(case_code="PHK-999"))

    assert response.status_code == 409
    assert response.json()["code"] == "CASE_CODE_LOCKED"
    assert client.get(f"/cases/{dibuat['id']}").json()["case_code"] == dibuat["case_code"]


def test_put_saat_ditinjau_ditolak(as_role, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _setujui(db_session, case_id)
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    kasus = db_session.get(Case, uuid.UUID(case_id))
    kasus.current_version.status = CaseStatus.IN_REVIEW
    db_session.commit()

    response = client.put(f"/cases/{case_id}", json=_badan())

    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_LOCKED"
