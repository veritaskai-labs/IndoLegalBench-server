"""SCRUM-139: perjalanan versi kasus dari v1 approved sampai v2 menggantikannya."""

from app.modules.auth.models import User
from app.shared.security import Role
from tests.modules.cases.test_cases import ADMIN_LAIN, _badan, _buat, _ganti, _setujui, _suite
from tests.modules.conftest import USER_ID_QA

JUDUL_REVISI = "PHK sepihak tanpa surat peringatan"


def _pengguna(db_session) -> None:
    db_session.add_all(
        [
            User(
                id=USER_ID_QA,
                name="Pengguna QA",
                email="qa-139@veritask.test",
                role=Role.CONTRIBUTOR,
            ),
            User(id=ADMIN_LAIN, name="Admin QA", email="admin-139@veritask.test", role=Role.ADMIN),
        ]
    )
    db_session.commit()


def _v2_disunting_admin(as_role, buat_pengguna, db_session) -> tuple:
    client = as_role(Role.CONTRIBUTOR)
    _pengguna(db_session)
    v1 = _buat(client, _suite(client)).json()
    _setujui(db_session, v1["id"])
    _ganti(client, buat_pengguna(Role.ADMIN, user_id=ADMIN_LAIN, name="Admin QA"))
    assert client.post(f"/cases/{v1['id']}/versions").status_code == 201
    revisi = _badan()
    revisi["identity"] = {**revisi["identity"], "title": JUDUL_REVISI}
    assert client.put(f"/cases/{v1['id']}", json=revisi).status_code == 200
    return client, v1


def _v2_disunting_admin_lalu_diajukan_penulis(as_role, buat_pengguna, db_session) -> tuple:
    client, v1 = _v2_disunting_admin(as_role, buat_pengguna, db_session)
    _ganti(client, buat_pengguna(Role.CONTRIBUTOR))
    diajukan = client.post(f"/cases/{v1['id']}/submit-review")
    assert diajukan.status_code == 200, diajukan.text
    assert diajukan.json()["version"] == 2
    assert diajukan.json()["status"] == "in_review"
    return client, v1


def test_admin_yang_menyunting_v2_tidak_bisa_mengajukannya_sendiri(
    as_role, buat_pengguna, db_session
):
    client, v1 = _v2_disunting_admin(as_role, buat_pengguna, db_session)

    response = client.post(f"/cases/{v1['id']}/submit-review")

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    riwayat = client.get(f"/cases/{v1['id']}/versions").json()
    assert [(v["version_no"], v["status"]) for v in riwayat] == [(1, "approved"), (2, "draft")]


def test_selama_v2_ditinjau_v1_tetap_berlaku_dan_v2_terkunci(as_role, buat_pengguna, db_session):
    client, v1 = _v2_disunting_admin_lalu_diajukan_penulis(as_role, buat_pengguna, db_session)

    versi_baru = client.post(f"/cases/{v1['id']}/versions")
    sunting = client.put(f"/cases/{v1['id']}", json=_badan())
    berlaku = client.get(f"/cases/{v1['id']}").json()

    assert versi_baru.status_code == 409
    assert versi_baru.json()["code"] == "VERSION_IN_PROGRESS"
    assert sunting.status_code == 409
    assert sunting.json()["code"] == "VERSION_LOCKED"
    assert berlaku["version"] == 1
    assert berlaku["status"] == "approved"
    assert berlaku["identity"]["title"] == v1["identity"]["title"]


def test_setelah_v2_disetujui_v2_berlaku_dan_riwayat_menyebut_siapa_mengubah_apa(
    as_role, buat_pengguna, db_session
):
    client, v1 = _v2_disunting_admin_lalu_diajukan_penulis(as_role, buat_pengguna, db_session)
    _setujui(db_session, v1["id"])

    berlaku = client.get(f"/cases/{v1['id']}").json()
    riwayat = client.get(f"/cases/{v1['id']}/versions").json()
    banding = client.get(f"/cases/{v1['id']}/versions/compare", params={"a": 1, "b": 2}).json()

    assert berlaku["version"] == 2
    assert berlaku["status"] == "approved"
    assert berlaku["identity"]["title"] == JUDUL_REVISI
    assert [(v["version_no"], v["status"], v["author"]["name"]) for v in riwayat] == [
        (1, "approved", "Pengguna QA"),
        (2, "approved", "Admin QA"),
    ]
    assert riwayat[1]["changed"] == ["identity.title"]
    assert riwayat[0]["created_at"] <= riwayat[1]["created_at"]
    assert banding["changed"] == ["identity.title"]
    assert banding["a"]["sections"]["identity.title"] == v1["identity"]["title"]
    assert banding["b"]["sections"]["identity.title"] == JUDUL_REVISI
