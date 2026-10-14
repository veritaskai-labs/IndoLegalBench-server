"""GET /cases/{id}/versions/current. SCRUM-172.

Alur utamanya: setelah kasus approved dibuatkan draf, draf itu harus
tetap bisa dibaca dan disunting ulang walau halaman ditutup, sementara
GET /cases/{id} terus menjawab versi yang berlaku.
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


def _kasus_dengan_draf(client, db_session) -> str:
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _setujui(db_session, case_id)
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    return case_id


def test_pembuat_membaca_draf_sementara_get_biasa_tetap_approved(as_role, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _kasus_dengan_draf(client, db_session)

    # Act
    response = client.get(f"/cases/{case_id}/versions/current")

    # Assert
    assert response.status_code == 200
    assert response.json()["version"] == 2
    assert response.json()["status"] == "draft"
    berlaku = client.get(f"/cases/{case_id}").json()
    assert berlaku["version"] == 1
    assert berlaku["status"] == "approved"


def test_draf_yang_disunting_terbaca_lagi_setelah_dibuka_ulang(as_role, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _kasus_dengan_draf(client, db_session)
    ubah = _badan(
        identity={
            "title": "Judul hasil sunting draf",
            "question": "Apakah PHK tanpa surat sah?",
            "category": "ketenagakerjaan",
        }
    )
    assert client.put(f"/cases/{case_id}", json=ubah).status_code == 200

    # Act
    response = client.get(f"/cases/{case_id}/versions/current")

    # Assert
    assert response.status_code == 200
    assert response.json()["identity"]["title"] == "Judul hasil sunting draf"
    assert response.json()["version"] == 2


def test_kasus_yang_belum_pernah_disetujui_mengembalikan_draf_pertamanya(as_role):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]

    # Act
    response = client.get(f"/cases/{case_id}/versions/current")

    # Assert
    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert response.json()["status"] == "draft"


def test_versi_yang_sedang_ditinjau_masih_terbaca_pembuatnya(as_role, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _kasus_dengan_draf(client, db_session)
    kasus = db_session.get(Case, uuid.UUID(case_id))
    kasus.current_version.status = CaseStatus.IN_REVIEW
    db_session.commit()

    # Act
    response = client.get(f"/cases/{case_id}/versions/current")

    # Assert
    assert response.status_code == 200
    assert response.json()["status"] == "in_review"


def test_tanpa_draf_menjawab_404_no_open_version(as_role, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    _setujui(db_session, case_id)

    # Act
    response = client.get(f"/cases/{case_id}/versions/current")

    # Assert
    assert response.status_code == 404
    assert response.json()["code"] == "NO_OPEN_VERSION"


def test_kasus_tidak_ada_menjawab_404_biasa(as_role):
    # Arrange
    client = as_role(Role.AUTHOR)

    # Act
    response = client.get(f"/cases/{uuid.uuid4()}/versions/current")

    # Assert
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_author_lain_tidak_boleh_membaca_draf(as_role, buat_pengguna, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _kasus_dengan_draf(client, db_session)
    _ganti(client, buat_pengguna(Role.AUTHOR, user_id=AUTHOR_LAIN))

    # Act
    response = client.get(f"/cases/{case_id}/versions/current")

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == ForbiddenError.code


def test_admin_boleh_membaca_draf_milik_author(as_role, buat_pengguna, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _kasus_dengan_draf(client, db_session)
    _ganti(client, buat_pengguna(Role.ADMIN, user_id=ADMIN_LAIN))

    # Act
    response = client.get(f"/cases/{case_id}/versions/current")

    # Assert
    assert response.status_code == 200
    assert response.json()["version"] == 2


@pytest.mark.parametrize("role", [Role.REVIEWER, Role.VIEWER])
def test_reviewer_dan_viewer_tidak_boleh_membaca_draf(as_role, buat_pengguna, db_session, role):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _kasus_dengan_draf(client, db_session)
    _ganti(client, buat_pengguna(role, user_id=uuid.uuid4()))

    # Act
    response = client.get(f"/cases/{case_id}/versions/current")

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == ForbiddenError.code
