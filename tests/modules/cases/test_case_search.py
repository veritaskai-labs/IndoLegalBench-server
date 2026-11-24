"""Daftar kasus lintas suite dengan pencarian dan filter. PBI-4 SCRUM-81 SCRUM-339, AC3.

GET /cases mengganti satu panggilan per suite yang dipakai halaman Daftar kasus (client #114,
SCRUM-341): satu permintaan untuk semua suite, tiap baris membawa nama suite dan tanda arsip.
Baris memakai versi yang berlaku (versi approved terakhir, kalau belum ada versi saat ini), sama
seperti daftar per suite dan hitungan dashboard, supaya angka dan daftar tidak pernah berbeda.

Label: Positif = jalur normal, Negatif = harus ditolak, Edge = data batas (alasannya ditulis).
"""

import uuid

import pytest

from app.modules.cases.models import Case
from app.shared.security import Role
from tests.modules.cases.test_cases import _badan, _buat, _setujui, _suite


@pytest.fixture
def penulis(as_role):
    return as_role(Role.CONTRIBUTOR)


# --- Siklus 1: akses dan daftar lintas suite ----------------------------------------------


def test_kontributor_melihat_kasus_dari_semua_suite_dengan_nama_suitenya(penulis):
    """Positif: AC3, satu permintaan memuat kasus dari dua suite, masing-masing dengan suitenya."""
    # Arrange
    kerja = _suite(penulis, "Ketenagakerjaan")
    pidana = _suite(penulis, "Pidana")
    phk = _buat(penulis, kerja, case_code="PHK-001").json()
    curi = _buat(penulis, pidana, case_code="CURI-001").json()

    # Act
    response = penulis.get("/cases")

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["page"], body["size"]) == (2, 1, 20)
    baris = {item["id"]: item for item in body["items"]}
    assert set(baris) == {phk["id"], curi["id"]}
    assert (baris[phk["id"]]["suite_id"], baris[phk["id"]]["suite_name"]) == (
        kerja,
        "Ketenagakerjaan",
    )
    assert (baris[curi["id"]]["suite_id"], baris[curi["id"]]["suite_name"]) == (pidana, "Pidana")
    assert baris[phk["id"]]["suite_archived"] is False


def test_baris_membawa_ringkasan_yang_sama_dengan_daftar_per_suite(penulis):
    """Positif: AC3, halaman cukup memakai baris ini; isinya sama dengan GET /suites/{id}/cases."""
    # Arrange
    suite_id = _suite(penulis)
    _buat(penulis, suite_id)
    per_suite = penulis.get(f"/suites/{suite_id}/cases").json()[0]

    # Act
    items = penulis.get("/cases").json()["items"]

    # Assert
    assert [item["id"] for item in items] == [per_suite["id"]]
    for kunci, nilai in per_suite.items():
        assert items[0][kunci] == nilai, kunci


def test_admin_boleh_melihat_daftar_kasus(as_role):
    """Positif: AC3, Admin mengelola semua kasus, jadi ikut melihat daftar lintas suite."""
    # Arrange
    admin = as_role(Role.ADMIN)
    _buat(admin, _suite(admin))

    # Act
    response = admin.get("/cases")

    # Assert
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.parametrize("role", [Role.VIEWER, Role.DEVELOPER_AI])
def test_peran_lain_ditolak(as_role, role):
    """Negatif: AC3 hanya untuk Kontributor dan Admin. Developer AI tetap memakai daftar per suite
    yang menyamarkan kasus test; daftar ini tidak menyamarkan apa pun, jadi harus 403."""
    # Arrange
    client = as_role(role)

    # Act
    response = client.get("/cases")

    # Assert
    assert response.status_code == 403


def test_tanpa_login_ditolak(client):
    """Negatif: tanpa sesi tidak ada daftar kasus sama sekali."""
    # Act
    response = client.get("/cases")

    # Assert
    assert response.status_code == 401


def test_suite_arsip_tetap_tampil_dengan_tandanya(as_role):
    """Edge: suite yang diarsipkan masih menyimpan kasus lama. Halaman menampilkannya dengan label
    Arsip; kalau baris ini hilang, kasus di suite arsip tidak bisa ditemukan lagi."""
    # Arrange
    admin = as_role(Role.ADMIN)
    suite_id = _suite(admin)
    _buat(admin, suite_id)
    assert admin.post(f"/suites/{suite_id}/archive").status_code == 200

    # Act
    items = admin.get("/cases").json()["items"]

    # Assert
    assert [item["suite_archived"] for item in items] == [True]


def test_draft_tanpa_tag_tetap_tampil(penulis, db_session):
    """Edge: AC2/AC8, draft hasil impor atau duplikasi belum punya tag dev/test (NULL). Join atau
    filter yang memakai tag akan membuang baris ini, padahal justru kasus inilah yang harus dicari
    untuk dilengkapi."""
    # Arrange
    dibuat = _buat(penulis, _suite(penulis)).json()
    kasus = db_session.get(Case, uuid.UUID(dibuat["id"]))
    kasus.current_version.split_tag = None
    db_session.commit()

    # Act
    items = penulis.get("/cases").json()["items"]

    # Assert
    assert [(item["id"], item["split_tag"]) for item in items] == [(dibuat["id"], None)]


def test_kasus_dengan_revisi_terbuka_tampil_sekali_dengan_versi_yang_berlaku(penulis, db_session):
    """Edge: kasus approved yang sedang direvisi punya dua versi. Daftar harus menampilkan satu baris
    dengan judul dan status versi approved; kalau memakai versi draf, status di daftar berbeda dari
    hitungan dashboard dan kasus terlihat belum disetujui."""
    # Arrange
    case_id = _buat(penulis, _suite(penulis)).json()["id"]
    _setujui(db_session, case_id)
    assert penulis.post(f"/cases/{case_id}/versions").status_code == 201
    badan = _badan()
    badan["identity"]["title"] = "Judul revisi yang belum disetujui"
    assert penulis.put(f"/cases/{case_id}", json=badan).status_code == 200

    # Act
    items = penulis.get("/cases").json()["items"]

    # Assert
    assert [(item["id"], item["status"], item["title"]) for item in items] == [
        (case_id, "approved", "PHK sepihak")
    ]


def test_urutan_terbaru_diubah_lebih_dulu(penulis):
    """Positif: urutan sama dengan daftar per suite (terakhir diubah di atas), dan stabil untuk halaman."""
    # Arrange
    suite_id = _suite(penulis)
    lama = _buat(penulis, suite_id, case_code="A-001").json()["id"]
    baru = _buat(penulis, suite_id, case_code="B-001").json()["id"]

    # Act
    items = penulis.get("/cases").json()["items"]

    # Assert
    assert [item["id"] for item in items] == [baru, lama]
