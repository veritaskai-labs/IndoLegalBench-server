"""Sesi yang layak jadi dasar laporan. PBI-12 AC7, AC9, D4, SCRUM-214.

#97 "Part 2" butir 3. Aturan D4 (dikonfirmasi untuk PBI-15/16):
- finished: layak.
- finished_with_failures: layak hanya bila Admin menandai terima apa adanya.
- cancelled: tidak layak, dan tidak bisa diunduh sampai dilanjutkan dan selesai (AC7).
- queued, running, paused, failed: tidak layak.
- finalized (sesi manual, D8 PBI-22): layak, karena Selesai adalah penguncian final.
  Status manual lain belum selesai diisi atau belum dikunci, jadi tidak layak.

Satu fungsi murni (session_rules.is_report_eligible) dipakai GET detail sebagai
report_eligible, supaya FE (SCRUM-220) dan laporan (PBI-15/16) memakai aturan yang sama.

Partisi masukan (E3): 12 status x tanda terima (ada / tidak) = 24 kasus, lalu jalur
HTTP untuk pasangan yang berubah karena aksi Admin (terima, hapus tanda).

Label: Positif = layak, Negatif = tidak layak, Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.modules.auth.models import User
from app.modules.runs.models import SessionStatus
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_list_endpoint import _sesi

DITERIMA = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)

_LAYAK = {
    (SessionStatus.FINISHED, False),
    (SessionStatus.FINISHED, True),
    (SessionStatus.FINISHED_WITH_FAILURES, True),
    # String, bukan anggota enum: StrEnum sama dengan string-nya, dan file ini tetap
    # terkumpul sebelum enum punya status D8.
    ("finalized", False),
    ("finalized", True),
}

KASUS = [
    pytest.param(status, diterima, (status, diterima) in _LAYAK, id=f"{status}-diterima={diterima}")
    for status in SessionStatus
    for diterima in (False, True)
]


@pytest.fixture
def is_report_eligible():
    # Diimpor lewat fixture: fungsi yang belum ada membuat tes merah, bukan galat koleksi.
    from app.modules.runs.session_rules import is_report_eligible

    return is_report_eligible


@pytest.mark.parametrize(("status", "diterima", "layak"), KASUS)
def test_aturan_layak_laporan_per_status_dan_tanda_terima(
    is_report_eligible, status, diterima, layak
):
    """Positif/Negatif: tabel D4 lengkap, satu kasus per pasangan status x tanda terima."""
    # Act
    hasil = is_report_eligible(status, accepted_as_is_at=DITERIMA if diterima else None)

    # Assert
    assert hasil is layak


def test_status_dari_database_sebagai_teks_diterima(is_report_eligible):
    """Edge: kolom dibaca sebagai teks di beberapa jalur; aturannya tetap sama."""
    # Act + Assert
    assert is_report_eligible("finished", accepted_as_is_at=None) is True
    assert is_report_eligible("cancelled", accepted_as_is_at=None) is False


def test_status_tidak_dikenal_ditolak(is_report_eligible):
    """Negatif: status di luar enum tidak diam-diam dianggap tidak layak."""
    # Act + Assert
    with pytest.raises(ValueError):
        is_report_eligible("budget_stopped", accepted_as_is_at=None)


# Jalur HTTP: GET /measurements/{id} membawa report_eligible.


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-laporan@veritask.test", name="Admin Laporan", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Laporan")
    db_session.add(item)
    db_session.commit()
    return item


def _layak(client, session_id: uuid.UUID) -> bool:
    response = client.get(f"/measurements/{session_id}")
    assert response.status_code == 200, response.text
    return response.json()["report_eligible"]


@pytest.mark.parametrize(
    ("status", "layak"),
    [
        (SessionStatus.FINISHED, True),
        (SessionStatus.FINISHED_WITH_FAILURES, False),
        (SessionStatus.CANCELLED, False),
        (SessionStatus.RUNNING, False),
    ],
)
def test_detail_sesi_membawa_report_eligible(as_role, db_session, produk, pembuat, status, layak):
    """Positif/Negatif: nilainya dari aturan yang sama, terlihat di detail sesi."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=status)

    # Act + Assert
    assert _layak(as_role(Role.ADMIN), sesi.id) is layak


def test_terima_apa_adanya_membuat_sesi_layak_laporan(as_role, db_session, produk, pembuat):
    """Positif: setelah PATCH accept, GET detail menjawab report_eligible true."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED_WITH_FAILURES)
    admin = as_role(Role.ADMIN)

    # Act
    admin.patch(f"/measurements/{sesi.id}/accept", json={"reason": "Gagal dari penyedia"})

    # Assert
    assert _layak(admin, sesi.id) is True


def test_hapus_tanda_terima_membuat_sesi_tidak_layak_lagi(as_role, db_session, produk, pembuat):
    """Negatif: DELETE accept mengembalikan finished_with_failures ke tidak layak."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED_WITH_FAILURES)
    admin = as_role(Role.ADMIN)
    admin.patch(f"/measurements/{sesi.id}/accept", json={"reason": "Gagal dari penyedia"})

    # Act
    admin.delete(f"/measurements/{sesi.id}/accept")

    # Assert
    assert _layak(admin, sesi.id) is False


def test_developer_ai_melihat_kelayakan_yang_sama(as_role, db_session, produk, pembuat):
    """Edge: kelayakan bukan rahasia; Developer AI melihat nilai yang sama dengan Admin."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED)

    # Act + Assert
    assert _layak(as_role(Role.DEVELOPER_AI), sesi.id) is True
