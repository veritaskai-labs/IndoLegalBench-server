"""Waktu terakhir diubah pada daftar kasus. PBI-4 SCRUM-81 SCRUM-379, AC3 (feedback UAT).

Kolom "Terakhir Kali Diubah" membaca `updated_at` dari GET /cases. Syaratnya: terisi untuk semua
kasus (tulis manual, hasil impor, duplikat; nilai awal sama dengan waktu dibuat) dan terbarui
setiap kasus diubah (edit isi, ubah tag, keputusan review). Kolom ini dipasang database
(server_default dan onupdate), jadi tes di sini membuktikan setiap jalur memang melewatinya.

SQLite menghitung now() per detik, jadi untuk membuktikan "terbarui" waktu lama dipasang dulu
secara eksplisit (2020), lalu perubahan harus menggesernya ke waktu sekarang.

Label: Positif = jalur normal, Edge = data batas (alasannya ditulis).
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.modules.cases import service
from app.modules.cases.models import Case, CaseStatus
from app.shared.security import Role
from tests.modules.cases.test_case_import_api import _berkas, _impor, _lengkap
from tests.modules.cases.test_cases import _badan, _buat, _setujui, _suite

LAMA = datetime(2020, 1, 1, tzinfo=UTC)
BATAS = "2021-01-01"  # ISO strings compare like dates; anything newer than 2020 is a fresh update


@pytest.fixture
def penulis(as_role):
    return as_role(Role.CONTRIBUTOR)


def _versi_berlaku(db_session, case_id: str):
    kasus = db_session.get(Case, uuid.UUID(case_id))
    return kasus.latest_approved_version or kasus.current_version


def _buat_tua(db_session, case_id: str) -> None:
    """Pretend the in-effect version was last changed in 2020."""
    _versi_berlaku(db_session, case_id).updated_at = LAMA
    db_session.commit()


def _baris(client, case_id: str) -> dict:
    items = client.get("/cases", params={"size": 100}).json()["items"]
    return next(item for item in items if item["id"] == case_id)


def test_kasus_manual_terisi_dan_sama_dengan_waktu_dibuat(penulis):
    """Positif: nilai awal updated_at = created_at, jadi kasus baru tidak pernah tanpa waktu."""
    # Arrange
    case_id = _buat(penulis, _suite(penulis)).json()["id"]

    # Act
    detail = penulis.get(f"/cases/{case_id}").json()
    baris = _baris(penulis, case_id)

    # Assert
    assert detail["updated_at"] == detail["created_at"]
    assert baris["updated_at"] == detail["updated_at"]


def test_kasus_hasil_impor_terisi_dan_sama_dengan_waktu_dibuat(penulis):
    """Positif: kasus hasil impor CSV (D7) ikut punya waktu, bukan kosong atau 1970."""
    # Arrange
    response = _impor(penulis, _berkas(_lengkap()))
    assert response.status_code in (200, 201), response.text

    # Act
    items = penulis.get("/cases").json()["items"]

    # Assert
    assert [item["origin"] for item in items] == ["import"]
    detail = penulis.get(f"/cases/{items[0]['id']}").json()
    assert items[0]["updated_at"] == detail["created_at"] > BATAS


def test_kasus_duplikat_terisi_dan_sama_dengan_waktu_dibuat(penulis, db_session):
    """Positif: salinan adalah kasus baru, waktunya waktu salin, bukan waktu kasus asal."""
    # Arrange
    asal = _buat(penulis, _suite(penulis)).json()["id"]
    _setujui(db_session, asal)
    _buat_tua(db_session, asal)

    # Act
    salinan = penulis.post(f"/cases/{asal}/duplicate").json()

    # Assert
    baris = _baris(penulis, salinan["id"])
    assert baris["updated_at"] == salinan["created_at"] > BATAS
    assert _baris(penulis, asal)["updated_at"] < BATAS


def test_edit_isi_menggeser_waktu_ke_sekarang(penulis, db_session):
    """Positif: PUT /cases/{id} memperbarui updated_at."""
    # Arrange
    case_id = _buat(penulis, _suite(penulis)).json()["id"]
    _buat_tua(db_session, case_id)
    assert _baris(penulis, case_id)["updated_at"] < BATAS
    badan = _badan()
    badan["identity"]["title"] = "Judul yang diperbaiki"

    # Act
    response = penulis.put(f"/cases/{case_id}", json=badan)

    # Assert
    assert response.status_code == 200
    assert _baris(penulis, case_id)["updated_at"] > BATAS


def test_ubah_tag_menggeser_waktu_versi_yang_berlaku(as_role, db_session):
    """Positif: PBI-9 AC3, Admin mengubah tag kasus approved; waktu yang tampil di daftar ikut
    bergeser karena versi yang berlaku (approved) yang diubah."""
    # Arrange
    admin = as_role(Role.ADMIN)
    case_id = _buat(admin, _suite(admin)).json()["id"]
    _setujui(db_session, case_id)
    _buat_tua(db_session, case_id)

    # Act
    response = admin.patch(
        f"/cases/{case_id}/split-tag", json={"split_tag": "test", "confirm": True}
    )

    # Assert
    assert response.status_code == 200, response.text
    assert _baris(admin, case_id)["updated_at"] > BATAS


@pytest.mark.parametrize("disetujui", [True, False], ids=["disetujui", "perlu-revisi"])
def test_keputusan_review_menggeser_waktu(penulis, db_session, disetujui):
    """Positif: keputusan round (setuju atau revisi) mengubah status versi, jadi waktunya ikut."""
    # Arrange
    case_id = _buat(penulis, _suite(penulis)).json()["id"]
    versi = _versi_berlaku(db_session, case_id)
    versi.status = CaseStatus.IN_REVIEW
    versi.updated_at = LAMA
    db_session.commit()
    assert _baris(penulis, case_id)["updated_at"] < BATAS

    # Act
    service.apply_review_decision(db_session, versi.id, approved=disetujui)
    db_session.commit()

    # Assert
    assert _baris(penulis, case_id)["updated_at"] > BATAS


def test_membaca_daftar_tidak_mengubah_waktu(penulis, db_session):
    """Edge: melihat atau mengurutkan daftar tidak boleh menggeser waktu, kalau tidak kolom
    "Terakhir Kali Diubah" selalu terlihat baru dan urutannya tidak berarti."""
    # Arrange
    case_id = _buat(penulis, _suite(penulis)).json()["id"]
    _buat_tua(db_session, case_id)

    # Act
    penulis.get("/cases", params={"sort": "updated_at", "order": "asc"})
    penulis.get(f"/cases/{case_id}")

    # Assert
    assert _baris(penulis, case_id)["updated_at"] < BATAS
