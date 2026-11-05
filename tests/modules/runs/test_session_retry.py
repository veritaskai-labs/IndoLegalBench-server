"""Coba ulang yang gagal: hanya panggilan gagal yang dibuka lagi. SCRUM-77 SCRUM-218.

PBI-12 AC3 dan D4: dari Selesai dengan kegagalan, pengguna memilih kasus gagal
(per kasus atau semua). Attempt technical_failure yang dipilih kembali pending,
sesi pindah ke running lewat change_status (tabel D4 SCRUM-214), lalu runner
SCRUM-249 memanggil ulang hanya attempt pending itu. Hasil yang berhasil tidak
disentuh dan repetition_no tidak bertambah; upaya baru tercatat di call_logs
sebagai try_no + 1 (test_call_log_try_no).

Ditolak tanpa mengubah apa pun: sesi tidak ada (404), Developer AI pada sesi
orang lain (403, D10), sesi bukan Selesai dengan kegagalan atau sudah diterima
apa adanya (409), attempt bukan milik sesi atau tidak gagal (422), dan sesi
otomatis lain sedang berjalan (409, partial index #89).

Label: Positif = dibuka untuk dicoba ulang, Negatif = ditolak tanpa perubahan,
Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.runs import session_retry
from app.modules.runs.models import Attempt, AttemptStatus, MeasurementSession, SessionStatus
from app.modules.runs.schemas import SessionRetryRequest
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_list_endpoint import _mulai, _sesi

JAM = datetime(2026, 11, 5, 4, 0, tzinfo=UTC)
_OK = AttemptStatus.SUCCEEDED
_GAGAL = AttemptStatus.TECHNICAL_FAILURE


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-retry@veritask.test", name="Admin Retry", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Retry")
    db_session.add(item)
    db_session.commit()
    return item


@pytest.fixture
def sesi(db_session, produk, pembuat) -> MeasurementSession:
    """Dua kasus x dua pengulangan: kasus 1 (ok, gagal), kasus 2 (gagal, ok)."""
    baris = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED_WITH_FAILURES)
    _mulai(db_session, baris, [_OK, _GAGAL, _GAGAL, _OK])
    return baris


def _percobaan(db_session, sesi_id: uuid.UUID) -> list[Attempt]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(Attempt)
            .where(Attempt.session_id == sesi_id)
            .order_by(Attempt.snapshot_item_id, Attempt.repetition_no)
        )
    )


def _status(db_session, sesi_id: uuid.UUID) -> dict[uuid.UUID, AttemptStatus]:
    return {baris.id: baris.status for baris in _percobaan(db_session, sesi_id)}


def _gagal(db_session, sesi_id: uuid.UUID) -> list[uuid.UUID]:
    return [baris.id for baris in _percobaan(db_session, sesi_id) if baris.status == _GAGAL]


def _status_sesi(db_session, sesi_id: uuid.UUID) -> SessionStatus:
    db_session.expire_all()
    return db_session.get(MeasurementSession, sesi_id).status


def _ulang(db_session, sesi, *, actor=None, peran=Role.ADMIN, **isian):
    return session_retry.retry_failed_attempts(
        db_session,
        sesi.id,
        SessionRetryRequest(**isian),
        actor_id=actor or sesi.created_by,
        actor_role=peran,
        now=JAM,
    )


def test_semua_yang_gagal_dibuka_lagi_dan_sesi_berjalan(db_session, sesi):
    """Positif: AC3 "semua", hanya attempt gagal yang kembali pending, sesi running."""
    # Arrange
    gagal = _gagal(db_session, sesi.id)

    # Act
    hasil = _ulang(db_session, sesi, all_failed=True)

    # Assert
    assert hasil.status == SessionStatus.RUNNING
    assert sorted(hasil.retried_attempt_ids) == sorted(gagal)
    assert _status_sesi(db_session, sesi.id) == SessionStatus.RUNNING
    status = _status(db_session, sesi.id)
    assert [status[i] for i in gagal] == [AttemptStatus.PENDING] * 2
    assert sorted(s.value for s in status.values()) == [
        "pending",
        "pending",
        "succeeded",
        "succeeded",
    ]


def test_per_kasus_hanya_attempt_yang_dipilih_dibuka(db_session, sesi):
    """Positif: AC3 "per kasus", attempt gagal lain tetap technical_failure."""
    # Arrange
    dipilih, lainnya = _gagal(db_session, sesi.id)

    # Act
    hasil = _ulang(db_session, sesi, attempt_ids=[dipilih])

    # Assert
    assert hasil.retried_attempt_ids == [dipilih]
    status = _status(db_session, sesi.id)
    assert status[dipilih] == AttemptStatus.PENDING
    assert status[lainnya] == _GAGAL


def test_coba_ulang_tidak_menambah_pengulangan_atau_attempt(db_session, sesi):
    """Positif: AC3, percobaan ulang bukan pengulangan sampel; repetition_no tetap."""
    # Arrange
    sebelum = [(b.id, b.snapshot_item_id, b.repetition_no) for b in _percobaan(db_session, sesi.id)]

    # Act
    _ulang(db_session, sesi, all_failed=True)

    # Assert
    sesudah = [(b.id, b.snapshot_item_id, b.repetition_no) for b in _percobaan(db_session, sesi.id)]
    assert sesudah == sebelum


def test_id_yang_sama_dikirim_dua_kali_dibuka_sekali(db_session, sesi):
    """Edge: checkbox yang terkirim ganda tidak membuat galat atau hasil ganda."""
    # Arrange
    dipilih = _gagal(db_session, sesi.id)[0]

    # Act
    hasil = _ulang(db_session, sesi, attempt_ids=[dipilih, dipilih])

    # Assert
    assert hasil.retried_attempt_ids == [dipilih]


def test_developer_ai_boleh_mencoba_ulang_sesi_miliknya(db_session, produk):
    """Positif: D10 [USULAN PO], Developer AI mengendalikan sesi yang ia buat."""
    # Arrange
    dev = User(email="dev-retry@veritask.test", name="Dev AI", role=Role.DEVELOPER_AI)
    db_session.add(dev)
    db_session.commit()
    milik_dev = _sesi(db_session, produk, dev, status=SessionStatus.FINISHED_WITH_FAILURES)
    _mulai(db_session, milik_dev, [_OK, _GAGAL])

    # Act
    hasil = _ulang(db_session, milik_dev, actor=dev.id, peran=Role.DEVELOPER_AI, all_failed=True)

    # Assert
    assert hasil.status == SessionStatus.RUNNING


def test_developer_ai_tidak_boleh_mencoba_ulang_sesi_orang_lain(db_session, sesi):
    """Negatif: D10, Developer AI hanya sesi miliknya; sesi orang lain 403 tanpa perubahan."""
    # Arrange
    sebelum = _status(db_session, sesi.id)

    # Act + Assert
    with pytest.raises(ForbiddenError) as galat:
        _ulang(db_session, sesi, actor=uuid.uuid4(), peran=Role.DEVELOPER_AI, all_failed=True)
    assert galat.value.code == "NOT_YOUR_SESSION"
    assert _status(db_session, sesi.id) == sebelum


def test_sesi_tidak_ada_ditolak_404(db_session, sesi):
    """Negatif: id sesi asing menjadi 404 MEASUREMENT_NOT_FOUND."""
    # Act + Assert
    with pytest.raises(NotFoundError) as galat:
        session_retry.retry_failed_attempts(
            db_session,
            uuid.uuid4(),
            SessionRetryRequest(all_failed=True),
            actor_id=sesi.created_by,
            actor_role=Role.ADMIN,
            now=JAM,
        )
    assert galat.value.code == "MEASUREMENT_NOT_FOUND"


@pytest.mark.parametrize(
    "status",
    [SessionStatus.FINISHED, SessionStatus.RUNNING, SessionStatus.PAUSED, SessionStatus.QUEUED],
)
def test_hanya_sesi_selesai_dengan_kegagalan_yang_bisa_dicoba_ulang(
    db_session, produk, pembuat, status
):
    """Negatif: D4 hanya menggambar coba ulang dari Selesai dengan kegagalan (409)."""
    # Arrange
    lain = _sesi(db_session, produk, pembuat, status=status)
    _mulai(db_session, lain, [_OK, _GAGAL])
    sebelum = _status(db_session, lain.id)

    # Act + Assert
    with pytest.raises(ConflictError) as galat:
        _ulang(db_session, lain, all_failed=True)
    assert galat.value.code == "INVALID_TRANSITION"
    assert _status(db_session, lain.id) == sebelum
    assert _status_sesi(db_session, lain.id) == status


def test_sesi_yang_diterima_apa_adanya_tidak_bisa_dicoba_ulang(db_session, sesi):
    """Negatif: AC9 #97, tanda terima apa adanya harus dihapus dulu (409 SESSION_ACCEPTED)."""
    # Arrange
    sesi.accepted_as_is_at = JAM
    sesi.accepted_as_is_by = sesi.created_by
    sesi.accepted_as_is_reason = "Kegagalan dari penyedia"
    db_session.commit()
    sebelum = _status(db_session, sesi.id)

    # Act + Assert
    with pytest.raises(ConflictError) as galat:
        _ulang(db_session, sesi, all_failed=True)
    assert galat.value.code == "SESSION_ACCEPTED"
    assert _status(db_session, sesi.id) == sebelum


