"""Prasyarat Nilai sesi manual. PBI-22 (SCRUM-176) AC9; dipindah dari PBI-14 AC6; SCRUM-233.

Tiket: POST /measurements/{id}/grade (PBI-14-BE-5, #114) untuk sesi manual hanya bila semua
kasus dan percobaan terisi; kalau belum, 409 NOT_COMPLETE. D7: tombol Nilai sesi manual
tersedia setelah semua kasus dan percobaan terisi (status Lengkap, D8). D8: Admin klik
Nilai memindahkan Lengkap ke Dinilai; nilai ulang tidak menulis status lagi, dan Selesai
tetap Selesai.

ManualGradeGate dipanggil grade_session di dua titik:
- before_grading(): None untuk sesi otomatis (aturan D4 #114 tetap berlaku), status sesi
  untuk sesi manual yang lengkap, atau 409 bila belum;
- after_grading(): Lengkap -> Dinilai lewat change_status (tabel transisi + audit PBI-18),
  di transaksi yang sama dengan hasil penilaian (tanpa commit sendiri).

Label: Positif = perilaku yang diharapkan, Negatif = ditolak, Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from app.modules.audit.models import AuditLog
from app.modules.runs.models import MeasurementSession, SessionStatus
from app.modules.runs.repository import ManualSessionRepository
from app.shared.exceptions import ConflictError
from tests.modules.runs.test_manual_session_detail import _sesi_manual

SESI = uuid.UUID("00000000-0000-0000-0000-0000000000e5")
JAM = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


class _Penyimpan:
    """Bagian ManualStatusStore yang dipakai gerbang: kunci status dan tulis status."""

    def __init__(self, status: str | None):
        self.status = None if status is None else SessionStatus(status)
        self.perpindahan: list[tuple] = []

    def lock_manual(self, session_id):
        return self.status

    def change_status(self, session_id, target, *, at, finalized_by=None, commit):
        self.perpindahan.append((SessionStatus(target), commit))


@pytest.fixture
def gerbang():
    # Diimpor lewat fixture: kelas yang belum ada membuat tes merah, bukan galat koleksi.
    from app.modules.runs.manual_grade_gate import ManualGradeGate

    return ManualGradeGate


def test_sesi_otomatis_tidak_diatur_gerbang_manual(gerbang):
    """Positif: AC9, sesi otomatis tetap memakai aturan D4 di #114 (None = bukan manual)."""
    # Act / Assert
    assert gerbang(_Penyimpan(None)).before_grading(SESI) is None


@pytest.mark.parametrize("status", ["not_filled", "partially_filled"])
def test_sesi_manual_belum_lengkap_ditolak_409(gerbang, status):
    """Negatif: AC9, masih ada kasus atau percobaan kosong = 409 NOT_COMPLETE."""
    # Arrange
    penyimpan = _Penyimpan(status)

    # Act
    with pytest.raises(ConflictError) as galat:
        gerbang(penyimpan).before_grading(SESI)

    # Assert
    assert galat.value.code == "NOT_COMPLETE"
    assert penyimpan.perpindahan == []


@pytest.mark.parametrize("status", ["complete", "graded", "finalized"])
def test_sesi_manual_lengkap_boleh_dinilai(gerbang, status):
    """Positif: AC9, nilai pertama (Lengkap), nilai ulang (Dinilai), Selesai lebih dulu (D8)."""
    # Act / Assert
    assert gerbang(_Penyimpan(status)).before_grading(SESI) == status


def test_sesi_manual_berstatus_d4_409_bukan_500(gerbang):
    """Negatif: baris manual lama berstatus D4 (sebelum SCRUM-230), sama seperti hitung ulang."""
    # Act
    with pytest.raises(ConflictError) as galat:
        gerbang(_Penyimpan("running")).before_grading(SESI)

    # Assert
    assert galat.value.code == "MANUAL_SESSION_STATUS_INVALID"


@pytest.mark.parametrize(
    ("status", "perpindahan"),
    [
        ("complete", [(SessionStatus.GRADED, False)]),
        # Edge: nilai ulang dan Selesai tidak menulis status (dan tidak ada audit ganda).
        ("graded", []),
        ("finalized", []),
    ],
)
def test_setelah_dinilai_lengkap_menjadi_dinilai(gerbang, status, perpindahan):
    """Positif: D8, Lengkap -> Dinilai tanpa commit sendiri; hasil dan status batal bersama."""
    # Arrange
    penyimpan = _Penyimpan(status)

    # Act
    gerbang(penyimpan).after_grading(SESI, SessionStatus(status), now=JAM)

    # Assert
    assert penyimpan.perpindahan == perpindahan


# Jalur ke tabel lewat ManualSessionRepository: tabel transisi dan audit sungguhan.


def _status(db, sesi_id, status):
    db.execute(
        update(MeasurementSession).where(MeasurementSession.id == sesi_id).values(status=status)
    )
    db.commit()


def test_nilai_sesi_lengkap_tercatat_dinilai_di_database(siap_manual, gerbang):
    """Positif: D8, Lengkap -> Dinilai lewat change_status, dengan catatan status_changed."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual, repeat_count=1)
    _status(siap_manual.db, sesi_id, "complete")
    pintu = gerbang(ManualSessionRepository(siap_manual.db))

    # Act
    status = pintu.before_grading(sesi_id)
    pintu.after_grading(sesi_id, status, now=JAM)
    siap_manual.db.commit()

    # Assert
    siap_manual.db.expire_all()
    assert siap_manual.db.get(MeasurementSession, sesi_id).status == SessionStatus.GRADED
    catatan = siap_manual.db.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == sesi_id, AuditLog.action == "measurement_session.status_changed"
        )
    ).all()
    assert [(c.before, c.after) for c in catatan][-1] == (
        {"status": "complete"},
        {"status": "graded"},
    )


def test_nilai_sesi_baru_dibuat_ditolak_di_database(siap_manual, gerbang):
    """Negatif: AC9, sesi yang baru dibuat Admin (Belum terisi) belum bisa dinilai."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual, repeat_count=1)

    # Act
    with pytest.raises(ConflictError) as galat:
        gerbang(ManualSessionRepository(siap_manual.db)).before_grading(sesi_id)

    # Assert
    assert galat.value.code == "NOT_COMPLETE"
