"""Audit setiap perpindahan status sesi dan terima apa adanya. PBI-12 AC9, PBI-18, SCRUM-214.

Bagian 2 SCRUM-214 (#97 "Part 2" butir 1). Listener audit hanya melihat flush ORM, sedangkan
status sesi ditulis lewat UPDATE Core bersyarat di tiga tempat:
- SessionStatusRepository.change_status (semua pasangan D4),
- SessionQueueRepository.start (queued -> running, giliran antrean),
- SessionQueueRepository.fail (queued -> failed, gagal disiapkan).
Ketiganya menulis satu baris measurement_session.status_changed di transaksi yang sama
dengan perubahan statusnya. Terima apa adanya dan hapus tandanya adalah flush ORM, jadi
dicatat lewat MeasurementSessionTracker.

Partisi masukan (E3): setiap pasangan sah di tabel D4 (satu kasus per pasangan), pasangan
yang ditolak, sesi yang sudah diterima apa adanya, pelaku pengguna vs sistem, dan
kegagalan penulisan audit di tengah transaksi.

Label: Positif = tercatat benar, Negatif = tidak boleh meninggalkan catatan,
Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.modules.audit import service as audit_service
from app.modules.audit.models import AuditLog
from app.modules.runs import repository
from app.modules.runs.models import MeasurementSession, SessionStatus
from app.modules.runs.session_transitions import ALLOWED_TRANSITIONS
from app.shared import request_context
from app.shared.exceptions import ConflictError
from app.shared.security import Role
from tests.modules.runs.test_session_accept_endpoint import (  # noqa: F401  fixture
    _tandai,
    _url,
    gagal_sebagian,
    pembuat,
    produk,
)
from tests.modules.runs.test_session_queue_repository import ITEMS, _baru, _salinan

JAM = datetime(2026, 11, 5, 7, 0, tzinfo=UTC)
AKSI_STATUS = "measurement_session.status_changed"

# Penyebab yang dikirim penulis status untuk tiap tujuan; paused wajib punya penyebab.
_PENYEBAB = {SessionStatus.PAUSED: "user", SessionStatus.FAILED: "error"}

PASANGAN_D4 = sorted(
    (asal.value, tujuan.value)
    for asal, semua_tujuan in ALLOWED_TRANSITIONS.items()
    for tujuan in semua_tujuan
)


@pytest.fixture
def antrean(db_session):
    return repository.SessionQueueRepository(db_session, clock=lambda: JAM)


@pytest.fixture
def status_repo(db_session):
    return repository.SessionStatusRepository(db_session)


@pytest.fixture
def sesi(antrean, db_session):
    """Sesi di status tertentu, disiapkan tanpa lewat aturan dan tanpa catatan audit."""

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


def _catatan(db_session, sesi_id: uuid.UUID, action: str = AKSI_STATUS) -> list[AuditLog]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(AuditLog)
            .where(AuditLog.entity_id == sesi_id, AuditLog.action == action)
            .order_by(AuditLog.id)
        )
    )


@pytest.mark.parametrize(("asal", "tujuan"), PASANGAN_D4)
def test_setiap_perpindahan_d4_menulis_satu_catatan(asal, tujuan, status_repo, sesi, db_session):
    """Positif: satu catatan per perpindahan, before = status lama, after = status baru."""
    # Arrange
    sesi_id = sesi(asal)
    penyebab = _PENYEBAB.get(SessionStatus(tujuan))

    # Act
    status_repo.change_status(sesi_id, tujuan, at=JAM, stop_reason=penyebab)

    # Assert
    (catatan,) = _catatan(db_session, sesi_id)
    assert catatan.entity_type == "measurement_session"
    assert catatan.before == {"status": asal}
    sesudah = {"status": tujuan} | ({"stop_reason": penyebab} if penyebab else {})
    assert catatan.after == sesudah


def test_penulis_tanpa_pengguna_tercatat_sebagai_sistem(status_repo, sesi, db_session):
    """Positif: worker (anggaran habis) tidak punya pelaku, jadi actor kosong."""
    # Arrange
    sesi_id = sesi("running")

    # Act
    status_repo.change_status(sesi_id, SessionStatus.PAUSED, at=JAM, stop_reason="budget")

    # Assert
    (catatan,) = _catatan(db_session, sesi_id)
    assert (catatan.actor_user_id, catatan.actor_role) == (None, None)
    assert catatan.after == {"status": "paused", "stop_reason": "budget"}


def test_pelaku_pengguna_ikut_tercatat(status_repo, sesi, db_session):
    """Positif: perpindahan dari request (Jeda oleh Admin, SCRUM-216) membawa pelakunya."""
    # Arrange
    sesi_id = sesi("running")
    admin_id = uuid.uuid4()
    request_context.bind(db_session, user_id=admin_id, role=Role.ADMIN)

    # Act
    status_repo.change_status(sesi_id, SessionStatus.PAUSED, at=JAM, stop_reason="user")

    # Assert
    (catatan,) = _catatan(db_session, sesi_id)
    assert (catatan.actor_user_id, catatan.actor_role) == (admin_id, "admin")


def test_perpindahan_yang_ditolak_tidak_menulis_catatan(status_repo, sesi, db_session):
    """Negatif: finished -> running ditolak 409 dan tidak meninggalkan jejak."""
    # Arrange
    sesi_id = sesi("finished")

    # Act
    with pytest.raises(ConflictError):
        status_repo.change_status(sesi_id, SessionStatus.RUNNING, at=JAM)

    # Assert
    assert _catatan(db_session, sesi_id) == []


def test_coba_ulang_sesi_yang_diterima_tidak_menulis_catatan(status_repo, sesi, db_session):
    """Negatif: sesi yang diterima apa adanya ditolak SESSION_ACCEPTED tanpa catatan."""
    # Arrange
    sesi_id = sesi("finished_with_failures")
    db_session.execute(
        update(MeasurementSession)
        .where(MeasurementSession.id == sesi_id)
        .values(
            accepted_as_is_at=JAM, accepted_as_is_by=uuid.uuid4(), accepted_as_is_reason="Alasan"
        )
    )
    db_session.commit()

    # Act
    with pytest.raises(ConflictError):
        status_repo.change_status(sesi_id, SessionStatus.RUNNING, at=JAM)

    # Assert
    assert _catatan(db_session, sesi_id) == []


def test_audit_gagal_status_ikut_batal(status_repo, sesi, db_session, monkeypatch):
    """Edge: status dan catatannya satu transaksi (D6b); audit gagal, status tetap lama."""
    # Arrange
    sesi_id = sesi("running")

    def gagal(*_args, **_kwargs):
        raise RuntimeError("audit_logs tidak bisa ditulis")

    monkeypatch.setattr(audit_service, "record", gagal)

    # Act
    with pytest.raises(RuntimeError):
        status_repo.change_status(sesi_id, SessionStatus.PAUSED, at=JAM, stop_reason="user")

    # Assert
    db_session.rollback()
    db_session.expire_all()
    assert db_session.get(MeasurementSession, sesi_id).status == SessionStatus.RUNNING


def test_giliran_antrean_mencatat_queued_ke_running(antrean, db_session):
    """Positif: start() menulis status lewat UPDATE-nya sendiri, tetap tercatat."""
    # Arrange
    sesi_id = antrean.create_queued(_baru()).id

    # Act
    antrean.start(sesi_id, items=ITEMS, product=_salinan(), started_at=JAM)

    # Assert
    (catatan,) = _catatan(db_session, sesi_id)
    assert (catatan.before, catatan.after) == ({"status": "queued"}, {"status": "running"})


def test_giliran_yang_kalah_tidak_menulis_catatan(antrean, db_session):
    """Negatif: start() kedua untuk sesi yang sama mendapat False dan tidak mencatat."""
    # Arrange
    sesi_id = antrean.create_queued(_baru()).id
    antrean.start(sesi_id, items=ITEMS, product=_salinan(), started_at=JAM)

    # Act
    kedua = antrean.start(
        sesi_id, items=ITEMS, product=_salinan(), started_at=JAM + timedelta(seconds=1)
    )

    # Assert
    assert kedua is False
    assert len(_catatan(db_session, sesi_id)) == 1


def test_gagal_disiapkan_mencatat_queued_ke_failed(antrean, db_session):
    """Positif: fail() menulis failed dengan penyebab error, tercatat sebagai sistem."""
    # Arrange
    sesi_id = antrean.create_queued(_baru()).id

    # Act
    antrean.fail(sesi_id, finished_at=JAM)

    # Assert
    (catatan,) = _catatan(db_session, sesi_id)
    assert catatan.before == {"status": "queued"}
    assert catatan.after == {"status": "failed", "stop_reason": "error"}


def test_gagal_disiapkan_sesi_yang_sudah_berjalan_tidak_mencatat(antrean, sesi, db_session):
    """Negatif: fail() tidak pernah menggagalkan sesi running, jadi tidak ada catatan."""
    # Arrange
    sesi_id = sesi("running")

    # Act
    berubah = antrean.fail(sesi_id, finished_at=JAM)

    # Assert
    assert berubah is False
    assert _catatan(db_session, sesi_id) == []


# Terima apa adanya: flush ORM, dicatat MeasurementSessionTracker.

AKSI_TERIMA = "measurement_session.accepted_as_is"
AKSI_HAPUS = "measurement_session.acceptance_cleared"


def test_terima_apa_adanya_tercatat_dengan_alasan_dan_pelaku(as_role, db_session, gagal_sebagian):  # noqa: F811
    """Positif: alasan tertulis masuk kolom reason, pelaku Admin dari sesi login."""
    # Act
    response = as_role(Role.ADMIN).patch(
        _url(gagal_sebagian.id), json={"reason": "Gagal karena penyedia"}
    )

    # Assert
    assert response.status_code == 200
    (catatan,) = _catatan(db_session, gagal_sebagian.id, AKSI_TERIMA)
    assert catatan.reason == "Gagal karena penyedia"
    assert catatan.actor_role == "admin"
    assert catatan.before == {"accepted_as_is_at": None}
    # Bandingkan waktunya, bukan teksnya: SQLite membuang zona waktu saat baris dibaca ulang.
    dicatat = datetime.fromisoformat(catatan.after["accepted_as_is_at"])
    dijawab = datetime.fromisoformat(response.json()["accepted_as_is_at"]).replace(tzinfo=UTC)
    assert dicatat == dijawab


def test_hapus_tanda_tercatat_dengan_alasan_lama(as_role, db_session, gagal_sebagian):  # noqa: F811
    """Positif: menghapus tanda mencatat alasan yang dihapus, supaya jejaknya tidak hilang."""
    # Arrange
    _tandai(db_session, gagal_sebagian, alasan="Alasan lama")

    # Act
    response = as_role(Role.ADMIN).delete(_url(gagal_sebagian.id))

    # Assert
    assert response.status_code == 200
    (catatan,) = _catatan(db_session, gagal_sebagian.id, AKSI_HAPUS)
    assert catatan.before["accepted_as_is_reason"] == "Alasan lama"
    assert catatan.after == {"accepted_as_is_at": None, "accepted_as_is_reason": None}


@pytest.mark.parametrize("body", [{"reason": "   "}, {"reason": ""}])
def test_terima_yang_ditolak_tidak_menulis_catatan(as_role, db_session, gagal_sebagian, body):  # noqa: F811
    """Negatif: alasan kosong ditolak 422 dan tidak meninggalkan catatan."""
    # Act
    response = as_role(Role.ADMIN).patch(_url(gagal_sebagian.id), json=body)

    # Assert
    assert response.status_code == 422
    assert _catatan(db_session, gagal_sebagian.id, AKSI_TERIMA) == []


def test_terima_tidak_menulis_catatan_status(as_role, db_session, gagal_sebagian):  # noqa: F811
    """Edge: terima apa adanya adalah flag, bukan status (AC9); tidak ada status_changed."""
    # Act
    as_role(Role.ADMIN).patch(_url(gagal_sebagian.id), json={"reason": "Alasan"})

    # Assert
    assert _catatan(db_session, gagal_sebagian.id) == []
