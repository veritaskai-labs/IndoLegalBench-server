"""Status sesi manual: hitung ulang dan Tandai selesai. PBI-22 (SCRUM-176) AC8, AC13; SCRUM-230.

D8: status dihitung ulang server setiap kali jawaban disimpan. recompute_manual_status
dipanggil SCRUM-231 di transaksi simpan jawaban, jadi ia tidak commit sendiri: jawaban
dan statusnya tersimpan atau batal bersama. Rentang tanggal jawaban (AC13) diperbarui
dari jawaban yang terisi saja.

Tandai selesai (POST /manual-measurements/{id}/finalize): hanya dari Lengkap atau Dinilai
(keputusan SA 7 November 2026), hanya sekali, dan oleh Admin (siapa yang boleh masih TBA;
standar tiket: Admin). Pelaku dan waktunya tersimpan (D9), dan perpindahan statusnya
tercatat di audit seperti status D4 (PBI-18).

Label: Positif = perilaku yang diharapkan, Negatif = ditolak, Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from app.modules.audit.models import AuditLog
from app.modules.runs import repository
from app.modules.runs.models import (
    Attempt,
    MeasurementSession,
    SessionStatus,
    SessionType,
)
from app.shared.exceptions import ConflictError, NotFoundError, ValidationError
from app.shared.security import Role
from tests.modules.cases.test_cases import ADMIN_LAIN
from tests.modules.runs.test_manual_session_endpoint import URL, _badan

HARI_1 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
HARI_3 = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
HARI_8 = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
JAM = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
AKSI_STATUS = "measurement_session.status_changed"


@pytest.fixture
def modul():
    # Diimpor lewat fixture: modul yang belum ada membuat tes merah, bukan galat koleksi.
    from app.modules.runs import manual_session_status

    return manual_session_status


def _sesi_manual(siap_manual, **ubah) -> uuid.UUID:
    """Sesi manual 3 kasus x 1 percobaan lewat POST, seperti Admin membuatnya."""
    badan = _badan(siap_manual, repeat_count=1) | ubah
    respons = siap_manual.sebagai(Role.ADMIN).post(URL, json=badan)
    assert respons.status_code == 201, respons.text
    return uuid.UUID(respons.json()["session_id"])


def _percobaan(db, sesi_id) -> list[Attempt]:
    return list(
        db.scalars(select(Attempt).where(Attempt.session_id == sesi_id).order_by(Attempt.id))
    )


def _isi(db, sesi_id, tanggal: list[datetime | None]) -> None:
    """Tandai percobaan pertama terisi, satu per tanggal; None = belum terisi (SCRUM-231)."""
    for attempt, diperoleh in zip(_percobaan(db, sesi_id), tanggal, strict=False):
        if diperoleh is not None:
            attempt.filled_at = JAM
        attempt.answered_at_platform = diperoleh
    db.commit()


def _ubah_status(db, sesi_id, status: SessionStatus) -> None:
    db.execute(
        update(MeasurementSession).where(MeasurementSession.id == sesi_id).values(status=status)
    )
    db.commit()


def _status(db, sesi_id) -> SessionStatus:
    db.expire_all()
    return db.get(MeasurementSession, sesi_id).status


def _catatan(db, sesi_id) -> list[AuditLog]:
    db.expire_all()
    return list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.entity_id == sesi_id, AuditLog.action == AKSI_STATUS)
            .order_by(AuditLog.id)
        )
    )


@pytest.mark.parametrize(
    ("terisi", "status"),
    [(0, "not_filled"), (1, "partially_filled"), (3, "complete")],
)
def test_status_dihitung_ulang_dari_jawaban_terisi(modul, siap_manual, terisi, status):
    """Positif: D8, 0 dari 3 Belum terisi, 1 dari 3 Terisi sebagian, 3 dari 3 Lengkap."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)
    _isi(siap_manual.db, sesi_id, [HARI_1] * terisi)

    # Act
    hasil = modul.recompute_manual_status(siap_manual.db, sesi_id, now=JAM)
    siap_manual.db.commit()

    # Assert
    assert hasil == status
    assert _status(siap_manual.db, sesi_id) == status


