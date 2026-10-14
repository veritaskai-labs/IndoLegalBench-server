"""Version history and compare. SCRUM-137.

Author, Reviewer, Admin, and Viewer can read. SCRUM-138 shows the history
to Viewer, and a Reviewer needs it while a new version is in review.
"""

import uuid

import pytest
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.cases.models import Case, CaseVersion
from app.shared.security import Role
from tests.modules.cases.test_cases import _badan, _buat, _ganti, _setujui, _suite
from tests.modules.conftest import USER_ID_QA


def _seed_author(db: Session) -> None:
    db.add(
        User(
            id=USER_ID_QA,
            name="Pengguna QA",
            email="qa-history@veritask.test",
            role=Role.AUTHOR,
        )
    )
    db.commit()


def _approved_case(client, db_session) -> tuple[str, str]:
    _seed_author(db_session)
    suite_id = _suite(client)
    dibuat = _buat(client, suite_id).json()
    _setujui(db_session, dibuat["id"])
    return suite_id, dibuat["id"]


def test_riwayat_versi_pertama_kosong_dan_menyebut_pembuat(as_role, db_session):
    client = as_role(Role.AUTHOR)
    _, case_id = _approved_case(client, db_session)

    response = client.get(f"/cases/{case_id}/versions")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["version_no"] == 1
    assert rows[0]["status"] == "approved"
    assert rows[0]["changed"] == []
    assert rows[0]["author"] == {"id": str(USER_ID_QA), "name": "Pengguna QA"}


def test_perubahan_kategori_tercatat_sendiri(as_role, db_session):
    client = as_role(Role.AUTHOR)
    _, case_id = _approved_case(client, db_session)
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    kategori = _badan()
    kategori["identity"] = {**kategori["identity"], "category": "pidana"}
    assert client.put(f"/cases/{case_id}", json=kategori).status_code == 200

    history = client.get(f"/cases/{case_id}/versions").json()

    assert history[1]["version_no"] == 2
    assert history[1]["changed"] == ["category"]


def test_perbandingan_menyebut_kategori_dan_rujukan(as_role, db_session):
    client = as_role(Role.AUTHOR)
    _, case_id = _approved_case(client, db_session)
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    badan = _badan()
    badan["identity"] = {**badan["identity"], "category": "pidana"}
    badan["legal_refs"] = [{**badan["legal_refs"][0], "pasal": "152"}]
    assert client.put(f"/cases/{case_id}", json=badan).status_code == 200

    compared = client.get(f"/cases/{case_id}/versions/compare", params={"a": 1, "b": 2})

    assert compared.status_code == 200
    body = compared.json()
    assert body["changed"] == ["category", "legal_refs"]
    assert body["a"]["sections"]["identity.title"] == body["b"]["sections"]["identity.title"]
    assert body["a"]["sections"]["category"] == "ketenagakerjaan"
    assert body["b"]["sections"]["category"] == "pidana"
    assert body["b"]["sections"]["legal_refs"][0]["pasal"] == "152"
    assert "identity.title" not in body["changed"]


def test_banding_versi_dengan_dirinya_kosong(as_role, db_session):
    client = as_role(Role.AUTHOR)
    _, case_id = _approved_case(client, db_session)

    response = client.get(f"/cases/{case_id}/versions/compare", params={"a": 1, "b": 1})

    assert response.status_code == 200
    assert response.json()["changed"] == []
    assert response.json()["a"]["version_no"] == 1
    assert response.json()["b"]["version_no"] == 1


def test_kode_kasus_dibaca_dari_versi(as_role, db_session):
    """History and compare use the code stored on each version, not the live case row."""
    client = as_role(Role.AUTHOR)
    _, case_id = _approved_case(client, db_session)
    assert client.post(f"/cases/{case_id}/versions").status_code == 201

    case = db_session.get(Case, uuid.UUID(case_id))
    v1 = (
        db_session.query(CaseVersion)
        .filter(CaseVersion.case_id == case.id, CaseVersion.version_no == 1)
        .one()
    )
    v1.case_code = "PHK-OLD"
    db_session.commit()

    history = client.get(f"/cases/{case_id}/versions").json()
    compared = client.get(f"/cases/{case_id}/versions/compare", params={"a": 1, "b": 2}).json()

    assert history[1]["changed"] == ["case_code"]
    assert compared["changed"] == ["case_code"]
    assert compared["a"]["sections"]["case_code"] == "PHK-OLD"
    assert compared["b"]["sections"]["case_code"] == case.case_code
    assert compared["a"]["sections"]["case_code"] != compared["b"]["sections"]["case_code"]


def test_nomor_versi_yang_tidak_ada_404(as_role, db_session):
    client = as_role(Role.AUTHOR)
    _, case_id = _approved_case(client, db_session)

    response = client.get(f"/cases/{case_id}/versions/compare", params={"a": 1, "b": 9})

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_kasus_yang_tidak_ada_404(as_role):
    client = as_role(Role.AUTHOR)

    response = client.get(f"/cases/{uuid.uuid4()}/versions")

    assert response.status_code == 404


@pytest.mark.parametrize("role", [Role.VIEWER, Role.REVIEWER])
def test_viewer_dan_reviewer_boleh_membaca_riwayat(as_role, buat_pengguna, db_session, role):
    client = as_role(Role.AUTHOR)
    _, case_id = _approved_case(client, db_session)
    _ganti(client, buat_pengguna(role, user_id=uuid.uuid4()))

    history = client.get(f"/cases/{case_id}/versions")
    compared = client.get(f"/cases/{case_id}/versions/compare", params={"a": 1, "b": 1})

    assert history.status_code == 200
    assert history.json()[0]["version_no"] == 1
    assert compared.status_code == 200
    assert compared.json()["changed"] == []
