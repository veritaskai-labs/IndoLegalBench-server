"""Daftar kasus lintas suite dengan pencarian dan filter. PBI-4 SCRUM-81 SCRUM-339, AC3.

GET /cases mengganti satu panggilan per suite yang dipakai halaman Daftar kasus (client #114,
SCRUM-341): satu permintaan untuk semua suite, tiap baris membawa nama suite dan tanda arsip.
Baris memakai versi yang berlaku (versi approved terakhir, kalau belum ada versi saat ini), sama
seperti daftar per suite dan hitungan dashboard, supaya angka dan daftar tidak pernah berbeda.

Label: Positif = jalur normal, Negatif = harus ditolak, Edge = data batas (alasannya ditulis).
"""

import uuid
from datetime import UTC, datetime

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


def _ubah_pada(db_session, case_id: str, waktu: datetime) -> None:
    """Set when the in-effect version was last changed. now() has a 1 s step in SQLite."""
    db_session.get(Case, uuid.UUID(case_id)).current_version.updated_at = waktu
    db_session.commit()


def test_urutan_terbaru_diubah_lebih_dulu(penulis, db_session):
    """Positif: urutan sama dengan daftar per suite, yang terakhir diubah di atas."""
    # Arrange
    suite_id = _suite(penulis)
    lama = _buat(penulis, suite_id, case_code="A-001").json()["id"]
    baru = _buat(penulis, suite_id, case_code="B-001").json()["id"]
    _ubah_pada(db_session, lama, datetime(2026, 11, 1, tzinfo=UTC))
    _ubah_pada(db_session, baru, datetime(2026, 11, 2, tzinfo=UTC))

    # Act
    items = penulis.get("/cases").json()["items"]

    # Assert
    assert [item["id"] for item in items] == [baru, lama]


def test_waktu_ubah_sama_diurutkan_menurut_kode(penulis, db_session):
    """Edge: kasus dari satu impor CSV punya waktu ubah yang sama (now() satu transaksi). Tanpa
    urutan kedua yang pasti, halaman 1 dan 2 bisa menampilkan baris yang sama dua kali atau
    melewatkan satu baris."""
    # Arrange
    suite_id = _suite(penulis)
    ids = [_buat(penulis, suite_id, case_code=kode).json()["id"] for kode in ("C-1", "A-1", "B-1")]
    for case_id in ids:
        _ubah_pada(db_session, case_id, datetime(2026, 11, 1, tzinfo=UTC))

    # Act
    items = penulis.get("/cases").json()["items"]

    # Assert
    assert [item["case_code"] for item in items] == ["A-1", "B-1", "C-1"]


# --- Siklus 2: filter status dan suite -----------------------------------------------------


def test_filter_status_memakai_status_versi_yang_berlaku(penulis, db_session):
    """Positif: AC3, filter status hanya memuat kasus dengan status itu, dan total ikut terfilter."""
    # Arrange
    suite_id = _suite(penulis)
    draf = _buat(penulis, suite_id, case_code="DRAF-001").json()["id"]
    setuju = _buat(penulis, suite_id, case_code="SETUJU-001").json()["id"]
    _setujui(db_session, setuju)

    # Act
    body = penulis.get("/cases", params={"status": "draft"}).json()

    # Assert
    assert ([item["id"] for item in body["items"]], body["total"]) == ([draf], 1)


def test_filter_suite_hanya_memuat_kasus_suite_itu(penulis):
    """Positif: AC3, filter suite memuat kasus satu suite saja."""
    # Arrange
    kerja = _suite(penulis, "Ketenagakerjaan")
    pidana = _suite(penulis, "Pidana")
    phk = _buat(penulis, kerja, case_code="PHK-001").json()["id"]
    _buat(penulis, pidana, case_code="CURI-001")

    # Act
    body = penulis.get("/cases", params={"suite_id": kerja}).json()

    # Assert
    assert ([item["id"] for item in body["items"]], body["total"]) == ([phk], 1)


def test_filter_status_dan_suite_digabung_dengan_dan(penulis, db_session):
    """Positif: AC3, dua filter sekaligus berarti kasus harus memenuhi keduanya."""
    # Arrange
    kerja = _suite(penulis, "Ketenagakerjaan")
    pidana = _suite(penulis, "Pidana")
    cocok = _buat(penulis, kerja, case_code="PHK-001").json()["id"]
    _setujui(db_session, _buat(penulis, kerja, case_code="PHK-002").json()["id"])
    _buat(penulis, pidana, case_code="CURI-001")

    # Act
    body = penulis.get("/cases", params={"status": "draft", "suite_id": kerja}).json()

    # Assert
    assert [item["id"] for item in body["items"]] == [cocok]


@pytest.mark.parametrize(("status", "terlihat"), [("approved", True), ("draft", False)])
def test_kasus_yang_sedang_direvisi_difilter_menurut_versi_approved(
    penulis, db_session, status, terlihat
):
    """Edge: kasus approved dengan draf revisi punya dua status sekaligus. Filter harus memakai
    versi yang berlaku (approved), sama dengan hitungan dashboard; kalau memakai draf, kasus ini
    hilang dari filter Disetujui padahal dashboard menghitungnya di sana."""
    # Arrange
    case_id = _buat(penulis, _suite(penulis)).json()["id"]
    _setujui(db_session, case_id)
    assert penulis.post(f"/cases/{case_id}/versions").status_code == 201

    # Act
    items = penulis.get("/cases", params={"status": status}).json()["items"]

    # Assert
    assert ([item["id"] for item in items] == [case_id]) is terlihat


def test_suite_yang_tidak_ada_memberi_daftar_kosong(penulis):
    """Edge: halaman bisa masih menyimpan suite yang baru dihapus di filternya. Daftar kosong
    (bukan 404) membuat halaman tetap jalan dan cukup menampilkan "tidak ada kasus"."""
    # Arrange
    _buat(penulis, _suite(penulis))

    # Act
    response = penulis.get("/cases", params={"suite_id": str(uuid.uuid4())})

    # Assert
    assert response.status_code == 200
    assert (response.json()["items"], response.json()["total"]) == ([], 0)


@pytest.mark.parametrize(
    "params", [{"status": "selesai"}, {"suite_id": "bukan-uuid"}], ids=["status", "suite_id"]
)
def test_nilai_filter_yang_tidak_dikenal_ditolak(penulis, params):
    """Negatif: nilai filter di luar daftar ditolak 422, bukan diam-diam diabaikan, supaya salah
    ketik di client terlihat dan tidak menampilkan semua kasus seolah-olah sudah terfilter."""
    # Act
    response = penulis.get("/cases", params=params)

    # Assert
    assert response.status_code == 422
