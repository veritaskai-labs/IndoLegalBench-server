"""Satu jalan tulis status sesi di database. PBI-12 AC7, AC9, AC10, SCRUM-214.

SessionStatusRepository.change_status adalah satu-satunya UPDATE status sesi yang
melewati tabel D4 (session_transitions). UPDATE-nya bersyarat (status sekarang harus
salah satu asal yang sah), jadi dua penulis bersamaan tidak bisa sama-sama menang;
balapan itu diuji di tests/integration/test_session_status_change_pg.py.

Kolom D2 stopped_at dan stop_reason mencatat penyebab sesi berhenti (keputusan dengan
Adra, 4 Nov): budget, restart, dan user membuat sesi paused; kesalahan fatal membuat
sesi failed dengan stop_reason error.

Label: Positif = perpindahan sah tersimpan, Negatif = ditolak tanpa mengubah baris,
Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.modules.runs import repository, schemas, session_ports
from app.modules.runs.models import MeasurementSession, SessionStatus
from app.shared.exceptions import ConflictError, NotFoundError, ValidationError

JAM = datetime(2026, 11, 4, 7, 0, tzinfo=UTC)
NANTI = JAM + timedelta(minutes=30)


def _baru() -> session_ports.NewSession:
    return session_ports.NewSession(
        label="Run status",
        purpose=schemas.MeasurementPurpose.TRIAL,
        product_id=uuid.uuid4(),
        suite_ids=(uuid.uuid4(),),
        case_filter=schemas.CaseFilter.DEV,
        repetitions=1,
        created_by=uuid.uuid4(),
    )


@pytest.fixture
def antrean(db_session):
    return repository.SessionQueueRepository(db_session, clock=lambda: JAM)


@pytest.fixture
def status_repo(db_session):
    return repository.SessionStatusRepository(db_session)


@pytest.fixture
def sesi(antrean, db_session):
    """Buat sesi lalu taruh di status tertentu tanpa lewat aturan (menyiapkan keadaan awal)."""

    def buat(status: str) -> uuid.UUID:
        sesi_id = antrean.create_queued(_baru()).id
        db_session.execute(
            update(MeasurementSession)
            .where(MeasurementSession.id == sesi_id)
            .values(status=SessionStatus(status))
        )
        db_session.commit()
        return sesi_id

    return buat


def _baris(db_session, sesi_id) -> MeasurementSession:
    db_session.expire_all()
    return db_session.get(MeasurementSession, sesi_id)


def _utc(nilai: datetime | None) -> datetime | None:
    return None if nilai is None else nilai.replace(tzinfo=UTC)


def test_anggaran_habis_menjadi_paused_dengan_penyebabnya(status_repo, sesi, db_session):
    """Positif: running -> paused menyimpan stopped_at dan stop_reason budget (AC10)."""
    # Arrange
    sesi_id = sesi("running")

    # Act
    status_repo.change_status(sesi_id, SessionStatus.PAUSED, at=JAM, stop_reason="budget")

    # Assert
    baris = _baris(db_session, sesi_id)
    assert baris.status == SessionStatus.PAUSED
    assert baris.stop_reason == "budget"
    assert _utc(baris.stopped_at) == JAM
    assert baris.finished_at is None


@pytest.mark.parametrize("akhir", ["finished", "finished_with_failures"])
def test_selesai_mengisi_finished_at_tanpa_penyebab_berhenti(status_repo, sesi, db_session, akhir):
    """Positif: running -> finished / finished_with_failures mengisi finished_at."""
    # Arrange
    sesi_id = sesi("running")

    # Act
    status_repo.change_status(sesi_id, akhir, at=JAM)

    # Assert
    baris = _baris(db_session, sesi_id)
    assert baris.status == SessionStatus(akhir)
    assert _utc(baris.finished_at) == JAM
    assert baris.stop_reason is None
    assert baris.stopped_at is None


def test_lanjut_dari_paused_menghapus_penyebab_berhenti(status_repo, sesi, db_session):
    """Edge: paused -> running (Lanjutkan, SCRUM-216) mengosongkan stopped_at dan stop_reason."""
    # Arrange
    sesi_id = sesi("running")
    status_repo.change_status(sesi_id, SessionStatus.PAUSED, at=JAM, stop_reason="budget")

    # Act
    status_repo.change_status(sesi_id, SessionStatus.RUNNING, at=NANTI)

    # Assert
    baris = _baris(db_session, sesi_id)
    assert baris.status == SessionStatus.RUNNING
    assert baris.stop_reason is None
    assert baris.stopped_at is None


def test_perpindahan_di_luar_d4_ditolak_dan_baris_tidak_berubah(status_repo, sesi, db_session):
    """Negatif: finished -> running ditolak 409 INVALID_TRANSITION; status tetap finished."""
    # Arrange
    sesi_id = sesi("finished")

    # Act
    with pytest.raises(ConflictError) as error:
        status_repo.change_status(sesi_id, SessionStatus.RUNNING, at=JAM)

    # Assert
    assert error.value.code == "INVALID_TRANSITION"
    assert error.value.details == {"from": "finished", "to": "running"}
    assert _baris(db_session, sesi_id).status == SessionStatus.FINISHED


def test_paused_tanpa_penyebab_ditolak(status_repo, sesi, db_session):
    """Negatif: berhenti tanpa penyebab tidak bisa dijelaskan ke Admin; status tetap running."""
    # Arrange
    sesi_id = sesi("running")

    # Act
    with pytest.raises(ValidationError) as error:
        status_repo.change_status(sesi_id, SessionStatus.PAUSED, at=JAM)

    # Assert
    assert error.value.field == "stop_reason"
    assert _baris(db_session, sesi_id).status == SessionStatus.RUNNING


def test_sesi_tidak_ada_ditolak_404(status_repo):
    """Negatif: id asing bukan konflik status, tetapi tidak ditemukan."""
    # Act / Assert
    with pytest.raises(NotFoundError):
        status_repo.change_status(uuid.uuid4(), SessionStatus.FINISHED, at=JAM)


def test_gagal_sebelum_mulai_mencatat_penyebab_error(antrean, db_session):
    """Positif: fail() antrean (queued -> failed) ikut mencatat stop_reason error."""
    # Arrange
    sesi_id = antrean.create_queued(_baru()).id

    # Act
    berhasil = antrean.fail(sesi_id, finished_at=JAM)

    # Assert
    assert berhasil is True
    baris = _baris(db_session, sesi_id)
    assert baris.status == SessionStatus.FAILED
    assert baris.stop_reason == "error"
    assert _utc(baris.stopped_at) == JAM
    assert _utc(baris.finished_at) == JAM


def _terima(db_session, sesi_id: uuid.UUID) -> None:
    db_session.execute(
        update(MeasurementSession)
        .where(MeasurementSession.id == sesi_id)
        .values(
            accepted_as_is_at=JAM,
            accepted_as_is_by=uuid.uuid4(),
            accepted_as_is_reason="Kegagalan dari penyedia",
        )
    )
    db_session.commit()


def test_sesi_yang_diterima_apa_adanya_tidak_bisa_dicoba_ulang(status_repo, sesi, db_session):
    """Negatif: diterima = selesai sepenuhnya (AC9); coba ulang ditolak 409 SESSION_ACCEPTED."""
    # Arrange
    sesi_id = sesi("finished_with_failures")
    _terima(db_session, sesi_id)

    # Act
    with pytest.raises(ConflictError) as error:
        status_repo.change_status(sesi_id, SessionStatus.RUNNING, at=NANTI)

    # Assert
    assert error.value.code == "SESSION_ACCEPTED"
    assert _baris(db_session, sesi_id).status == SessionStatus.FINISHED_WITH_FAILURES


def test_coba_ulang_boleh_setelah_tanda_terima_dihapus(status_repo, sesi, db_session):
    """Edge: tanpa tanda terima, finished_with_failures -> running (coba ulang) tetap sah."""
    # Arrange
    sesi_id = sesi("finished_with_failures")

    # Act
    status_repo.change_status(sesi_id, SessionStatus.RUNNING, at=NANTI)

    # Assert
    assert _baris(db_session, sesi_id).status == SessionStatus.RUNNING


def test_coba_ulang_mengosongkan_waktu_selesai_lama(status_repo, sesi, db_session):
    """Negatif: running -> finished_with_failures -> running tidak membawa finished_at lama.

    Ditemukan Roben di review #97: GET menjawab status running dengan finished_at terisi,
    padahal sesinya belum selesai lagi.
    """
    # Arrange
    sesi_id = sesi("running")
    status_repo.change_status(sesi_id, SessionStatus.FINISHED_WITH_FAILURES, at=JAM)

    # Act
    status_repo.change_status(sesi_id, SessionStatus.RUNNING, at=NANTI)

    # Assert
    assert _baris(db_session, sesi_id).finished_at is None


def test_coba_ulang_lalu_dijeda_tetap_tanpa_waktu_selesai(status_repo, sesi, db_session):
    """Edge: coba ulang yang dijeda belum selesai, jadi finished_at tetap kosong."""
    # Arrange
    sesi_id = sesi("running")
    status_repo.change_status(sesi_id, SessionStatus.FINISHED_WITH_FAILURES, at=JAM)
    status_repo.change_status(sesi_id, SessionStatus.RUNNING, at=NANTI)

    # Act
    status_repo.change_status(sesi_id, SessionStatus.PAUSED, at=NANTI, stop_reason="user")

    # Assert
    baris = _baris(db_session, sesi_id)
    assert (baris.status, baris.finished_at) == (SessionStatus.PAUSED, None)


def test_coba_ulang_yang_selesai_lagi_mencatat_waktu_selesai_baru(status_repo, sesi, db_session):
    """Positif: selesai kedua kali memakai waktu selesai yang baru, bukan yang pertama."""
    # Arrange
    sesi_id = sesi("running")
    status_repo.change_status(sesi_id, SessionStatus.FINISHED_WITH_FAILURES, at=JAM)
    status_repo.change_status(sesi_id, SessionStatus.RUNNING, at=JAM + timedelta(minutes=5))

    # Act
    status_repo.change_status(sesi_id, SessionStatus.FINISHED, at=NANTI)

    # Assert
    assert _utc(_baris(db_session, sesi_id).finished_at) == NANTI
