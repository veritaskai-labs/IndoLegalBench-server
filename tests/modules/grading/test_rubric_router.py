"""SCRUM-222 PR B: endpoint /admin/rubrics. PBI-14 AC3, AC7; keputusan PO K7.

Hanya Admin: tanpa sesi 401, peran lain 403 (CLAUDE.md, aturan empat peran). Viewer
cukup melihat nomor versi di hasil penilaian (SCRUM-227), jadi tidak ada GET untuknya
di sini, dan prompt penilai tidak pernah keluar ke peran lain.

Isolasi: as_role memalsukan hasil verifikasi sesi (tests/modules/conftest.py); client
tanpa override dipakai untuk 401. Database SQLite in-memory.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

import uuid

import pytest

from app.modules.auth.models import User
from app.modules.grading import rubric_rules, service
from app.shared.security import Role
from tests.modules.conftest import USER_ID_QA
from tests.modules.grading.rubric_payload import body_rubrik

URL = "/admin/rubrics"
BUKAN_ADMIN = [Role.VIEWER, Role.CONTRIBUTOR, Role.DEVELOPER_AI]


@pytest.fixture
def admin(as_role, db_session):
    db_session.add(User(id=USER_ID_QA, email="qa@veritask.test", name="Pengguna QA", role="admin"))
    db_session.commit()
    return as_role(Role.ADMIN)


# --- Positif ------------------------------------------------------------------


def test_admin_membuat_versi_201_dengan_isi_lengkap(admin):
    respons = admin.post(URL, json=body_rubrik())

    assert respons.status_code == 201, respons.text
    isi = respons.json()
    assert (isi["version_no"], isi["is_active"], isi["created_by_name"]) == (1, True, "Pengguna QA")
    assert isi["created_by"] == str(USER_ID_QA)
    assert [lv["score"] for lv in isi["levels"]] == [5, 4, 3, 2, 1, 0]
    assert isi["judge_prompt_quality"] == "Prompt kualitas sintetis."


def test_admin_melihat_daftar_versi_terbaru_di_atas_tanpa_prompt(admin):
    """Positif: SCRUM-228 daftar versi. Prompt hanya ada di detail."""
    admin.post(URL, json=body_rubrik())
    admin.post(URL, json=body_rubrik())

    respons = admin.get(URL)

    assert respons.status_code == 200
    daftar = respons.json()
    assert [(v["version_no"], v["is_active"]) for v in daftar] == [(2, True), (1, False)]
    assert all("judge_prompt_quality" not in v for v in daftar)


def test_daftar_kosong_200(admin):
    """Edge: halaman rubrik sebelum ada versi."""
    respons = admin.get(URL)

    assert (respons.status_code, respons.json()) == (200, [])


def test_admin_membuka_detail_versi(admin):
    dibuat = admin.post(URL, json=body_rubrik()).json()

    respons = admin.get(f"{URL}/{dibuat['id']}")

    assert respons.status_code == 200
    assert respons.json() == dibuat


# --- Negatif: isi -------------------------------------------------------------


def test_aturan_antar_field_menjadi_422_dengan_code_dan_field(admin):
    respons = admin.post(URL, json=body_rubrik(approved_at="2026-10-27"))

    assert respons.status_code == 422
    assert respons.json()["code"] == rubric_rules.APPROVED_AT_WITHOUT_NAME
    assert respons.json()["field"] == "approved_at"


def test_batas_field_menjadi_422_detail_pydantic(admin):
    respons = admin.post(URL, json=body_rubrik(aspects=[]))

    assert respons.status_code == 422
    assert respons.json()["detail"][0]["loc"] == ["body", "aspects"]


def test_nomor_versi_dari_body_ditolak(admin):
    """Negatif: version_no ditentukan server (AC7)."""
    respons = admin.post(URL, json=body_rubrik(version_no=7))

    assert respons.status_code == 422


def test_detail_versi_yang_tidak_ada_404(admin):
    respons = admin.get(f"{URL}/{uuid.uuid4()}")

    assert respons.status_code == 404
    assert respons.json()["code"] == service.RUBRIC_VERSION_NOT_FOUND


def test_id_bukan_uuid_422(admin):
    assert admin.get(f"{URL}/bukan-uuid").status_code == 422


def test_bentrok_nomor_versi_409(admin, monkeypatch):
    """Negatif: dua Admin menyimpan bersamaan; yang kalah diminta memuat ulang."""
    admin.post(URL, json=body_rubrik())
    from app.modules.grading import repository

    monkeypatch.setattr(repository, "max_version_no", lambda db: 0)

    respons = admin.post(URL, json=body_rubrik())

    assert respons.status_code == 409
    assert respons.json()["code"] == service.RUBRIC_VERSION_CONFLICT


# --- Negatif: akses (K7, OWASP A01) ---------------------------------------------


@pytest.mark.parametrize(
    ("metode", "jalur"),
    [("get", URL), ("get", f"{URL}/{uuid.uuid4()}"), ("post", URL)],
    ids=["daftar", "detail", "buat"],
)
def test_tanpa_sesi_401(client, metode, jalur):
    respons = client.request(
        metode.upper(), jalur, json=body_rubrik() if metode == "post" else None
    )

    assert respons.status_code == 401
    assert respons.json()["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("peran", BUKAN_ADMIN, ids=lambda p: p.value)
@pytest.mark.parametrize(
    ("metode", "jalur"),
    [("get", URL), ("get", f"{URL}/{uuid.uuid4()}"), ("post", URL)],
    ids=["daftar", "detail", "buat"],
)
def test_peran_selain_admin_403_dan_tidak_menyimpan(as_role, db_session, peran, metode, jalur):
    client = as_role(peran)

    respons = client.request(
        metode.upper(), jalur, json=body_rubrik() if metode == "post" else None
    )

    assert respons.status_code == 403
    assert respons.json()["code"] == "FORBIDDEN"
    assert service.list_versions(db_session) == []
