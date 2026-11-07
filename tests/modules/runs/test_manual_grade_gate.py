"""Prasyarat Nilai sesi manual. PBI-22 (SCRUM-176) AC9; dipindah dari PBI-14 AC6; SCRUM-233.

Tiket: POST /measurements/{id}/grade (PBI-14-BE-5, #114) untuk sesi manual hanya bila semua
kasus dan percobaan terisi; kalau belum, 409 NOT_COMPLETE. D7: tombol Nilai sesi manual
tersedia setelah semua kasus dan percobaan terisi (status Lengkap, D8). D8: Admin klik
Nilai memindahkan Lengkap ke Dinilai; nilai ulang tidak menulis status lagi, dan Selesai
tetap Selesai.

ManualGradeGate dipanggil grade_session di dua titik:
- before_grading(): tanpa kunci (review #121: penilaian memanggil LLM judge bermenit-menit,
  dan kunci FOR UPDATE selama itu menahan semua simpan jawaban). None untuk sesi otomatis
  (aturan D4 #114 tetap berlaku); 409 SESSION_ARCHIVED untuk sesi yang diarsipkan, seperti
  sesi otomatis (review #121 blocker 2); 409 NOT_COMPLETE bila belum lengkap.
- after_grading(): baru mengunci baris sesi, membaca ulang statusnya, lalu Lengkap ->
  Dinilai lewat change_status (tabel transisi + audit PBI-18), tanpa commit sendiri.

Label: Positif = perilaku yang diharapkan, Negatif = ditolak, Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

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
    """Bagian ManualStatusStore yang dipakai gerbang: baca, kunci, dan tulis status."""

    def __init__(self, status: str | None, *, diarsipkan=False, terkunci: str | None = None):
        self.status = None if status is None else SessionStatus(status)
        self.archived_at = JAM if diarsipkan else None
        # Status saat dikunci; bisa berbeda dari saat dibaca (sesi berubah selama dinilai).
        self.terkunci = self.status if terkunci is None else SessionStatus(terkunci)
        self.kunci = 0
        self.perpindahan: list[tuple] = []

    def read_manual(self, session_id):
        if self.status is None:
            return None
        return SimpleNamespace(status=self.status, archived_at=self.archived_at)

    def lock_manual(self, session_id):
        self.kunci += 1
        return self.terkunci

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
def test_sesi_manual_lengkap_boleh_dinilai_tanpa_mengunci(gerbang, status):
    """Positif: AC9, nilai pertama, nilai ulang, Selesai lebih dulu (D8); belum ada kunci."""
    # Arrange
    penyimpan = _Penyimpan(status)

    # Act / Assert
    assert gerbang(penyimpan).before_grading(SESI) == status
    # Review #121: kunci baru diambil di after_grading, bukan selama LLM judge berjalan.
    assert penyimpan.kunci == 0


def test_sesi_manual_yang_diarsipkan_ditolak_seperti_sesi_otomatis(gerbang):
    """Negatif: review #121 blocker 2, Selesai bisa diarsipkan; arsip tidak dinilai (#114)."""
    # Act
    with pytest.raises(ConflictError) as galat:
        gerbang(_Penyimpan("finalized", diarsipkan=True)).before_grading(SESI)

    # Assert
    assert galat.value.code == "SESSION_ARCHIVED"


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
    hasil = gerbang(penyimpan).after_grading(SESI, now=JAM)

    # Assert
    assert penyimpan.perpindahan == perpindahan
    assert penyimpan.kunci == 1
    assert hasil == (perpindahan[0][0] if perpindahan else SessionStatus(status))


def test_setelah_dinilai_memakai_status_terbaru_saat_dikunci(gerbang):
    """Edge: review #121, sesi ditandai Selesai selama penilaian; status tidak ditimpa Dinilai."""
    # Arrange
    penyimpan = _Penyimpan("complete", terkunci="finalized")

    # Act
    hasil = gerbang(penyimpan).after_grading(SESI, now=JAM)

    # Assert
    assert (hasil, penyimpan.perpindahan) == (SessionStatus.FINALIZED, [])


def test_setelah_dinilai_sesi_otomatis_tidak_disentuh(gerbang):
    """Edge: after_grading untuk sesi bukan manual tidak menulis apa pun."""
    # Arrange
    penyimpan = _Penyimpan(None)

    # Act / Assert
    assert gerbang(penyimpan).after_grading(SESI, now=JAM) is None
    assert penyimpan.perpindahan == []


# Jalur ke tabel lewat ManualSessionRepository: tabel transisi dan audit sungguhan.


def _ubah(db, sesi_id, **kolom):
    db.execute(update(MeasurementSession).where(MeasurementSession.id == sesi_id).values(**kolom))
    db.commit()


def test_nilai_sesi_lengkap_tercatat_dinilai_di_database(siap_manual, gerbang):
    """Positif: D8, Lengkap -> Dinilai lewat change_status, dengan catatan status_changed."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual, repeat_count=1)
    _ubah(siap_manual.db, sesi_id, status="complete")
    pintu = gerbang(ManualSessionRepository(siap_manual.db))

    # Act
    pintu.before_grading(sesi_id)
    pintu.after_grading(sesi_id, now=JAM)
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


def test_nilai_sesi_selesai_yang_diarsipkan_ditolak_di_database(siap_manual, gerbang):
    """Negatif: review #121 blocker 2, arsip dibaca dari database (read_manual)."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual, repeat_count=1)
    _ubah(
        siap_manual.db,
        sesi_id,
        status="finalized",
        finalized_at=JAM,
        finalized_by=siap_manual.produk.created_by,
        archived_at=JAM,
        archived_by=siap_manual.produk.created_by,
    )

    # Act
    with pytest.raises(ConflictError) as galat:
        gerbang(ManualSessionRepository(siap_manual.db)).before_grading(sesi_id)

    # Assert
    assert galat.value.code == "SESSION_ARCHIVED"
