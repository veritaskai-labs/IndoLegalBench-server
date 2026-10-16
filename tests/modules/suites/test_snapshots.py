"""Suite snapshots. SCRUM-137.

The snapshot stores a copy of each approved case. A later case edit or
suite rename does not change that copy. Author, Reviewer, Admin, and
Viewer can read. Creating a snapshot stays Admin only. SCRUM-138 shows
the list and the detail to Viewer.
"""

import uuid
from datetime import timedelta
from typing import Any, NamedTuple

import pytest

from app.modules.auth.models import User
from app.modules.cases.models import Case
from app.modules.suites.models import SuiteSnapshot
from app.shared.exceptions import ForbiddenError
from app.shared.security import Role
from tests.modules.cases.test_cases import (
    ADMIN_LAIN,
    _badan,
    _buat,
    _ganti,
    _setujui,
    _suite,
)
from tests.modules.conftest import USER_ID_QA


def _seed(db, user_id, name, email, role) -> None:
    db.add(User(id=user_id, name=name, email=email, role=role))
    db.commit()


def _as_admin(client, buat_pengguna):
    _ganti(client, buat_pengguna(Role.ADMIN, user_id=ADMIN_LAIN, name="Admin QA"))


def test_snapshot_hanya_versi_approved_dan_menolak_suite_kosong(as_role, buat_pengguna, db_session):
    client = as_role(Role.AUTHOR)
    _seed(db_session, USER_ID_QA, "Pengguna QA", "qa-snap@veritask.test", Role.AUTHOR)
    _seed(db_session, ADMIN_LAIN, "Admin QA", "admin-snap@veritask.test", Role.ADMIN)
    suite_id = _suite(client)
    _as_admin(client, buat_pengguna)
    empty = client.post(f"/suites/{suite_id}/snapshots")
    assert empty.status_code == 422
    assert empty.json()["code"] == "NOTHING_TO_SNAPSHOT"

    _ganti(client, buat_pengguna(Role.AUTHOR, user_id=USER_ID_QA, name="Pengguna QA"))
    approved = _buat(client, suite_id).json()
    draft_only = _buat(client, suite_id, case_code="PHK-002").json()
    _setujui(db_session, approved["id"])
    assert client.post(f"/cases/{approved['id']}/versions").status_code == 201
    judul_draf = _badan(case_code="PHK-001")
    judul_draf["identity"] = {**judul_draf["identity"], "title": "Judul draf yang tidak ikut"}
    assert client.put(f"/cases/{approved['id']}", json=judul_draf).status_code == 200

    _as_admin(client, buat_pengguna)
    response = client.post(f"/suites/{suite_id}/snapshots")

    assert response.status_code == 201
    body = response.json()
    assert body["author"] == {"id": str(ADMIN_LAIN), "name": "Admin QA"}
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["case_id"] == approved["id"]
    assert item["case_id"] != draft_only["id"]
    assert item["body"]["version_no"] == 1
    assert item["body"]["status"] == "approved"
    assert item["body"]["sections"]["identity.title"] == approved["identity"]["title"]
    assert item["body"]["sections"]["case_code"] == "PHK-001"


def test_suite_arsip_yang_punya_kasus_approved_boleh_di_snapshot(
    as_role, buat_pengguna, db_session
):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _setujui(db_session, case_id)
    assert client.post(f"/suites/{suite_id}/archive").status_code == 200
    _as_admin(client, buat_pengguna)

    response = client.post(f"/suites/{suite_id}/snapshots")

    assert response.status_code == 201
    assert len(response.json()["items"]) == 1