def test_perubahan_status_tercatat_di_audit_dan_yang_tetap_tidak(modul, siap_manual):
    """Positif: PBI-18, satu catatan per perpindahan; hitung ulang tanpa perubahan tidak mencatat."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)
    modul.recompute_manual_status(siap_manual.db, sesi_id, now=JAM)
    _isi(siap_manual.db, sesi_id, [HARI_1])

    # Act
    modul.recompute_manual_status(siap_manual.db, sesi_id, now=JAM)
    siap_manual.db.commit()

    # Assert
    (catatan,) = _catatan(siap_manual.db, sesi_id)
    assert (catatan.before, catatan.after) == (
        {"status": "not_filled"},
        {"status": "partially_filled"},
    )


def test_rentang_tanggal_hanya_dari_jawaban_terisi(modul, siap_manual):
    """Positif: AC13, tanggal percobaan yang belum terisi tidak ikut rentang."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)
    _isi(siap_manual.db, sesi_id, [HARI_3, HARI_1])
    belum = _percobaan(siap_manual.db, sesi_id)[2]
    belum.answered_at_platform = HARI_8
    siap_manual.db.commit()

    # Act
    modul.recompute_manual_status(siap_manual.db, sesi_id, now=JAM)
    siap_manual.db.commit()

    # Assert
    siap_manual.db.expire_all()
    sesi = siap_manual.db.get(MeasurementSession, sesi_id)
    assert (sesi.answer_date_from, sesi.answer_date_to) == (
        HARI_1.replace(tzinfo=None),
        HARI_3.replace(tzinfo=None),
    )


def test_hitung_ulang_tidak_commit_sendiri(modul, siap_manual):
    """Edge: SCRUM-231 memegang transaksinya; batal simpan jawaban ikut membatalkan status."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)
    _isi(siap_manual.db, sesi_id, [HARI_1])

    # Act
    modul.recompute_manual_status(siap_manual.db, sesi_id, now=JAM)
    siap_manual.db.rollback()

    # Assert
    assert _status(siap_manual.db, sesi_id) == SessionStatus.NOT_FILLED


@pytest.mark.parametrize("tetap", [SessionStatus.GRADED, SessionStatus.FINALIZED])
def test_dinilai_dan_selesai_tidak_dihitung_ulang(modul, siap_manual, tetap):
    """Edge: D8, Dinilai dan Selesai ditulis aksi Admin, bukan dari jumlah jawaban."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)
    _isi(siap_manual.db, sesi_id, [HARI_1, HARI_1, HARI_1])
    _ubah_status(siap_manual.db, sesi_id, tetap)

    # Act
    hasil = modul.recompute_manual_status(siap_manual.db, sesi_id, now=JAM)

    # Assert
    assert hasil == tetap
    assert _catatan(siap_manual.db, sesi_id) == []


@pytest.mark.parametrize("lama", [SessionStatus.QUEUED, SessionStatus.RUNNING])
def test_sesi_manual_berstatus_d4_ditolak_409(modul, siap_manual, lama):
    """Negatif: review #116, baris manual lama berstatus D4 tidak boleh membuat 500 di SCRUM-231."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)
    _ubah_status(siap_manual.db, sesi_id, lama)

    # Act
    with pytest.raises(ConflictError) as galat:
        modul.recompute_manual_status(siap_manual.db, sesi_id, now=JAM)

    # Assert
    assert galat.value.code == "MANUAL_SESSION_STATUS_INVALID"


def test_sesi_otomatis_atau_tidak_ada_bukan_sesi_manual(modul, siap_manual):
    """Negatif: hitung ulang hanya untuk sesi manual; id lain ditolak 404."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)
    siap_manual.db.execute(
        update(MeasurementSession)
        .where(MeasurementSession.id == sesi_id)
        .values(session_type=SessionType.AUTOMATIC)
    )
    siap_manual.db.commit()

    # Act / Assert
    for target in (sesi_id, uuid.uuid4()):
        with pytest.raises(NotFoundError) as galat:
            modul.recompute_manual_status(siap_manual.db, target, now=JAM)
        assert galat.value.code == "MANUAL_SESSION_NOT_FOUND"