def test_attempt_yang_berhasil_tidak_bisa_dicoba_ulang(db_session, sesi):
    """Negatif: D4 "hasil yang sudah berhasil dikunci"; 422 dan tidak ada yang dibuka."""
    # Arrange
    berhasil = next(b.id for b in _percobaan(db_session, sesi.id) if b.status == _OK)
    gagal = _gagal(db_session, sesi.id)[0]
    sebelum = _status(db_session, sesi.id)

    # Act + Assert
    with pytest.raises(ValidationError) as galat:
        _ulang(db_session, sesi, attempt_ids=[gagal, berhasil])
    assert galat.value.code == "ATTEMPT_NOT_RETRYABLE"
    assert galat.value.details == {"attempt_ids": [str(berhasil)]}
    assert _status(db_session, sesi.id) == sebelum
    assert _status_sesi(db_session, sesi.id) == SessionStatus.FINISHED_WITH_FAILURES


def test_attempt_milik_sesi_lain_ditolak(db_session, sesi, produk, pembuat):
    """Negatif: id attempt dari sesi lain tidak boleh ikut dibuka lewat sesi ini (422)."""
    # Arrange
    lain = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED_WITH_FAILURES)
    _mulai(db_session, lain, [_GAGAL, _OK])
    asing = _gagal(db_session, lain.id)[0]

    # Act + Assert
    with pytest.raises(ValidationError) as galat:
        _ulang(db_session, sesi, attempt_ids=[asing])
    assert galat.value.code == "ATTEMPT_NOT_RETRYABLE"
    assert _status(db_session, lain.id)[asing] == _GAGAL