@pytest.mark.parametrize("role", [Role.AUTHOR, Role.REVIEWER, Role.VIEWER])
def test_bukan_admin_tidak_boleh_membuat_snapshot(as_role, buat_pengguna, db_session, role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _setujui(db_session, case_id)
    if role != Role.AUTHOR:
        _ganti(client, buat_pengguna(role, user_id=uuid.uuid4()))

    response = client.post(f"/suites/{suite_id}/snapshots")

    assert response.status_code == 403
    assert response.json()["code"] == ForbiddenError.code


def test_isi_snapshot_tidak_berubah_setelah_kasus_dan_suite_diedit(
    as_role, buat_pengguna, db_session
):
    client = as_role(Role.AUTHOR)
    _seed(db_session, USER_ID_QA, "Pengguna QA", "qa-freeze@veritask.test", Role.AUTHOR)
    _seed(db_session, ADMIN_LAIN, "Admin QA", "admin-freeze@veritask.test", Role.ADMIN)
    suite_id = _suite(client, name="Suite sebelum snapshot")
    case_id = _buat(client, suite_id).json()["id"]
    _setujui(db_session, case_id)
    _as_admin(client, buat_pengguna)
    dibuat = client.post(f"/suites/{suite_id}/snapshots")
    assert dibuat.status_code == 201
    snapshot_id = dibuat.json()["id"]
    sebelum = client.get(f"/snapshots/{snapshot_id}").json()

    _ganti(client, buat_pengguna(Role.AUTHOR, user_id=USER_ID_QA, name="Pengguna QA"))
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    kode_baru = _badan(case_code="PHK-999")
    ditolak = client.put(f"/cases/{case_id}", json=kode_baru)
    assert ditolak.status_code == 409
    assert ditolak.json()["code"] == "CASE_CODE_LOCKED"
    diubah = _badan()
    diubah["identity"] = {**diubah["identity"], "title": "Judul sesudah snapshot"}
    assert client.put(f"/cases/{case_id}", json=diubah).status_code == 200
    assert (
        client.patch(f"/suites/{suite_id}", json={"name": "Suite sesudah snapshot"}).status_code
        == 200
    )

    _as_admin(client, buat_pengguna)
    sesudah = client.get(f"/snapshots/{snapshot_id}").json()

    assert sesudah["items"] == sebelum["items"]
    assert sesudah["items"][0]["body"]["sections"]["case_code"] == "PHK-001"
    assert sesudah["items"][0]["body"]["sections"]["identity.title"] == "PHK sepihak"
    assert client.get(f"/cases/{case_id}").json()["case_code"] == "PHK-001"


def test_dua_snapshot_tersimpan_terpisah(as_role, buat_pengguna, db_session):
    client = as_role(Role.AUTHOR)
    _seed(db_session, ADMIN_LAIN, "Admin QA", "admin-dua@veritask.test", Role.ADMIN)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _setujui(db_session, case_id)
    _as_admin(client, buat_pengguna)

    pertama = client.post(f"/suites/{suite_id}/snapshots")
    kedua = client.post(f"/suites/{suite_id}/snapshots")
    daftar = client.get(f"/suites/{suite_id}/snapshots")

    assert pertama.status_code == 201
    assert kedua.status_code == 201
    assert pertama.json()["id"] != kedua.json()["id"]
    assert daftar.status_code == 200
    body = daftar.json()
    assert body["total"] == 2
    assert {item["id"] for item in body["items"]} == {pertama.json()["id"], kedua.json()["id"]}
    assert body["items"][0]["case_count"] == 1


@pytest.mark.parametrize("role", [Role.REVIEWER, Role.VIEWER])
def test_viewer_dan_reviewer_boleh_membaca_snapshot(as_role, buat_pengguna, db_session, role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _setujui(db_session, case_id)
    _as_admin(client, buat_pengguna)
    snapshot_id = client.post(f"/suites/{suite_id}/snapshots").json()["id"]
    _ganti(client, buat_pengguna(role, user_id=uuid.uuid4()))

    daftar = client.get(f"/suites/{suite_id}/snapshots")
    detail = client.get(f"/snapshots/{snapshot_id}")

    assert daftar.status_code == 200
    assert daftar.json()["total"] == 1
    assert detail.status_code == 200
    assert detail.json()["id"] == snapshot_id


def test_snapshot_yang_tidak_ada_404(as_role):
    client = as_role(Role.ADMIN)

    response = client.get(f"/snapshots/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


JUDUL_V2 = "PHK sepihak tanpa surat peringatan"


def _versi_berlaku(db_session, case_id: str) -> str:
    db_session.expire_all()
    return str(db_session.get(Case, uuid.UUID(case_id)).latest_approved_version_id)


def _mundurkan(db_session, snapshot_id: str, hari: int) -> None:
    # SQLite menyimpan waktu per detik; jarak harian membuat urutan daftar pasti.
    snapshot = db_session.get(SuiteSnapshot, uuid.UUID(snapshot_id))
    snapshot.created_at = snapshot.created_at - timedelta(days=hari)
    db_session.commit()


class TigaSnapshot(NamedTuple):
    client: Any
    suite_id: str
    v1_id: str
    v2_id: str
    ids: dict[str, str]


@pytest.fixture
def tiga_snapshot(as_role, buat_pengguna, db_session) -> TigaSnapshot:
    client = as_role(Role.AUTHOR)
    _seed(db_session, USER_ID_QA, "Pengguna QA", "qa-v2-snap@veritask.test", Role.AUTHOR)
    _seed(db_session, ADMIN_LAIN, "Admin QA", "admin-v2-snap@veritask.test", Role.ADMIN)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _setujui(db_session, case_id)
    v1_id = _versi_berlaku(db_session, case_id)
    _as_admin(client, buat_pengguna)
    lama = client.post(f"/suites/{suite_id}/snapshots").json()["id"]
    _mundurkan(db_session, lama, hari=2)
    _ganti(client, buat_pengguna(Role.AUTHOR))
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    revisi = _badan()
    revisi["identity"] = {**revisi["identity"], "title": JUDUL_V2}
    assert client.put(f"/cases/{case_id}", json=revisi).status_code == 200
    _as_admin(client, buat_pengguna)
    tengah = client.post(f"/suites/{suite_id}/snapshots").json()["id"]
    _mundurkan(db_session, tengah, hari=1)
    _setujui(db_session, case_id)
    v2_id = _versi_berlaku(db_session, case_id)
    baru = client.post(f"/suites/{suite_id}/snapshots").json()["id"]
    ids = {"lama": lama, "tengah": tengah, "baru": baru}
    return TigaSnapshot(client, suite_id, v1_id, v2_id, ids)


def _isi(client, snapshot_id: str) -> list[tuple[str, str]]:
    items = client.get(f"/snapshots/{snapshot_id}").json()["items"]
    return [(i["case_version_id"], i["body"]["sections"]["identity.title"]) for i in items]


@pytest.mark.parametrize("snapshot", ["lama", "tengah"])
def test_snapshot_sebelum_v2_disetujui_memegang_v1(tiga_snapshot, snapshot):
    isi = _isi(tiga_snapshot.client, tiga_snapshot.ids[snapshot])

    assert isi == [(tiga_snapshot.v1_id, "PHK sepihak")]


def test_snapshot_setelah_v2_disetujui_memegang_v2(tiga_snapshot):
    isi = _isi(tiga_snapshot.client, tiga_snapshot.ids["baru"])

    assert isi == [(tiga_snapshot.v2_id, JUDUL_V2)]
    assert tiga_snapshot.v1_id != tiga_snapshot.v2_id


def test_daftar_snapshot_terbaru_di_atas(tiga_snapshot):
    daftar = tiga_snapshot.client.get(f"/suites/{tiga_snapshot.suite_id}/snapshots").json()

    ids = tiga_snapshot.ids
    assert [item["id"] for item in daftar["items"]] == [ids["baru"], ids["tengah"], ids["lama"]]


def test_suite_yang_belum_pernah_di_snapshot_menjawab_daftar_kosong(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    response = client.get(f"/suites/{suite_id}/snapshots")

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["items"] == []
