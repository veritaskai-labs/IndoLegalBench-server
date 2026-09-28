"""Test modul suites.

PBI-2, sub task SCRUM-99. Kasus positif dan negatif untuk siklus suite:
buat, ubah, arsip, aktifkan kembali, dan hapus.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.auth.models import User, UserSession
from app.modules.cases import service as cases_service
from app.modules.suites import repository, service
from app.modules.suites.models import Suite
from app.modules.suites.router import _user_id
from app.shared.config import get_settings
from app.shared.exceptions import ForbiddenError
from app.shared.security import Role
from tests.login import complete_login
from tests.modules.conftest import USER_ID_QA

NAMA = "Ketenagakerjaan 2026"


def _buat(client, name: str = NAMA, description: str | None = "Kasus seputar hubungan kerja"):
    return client.post("/suites", json={"name": name, "description": description})


def test_buat_suite_berhasil(as_role, db_session):
    client = as_role(Role.AUTHOR)

    response = _buat(client)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == NAMA
    assert body["status"] == "active"
    assert body["case_count"] == 0
    assert body["is_empty"] is True
    assert body["exportable"] is False
    tersimpan = db_session.get(Suite, uuid.UUID(body["id"]))
    assert tersimpan is not None
    assert tersimpan.created_by == USER_ID_QA


def test_admin_boleh_membuat_suite(as_role):
    client = as_role(Role.ADMIN)

    assert _buat(client, name="Suite Admin").status_code == 201


def test_nama_kosong_ditolak(as_role):
    """AC1: nama wajib ada."""
    client = as_role(Role.AUTHOR)

    assert _buat(client, name="").status_code == 422
    assert _buat(client, name="   ").status_code == 422


def test_nama_suite_duplikat_ditolak(as_role):
    """AC2: nama yang sudah dipakai, termasuk beda kapital, tidak bisa dipakai ulang."""
    client = as_role(Role.AUTHOR)
    assert _buat(client).status_code == 201

    kedua = _buat(client, name="ketenagakerjaan 2026")

    assert kedua.status_code == 409
    assert kedua.json()["code"] == "SUITE_NAME_TAKEN"


def test_nama_yang_sudah_dihapus_tetap_tidak_boleh_dipakai(as_role):
    client = as_role(Role.AUTHOR)
    dibuat = _buat(client)
    suite_id = dibuat.json()["id"]
    assert client.delete(f"/suites/{suite_id}").status_code == 204

    assert _buat(client).status_code == 409
    assert client.get(f"/suites/{suite_id}").status_code == 404


def test_ubah_nama_null_tidak_mengosongkan(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _buat(client).json()["id"]

    response = client.patch(
        f"/suites/{suite_id}",
        json={"name": None, "description": "Tetap punya nama"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == NAMA


def test_bentrok_unik_saat_ubah_jadi_nama_sudah_dipakai(as_role, monkeypatch):
    def bentrok(*_args, **_kwargs):
        raise IntegrityError("UPDATE", {}, Exception("unique"))

    client = as_role(Role.AUTHOR)
    suite_id = _buat(client).json()["id"]
    monkeypatch.setattr(repository, "save", bentrok)

    response = client.patch(f"/suites/{suite_id}", json={"description": "Gagal simpan"})

    assert response.status_code == 409
    assert response.json()["code"] == "SUITE_NAME_TAKEN"


def test_ubah_hanya_deskripsi(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _buat(client).json()["id"]

    response = client.patch(f"/suites/{suite_id}", json={"description": "Hanya tema"})

    assert response.status_code == 200
    assert response.json()["name"] == NAMA
    assert response.json()["description"] == "Hanya tema"


def test_ubah_deskripsi_null_mengosongkan(as_role):
    """description: null mengosongkan. Kunci yang tidak dikirim tidak mengubahnya."""
    client = as_role(Role.AUTHOR)
    suite_id = _buat(client).json()["id"]

    dikosongkan = client.patch(f"/suites/{suite_id}", json={"description": None})

    assert dikosongkan.status_code == 200
    assert dikosongkan.json()["description"] is None

    diisi = client.patch(f"/suites/{suite_id}", json={"description": "Tema lagi"})
    assert diisi.status_code == 200

    hanya_nama = client.patch(f"/suites/{suite_id}", json={"name": "Nama Baru"})

    assert hanya_nama.status_code == 200
    assert hanya_nama.json()["name"] == "Nama Baru"
    assert hanya_nama.json()["description"] == "Tema lagi"


def test_form_kirim_nama_dan_deskripsi_null(as_role):
    """Body form suite: nama tetap dikirim, deskripsi kosong jadi null."""
    client = as_role(Role.AUTHOR)
    suite_id = _buat(client).json()["id"]

    response = client.patch(
        f"/suites/{suite_id}",
        json={"name": NAMA, "description": None},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == NAMA
    assert body["description"] is None


def test_ubah_kapital_nama_yang_sama_diterima(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _buat(client).json()["id"]

    response = client.patch(f"/suites/{suite_id}", json={"name": "ketenagakerjaan 2026"})

    assert response.status_code == 200
    assert response.json()["name"] == "ketenagakerjaan 2026"


def test_ubah_nama_dan_deskripsi(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _buat(client).json()["id"]

    response = client.patch(
        f"/suites/{suite_id}",
        json={"name": "Hukum Keluarga", "description": "Tema baru"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Hukum Keluarga"
    assert body["description"] == "Tema baru"


def test_ubah_ke_nama_yang_sudah_dipakai_ditolak(as_role):
    client = as_role(Role.AUTHOR)
    _buat(client, name="Sudah Ada")
    suite_id = _buat(client, name="Yang Lain").json()["id"]

    response = client.patch(f"/suites/{suite_id}", json={"name": "sudah ada"})

    assert response.status_code == 409
    assert response.json()["code"] == "SUITE_NAME_TAKEN"


def test_daftar_memisahkan_aktif_dan_arsip(as_role):
    client = as_role(Role.AUTHOR)
    aktif_id = _buat(client, name="Masih Aktif").json()["id"]
    arsip_id = _buat(client, name="Sudah Arsip").json()["id"]
    assert client.post(f"/suites/{arsip_id}/archive").status_code == 200

    aktif = client.get("/suites")
    assert aktif.status_code == 200
    assert [item["id"] for item in aktif.json()["items"]] == [aktif_id]

    arsip = client.get("/suites", params={"status": "archived"})
    assert [item["id"] for item in arsip.json()["items"]] == [arsip_id]
    assert arsip.json()["items"][0]["status"] == "archived"
    assert arsip.json()["items"][0]["exportable"] is False


def test_suite_terhapus_tidak_muncul_di_daftar(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _buat(client).json()["id"]
    assert client.delete(f"/suites/{suite_id}").status_code == 204

    daftar = client.get("/suites")
    assert daftar.json()["items"] == []
    assert daftar.json()["total"] == 0


def test_arsip_lalu_aktifkan_kembali(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _buat(client).json()["id"]

    arsip = client.post(f"/suites/{suite_id}/archive")
    assert arsip.status_code == 200
    assert arsip.json()["status"] == "archived"

    kembali = client.post(f"/suites/{suite_id}/unarchive")
    assert kembali.status_code == 200
    assert kembali.json()["status"] == "active"


def test_arsip_dan_unarchive_yang_sudah_sesuai_tetap_berhasil(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _buat(client).json()["id"]

    assert client.post(f"/suites/{suite_id}/unarchive").status_code == 200
    assert client.post(f"/suites/{suite_id}/archive").status_code == 200
    assert client.post(f"/suites/{suite_id}/archive").status_code == 200


def test_hapus_suite_arsip_ditolak(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _buat(client).json()["id"]
    assert client.post(f"/suites/{suite_id}/archive").status_code == 200

    response = client.delete(f"/suites/{suite_id}")

    assert response.status_code == 409
    masih_ada = client.get("/suites", params={"status": "archived"})
    assert masih_ada.json()["items"][0]["id"] == suite_id


def test_hapus_suite_berisi_kasus_approved_ditolak(as_role, monkeypatch):
    """AC4: suite berisi kasus approved hanya bisa diarsipkan."""
    monkeypatch.setattr(cases_service, "has_approved_case", lambda *_args, **_kwargs: True)
    client = as_role(Role.AUTHOR)
    suite_id = _buat(client).json()["id"]

    response = client.delete(f"/suites/{suite_id}")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "SUITE_HAS_APPROVED_CASES"
    assert "arsip" in body["message"].lower()
    assert client.get(f"/suites/{suite_id}").status_code == 200


def test_suite_kosong_tidak_bisa_diekspor(as_role):
    """AC5: suite kosong ditandai dan tidak bisa ikut ekspor."""
    client = as_role(Role.AUTHOR)
    body = _buat(client).json()

    assert body["is_empty"] is True
    assert body["exportable"] is False
    assert body["case_count"] == 0


def test_daftar_suite_menampilkan_jumlah_kasus(as_role, monkeypatch):
    client = as_role(Role.AUTHOR)
    monkeypatch.setattr(cases_service, "count_for_suite", lambda *_args, **_kwargs: 2)
    _buat(client)

    item = client.get("/suites").json()["items"][0]

    assert item["case_count"] == 2
    assert item["is_empty"] is False
    assert item["exportable"] is True


def test_suite_arsip_tidak_bisa_diekspor_meski_ada_kasus(as_role, monkeypatch):
    monkeypatch.setattr(cases_service, "count_for_suite", lambda *_args, **_kwargs: 2)
    client = as_role(Role.AUTHOR)
    suite_id = _buat(client).json()["id"]

    body = client.post(f"/suites/{suite_id}/archive").json()

    assert body["case_count"] == 2
    assert body["exportable"] is False


def test_bentrok_unik_di_database_jadi_nama_sudah_dipakai(as_role, monkeypatch):
    def bentrok(*_args, **_kwargs):
        raise IntegrityError("INSERT", {}, Exception("unique"))

    monkeypatch.setattr(repository, "create", bentrok)
    client = as_role(Role.AUTHOR)

    response = _buat(client, name="Nama Lain")

    assert response.status_code == 409
    assert response.json()["code"] == "SUITE_NAME_TAKEN"


def test_is_exportable_kosong_lalu_berisi(as_role, db_session, monkeypatch):
    client = as_role(Role.AUTHOR)
    suite_id = uuid.UUID(_buat(client).json()["id"])

    assert service.is_exportable(db_session, suite_id) is False

    monkeypatch.setattr(cases_service, "count_for_suite", lambda *_args, **_kwargs: 1)
    assert service.is_exportable(db_session, suite_id) is True


def test_user_id_dari_string_tetap_uuid():
    class Pengguna:
        user_id = str(USER_ID_QA)

    assert _user_id(Pengguna()) == USER_ID_QA


def test_tanpa_sesi_ditolak(client):
    response = _buat(client)
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_suite_request_refreshes_cookie_at_absolute_cap(client, db_session):
    """SCRUM-91: /suites must not shrink the cookie back to the idle window.

    get_current_user refreshes the cookie on every authenticated request.
    If that refresh used the idle timeout, a real idle expiry on a suite
    call would come back as UNAUTHENTICATED with no cookie left to clear.
    """
    settings = get_settings()
    complete_login(client, db_session)
    expected = f"max-age={settings.absolute_session_lifetime_minutes * 60}"

    listed = client.get("/suites")

    assert listed.status_code == 200
    assert expected in listed.headers["set-cookie"].lower()
    assert settings.absolute_session_lifetime_minutes > settings.idle_timeout_minutes


def test_idle_suite_request_returns_session_expired(client, db_session):
    complete_login(client, db_session)
    session = db_session.query(UserSession).one()
    session_id = session.id
    session.last_activity_at = datetime.now(UTC) - timedelta(
        minutes=get_settings().idle_timeout_minutes + 1
    )
    db_session.commit()

    listed = client.get("/suites")

    assert listed.status_code == 401
    assert listed.json()["code"] == "SESSION_EXPIRED"
    assert db_session.get(UserSession, session_id) is None
    set_cookie = listed.headers.get("set-cookie", "").lower()
    assert "veritask_session=" in set_cookie
    assert "max-age=0" in set_cookie or 'veritask_session=""' in set_cookie


def test_deactivated_user_suite_request_drops_session(client, db_session):
    complete_login(client, db_session)
    session = db_session.query(UserSession).one()
    session_id = session.id
    user = db_session.get(User, session.user_id)
    user.is_active = False
    db_session.commit()

    listed = client.get("/suites")

    assert listed.status_code == 401
    assert listed.json()["code"] == "UNAUTHENTICATED"
    assert db_session.get(UserSession, session_id) is None


@pytest.mark.parametrize("role", [Role.VIEWER, Role.REVIEWER])
def test_peran_bukan_author_atau_admin_ditolak(as_role, role):
    client = as_role(role)

    response = _buat(client, name=f"Coba {role.value}")

    assert response.status_code == 403
    assert response.json()["code"] == ForbiddenError.code
