"""Test endpoint audit log, ujung ke ujung lewat HTTP.

PBI-18 AC3 (Admin mencari dan menyaring), AC4 (catatan dibuka kembali),
AC5 (Reviewer dibatasi), AC6 (unduh CSV dan PDF). Data audit disiapkan
dengan aksi sungguhan, jadi baris yang dicari lahir dari listener.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.modules.audit import service
from app.modules.audit.schemas import WIB
from app.modules.auth.models import User
from app.shared.security import Role
from tests.modules.conftest import USER_ID_QA

SUITE = {"entity_type": "suite"}


@pytest.fixture
def admin(as_role, db_session):
    """Admin yang tercatat di tabel users, supaya namanya bisa dicari (AC3)."""
    db_session.add(User(id=USER_ID_QA, email="admin.qa@veritask.ai", name="Admin QA", role="admin"))
    db_session.commit()
    client = as_role(Role.ADMIN)
    for nama in ("Pidana", "Perdata"):
        assert client.post("/suites", json={"name": nama}).status_code == 201
    return client


# --- Hak akses ----------------------------------------------------------------


def test_tanpa_sesi_ditolak(client):
    assert client.get("/audit-logs").status_code == 401


@pytest.mark.parametrize("role", [Role.AUTHOR, Role.VIEWER])
def test_peran_selain_admin_dan_reviewer_ditolak(as_role, role):
    client = as_role(role)

    assert client.get("/audit-logs").status_code == 403
    assert client.get("/audit-logs/export", params={"format": "csv"}).status_code == 403


# --- AC3 pencarian ------------------------------------------------------------


def test_admin_melihat_catatan_terbaru_lebih_dulu(admin):
    response = admin.get("/audit-logs", params=SUITE)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["after"]["name"] for item in body["items"]] == ["Perdata", "Pidana"]
    assert body["items"][0]["actor_name"] == "Admin QA"
    waktu = datetime.fromisoformat(body["items"][0]["occurred_at"])
    assert waktu.utcoffset() == timedelta(0)


def test_saring_jenis_data(admin):
    body = admin.get("/audit-logs", params={"entity_type": "user"}).json()

    assert [item["action"] for item in body["items"]] == ["user.added"]


def test_saring_nama_pengguna(admin):
    ditemukan = admin.get("/audit-logs", params={**SUITE, "actor": "admin qa"}).json()
    kosong = admin.get("/audit-logs", params={**SUITE, "actor": "tidak ada"}).json()

    assert ditemukan["total"] == 2
    assert kosong == {"items": [], "total": 0, "page": 1, "size": 20}


def test_saring_rentang_waktu_dalam_wib(admin):
    sekarang = datetime.now(WIB).replace(tzinfo=None)
    sebelum = (sekarang - timedelta(hours=1)).isoformat()
    sesudah = (sekarang + timedelta(hours=1)).isoformat()

    di_dalam = admin.get("/audit-logs", params={**SUITE, "from": sebelum, "to": sesudah}).json()
    nanti = admin.get("/audit-logs", params={**SUITE, "from": sesudah}).json()

    assert di_dalam["total"] == 2
    assert nanti["total"] == 0


def test_paginasi(admin):
    body = admin.get("/audit-logs", params={**SUITE, "page": 2, "size": 1}).json()

    assert (body["total"], body["page"], body["size"]) == (2, 2, 1)
    assert [item["after"]["name"] for item in body["items"]] == ["Pidana"]


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-10-09T00:00:00", "to": "2026-10-08T00:00:00"},
        {"entity_type": "session"},
        {"size": 101},
        {"actor": "   "},
    ],
)
def test_saringan_tidak_valid_422(admin, params):
    assert admin.get("/audit-logs", params=params).status_code == 422


# --- AC4 detail ---------------------------------------------------------------


def test_buka_kembali_satu_catatan(admin):
    item = admin.get("/audit-logs", params=SUITE).json()["items"][0]

    response = admin.get(f"/audit-logs/{item['id']}")

    assert response.status_code == 200
    assert response.json() == item


def test_catatan_tidak_ada_404(admin):
    assert admin.get("/audit-logs/999999").status_code == 404


# --- AC5 reviewer -------------------------------------------------------------


def test_reviewer_hanya_melihat_kasus_yang_ditugaskan(admin, as_role):
    item_id = admin.get("/audit-logs", params=SUITE).json()["items"][0]["id"]
    reviewer = as_role(Role.REVIEWER)

    daftar = reviewer.get("/audit-logs")
    kasus_lain = reviewer.get(
        "/audit-logs", params={"case_id": "00000000-0000-0000-0000-0000000000c1"}
    )
    detail = reviewer.get(f"/audit-logs/{item_id}")

    assert (daftar.status_code, daftar.json()["total"]) == (200, 0)
    assert kasus_lain.status_code == 403
    assert detail.status_code == 403


# --- AC6 unduh ----------------------------------------------------------------


def test_unduh_csv_dengan_saringan_yang_sama(admin):
    response = admin.get("/audit-logs/export", params={**SUITE, "format": "csv"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"].startswith('attachment; filename="audit-log-')
    baris = response.content.decode("utf-8-sig").splitlines()
    assert baris[0].startswith("Waktu (WIB),Pelaku")
    assert len(baris) == 3
    assert "Admin QA" in baris[1]
    assert "user.added" not in response.text


def test_unduh_pdf(admin):
    response = admin.get("/audit-logs/export", params={"format": "pdf"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].endswith('.pdf"')
    assert response.content.startswith(b"%PDF")


def test_format_unduhan_wajib_dan_dikenal(admin):
    assert admin.get("/audit-logs/export").status_code == 422
    assert admin.get("/audit-logs/export", params={"format": "xlsx"}).status_code == 422


def test_unduhan_terlalu_besar_ditolak(admin):
    with patch.object(service, "EXPORT_LIMIT", 1):
        response = admin.get("/audit-logs/export", params={"format": "csv"})

    assert response.status_code == 422
    assert response.json()["code"] == "AUDIT_EXPORT_TOO_LARGE"


def test_reviewer_mengunduh_hanya_cakupannya(as_role, admin):
    reviewer = as_role(Role.REVIEWER)

    response = reviewer.get("/audit-logs/export", params={"format": "csv"})

    assert response.status_code == 200
    assert len(response.content.decode("utf-8-sig").splitlines()) == 1