def test_sesi_tanpa_attempt_gagal_ditolak(db_session, produk, pembuat):
    """Edge: "semua yang gagal" pada sesi yang tidak punya attempt gagal tidak menjalankan apa pun."""
    # Arrange
    janggal = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED_WITH_FAILURES)
    _mulai(db_session, janggal, [_OK, _OK])

    # Act + Assert
    with pytest.raises(ValidationError) as galat:
        _ulang(db_session, janggal, all_failed=True)
    assert galat.value.code == "NO_FAILED_ATTEMPTS"
    assert _status_sesi(db_session, janggal.id) == SessionStatus.FINISHED_WITH_FAILURES


def test_ditolak_saat_sesi_otomatis_lain_sedang_berjalan(db_session, sesi, produk, pembuat):
    """Negatif: AC3 PBI-11, hanya satu sesi otomatis running; 409 dan semua dibatalkan."""
    # Arrange
    _sesi(db_session, produk, pembuat, label="Sedang jalan", status=SessionStatus.RUNNING)
    sebelum = _status(db_session, sesi.id)

    # Act + Assert
    with pytest.raises(ConflictError) as galat:
        _ulang(db_session, sesi, all_failed=True)
    assert galat.value.code == "ANOTHER_SESSION_RUNNING"
    assert _status(db_session, sesi.id) == sebelum
    assert _status_sesi(db_session, sesi.id) == SessionStatus.FINISHED_WITH_FAILURES


@pytest.mark.parametrize(
    "isian",
    [
        {},
        {"all_failed": False},
        {"attempt_ids": []},
        {"attempt_ids": [str(uuid.uuid4())], "all_failed": True},
        {"attempt_ids": ["bukan-uuid"]},
    ],
)
def test_isian_coba_ulang_harus_tepat_satu_pilihan(isian):
    """Negatif: body memilih per kasus (attempt_ids) atau semua (all_failed), tidak keduanya."""
    # Act + Assert
    with pytest.raises(PydanticValidationError):
        SessionRetryRequest(**isian)