@pytest.mark.parametrize("asal", [SessionStatus.COMPLETE, SessionStatus.GRADED])
def test_admin_menandai_selesai_dari_lengkap_atau_dinilai(siap_manual, asal):
    """Positif: AC8, pelaku dan waktu Selesai tersimpan, perpindahannya tercatat di audit."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)
    _ubah_status(siap_manual.db, sesi_id, asal)

    # Act
    respons = siap_manual.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/finalize")

    # Assert
    assert respons.status_code == 200, respons.text
    isi = respons.json()
    assert (isi["status"], isi["finalized_by"]) == ("finalized", str(ADMIN_LAIN))
    siap_manual.db.expire_all()
    sesi = siap_manual.db.get(MeasurementSession, sesi_id)
    assert (sesi.status, sesi.finalized_by) == (SessionStatus.FINALIZED, ADMIN_LAIN)
    assert sesi.finalized_at is not None
    (catatan,) = _catatan(siap_manual.db, sesi_id)
    assert catatan.after == {"status": "finalized"}


@pytest.mark.parametrize("asal", [SessionStatus.NOT_FILLED, SessionStatus.PARTIALLY_FILLED])
def test_selesai_ditolak_selama_masih_ada_jawaban_kosong(siap_manual, asal):
    """Negatif: AC8, Selesai hanya bila semua kasus dan percobaan terisi."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)
    _ubah_status(siap_manual.db, sesi_id, asal)

    # Act
    respons = siap_manual.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/finalize")

    # Assert
    assert respons.status_code == 409
    assert respons.json()["code"] == "MANUAL_SESSION_INCOMPLETE"
    assert _status(siap_manual.db, sesi_id) == asal


def test_selesai_tidak_bisa_diulang(siap_manual):
    """Negatif: AC8, setelah Selesai sesi tidak dapat dibuka kembali atau ditandai lagi."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)
    _ubah_status(siap_manual.db, sesi_id, SessionStatus.COMPLETE)
    admin = siap_manual.sebagai(Role.ADMIN)
    admin.post(f"{URL}/{sesi_id}/finalize")

    # Act
    respons = admin.post(f"{URL}/{sesi_id}/finalize")

    # Assert
    assert respons.status_code == 409
    assert respons.json()["code"] == "MANUAL_SESSION_FINALIZED"


@pytest.mark.parametrize("peran", [Role.CONTRIBUTOR, Role.VIEWER, Role.DEVELOPER_AI])
def test_selain_admin_tidak_bisa_menandai_selesai(siap_manual, peran):
    """Negatif: siapa yang boleh menandai Selesai masih TBA; standar tiket Admin saja."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)
    _ubah_status(siap_manual.db, sesi_id, SessionStatus.COMPLETE)

    # Act
    respons = siap_manual.sebagai(peran).post(f"{URL}/{sesi_id}/finalize")

    # Assert
    assert respons.status_code == 403
    assert _status(siap_manual.db, sesi_id) == SessionStatus.COMPLETE


def test_selesai_untuk_sesi_yang_tidak_ada_ditolak_404(siap_manual):
    """Negatif: id yang bukan sesi manual tidak bisa ditandai Selesai."""
    # Act
    respons = siap_manual.sebagai(Role.ADMIN).post(f"{URL}/{uuid.uuid4()}/finalize")

    # Assert
    assert respons.status_code == 404
    assert respons.json()["code"] == "MANUAL_SESSION_NOT_FOUND"


def test_penulis_status_menolak_selesai_tanpa_pelaku(siap_manual):
    """Negatif: D9, finalized_at tanpa finalized_by tidak bisa dijelaskan, jadi ditolak di depan."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)
    _ubah_status(siap_manual.db, sesi_id, SessionStatus.COMPLETE)

    # Act
    with pytest.raises(ValidationError) as galat:
        repository.SessionStatusRepository(siap_manual.db).change_status(
            sesi_id, SessionStatus.FINALIZED, at=JAM
        )

    # Assert
    assert galat.value.code == "FINALIZED_BY_REQUIRED"
    assert _status(siap_manual.db, sesi_id) == SessionStatus.COMPLETE
