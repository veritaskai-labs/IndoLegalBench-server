"""Jumlah kasus selesai per sesi dari tabel attempts (SQLite). SCRUM-77 SCRUM-265.

Kasus selesai bila seluruh pengulangannya sudah punya hasil akhir (AC5): succeeded,
technical_failure, atau jawaban manual yang sudah terisi (filled_at, PBI-22 D8).
Coba ulang mengembalikan percobaan gagal ke pending (reopen_for_retry), jadi kasusnya
kembali belum selesai sampai coba ulangnya selesai.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: data yang tidak boleh ikut terhitung.
- Edge: batas atau kasus pinggir.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.modules.auth.models import User
from app.modules.runs import repository
from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    AttemptStatus,
    CaseSnapshot,
    CaseSnapshotItem,
    MeasurementSession,
    SessionPurpose,
    SessionStatus,
    SessionType,
    SplitFilter,
)
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis

S = AttemptStatus.SUCCEEDED
F = AttemptStatus.TECHNICAL_FAILURE
P = AttemptStatus.PENDING
R = AttemptStatus.RUNNING
SAAT = datetime(2026, 11, 8, 10, 0, tzinfo=UTC)


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="admin-265@veritask.test", name="Admin", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk 265")
    db_session.add(item)
    db_session.commit()
    return item


def _sesi(
    db_session,
    produk,
    pembuat,
    kasus: list[list[AttemptStatus]],
    *,
    status: SessionStatus = SessionStatus.RUNNING,
    terisi: bool = False,
) -> MeasurementSession:
    """Satu sesi sintetis. Tiap elemen kasus = status per pengulangan kasus itu.

    terisi meniru sesi manual: percobaan tetap pending tetapi filled_at terisi.
    """
    sesi = MeasurementSession(
        label="Sesi 265",
        purpose=SessionPurpose.TRIAL,
        session_type=SessionType.AUTOMATIC,
        product_id=produk.id,
        repetitions=max((len(k) for k in kasus), default=1),
        split_filter=SplitFilter.BOTH,
        created_by=pembuat.id,
        status=status,
    )
    db_session.add(sesi)
    db_session.flush()
    if kasus:
        snapshot = CaseSnapshot(session_id=sesi.id)
        db_session.add(snapshot)
        db_session.flush()
    for pengulangan in kasus:
        item = CaseSnapshotItem(
            snapshot_id=snapshot.id,
            case_id=uuid.uuid4(),
            case_version_id=uuid.uuid4(),
            body={},
        )
        db_session.add(item)
        db_session.flush()
        for nomor, status_percobaan in enumerate(pengulangan, 1):
            db_session.add(
                Attempt(
                    session_id=sesi.id,
                    snapshot_item_id=item.id,
                    repetition_no=nomor,
                    source=AttemptSource.AUTOMATIC,
                    status=status_percobaan,
                    filled_at=SAAT if terisi else None,
                )
            )
    db_session.commit()
    return sesi


def test_sesi_baru_belum_punya_kasus(db_session, produk, pembuat):
    """Edge: sesi antre belum punya attempts, jadi tidak muncul (dibaca 0 dari 0)."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, [], status=SessionStatus.QUEUED)

    # Act
    hasil = repository.case_completion(db_session, [sesi.id])

    # Assert
    assert hasil == {}


def test_sesi_berjalan_menghitung_kasus_yang_semua_pengulangannya_selesai(
    db_session, produk, pembuat
):
    """Positif: hanya kasus pertama yang ketiga pengulangannya selesai."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, [[S, S, F], [S, R, P], [P, P, P]])

    # Act
    hasil = repository.case_completion(db_session, [sesi.id])

    # Assert
    assert hasil == {sesi.id: (1, 3)}


def test_sesi_dijeda_tetap_menghitung_dari_attempts(db_session, produk, pembuat):
    """Positif: jeda tidak mengubah hitungan, kasus yang sudah selesai tetap selesai."""
    # Arrange
    sesi = _sesi(
        db_session, produk, pembuat, [[S, S], [S, P]], status=SessionStatus.PAUSED
    )

    # Act
    hasil = repository.case_completion(db_session, [sesi.id])

    # Assert
    assert hasil == {sesi.id: (1, 2)}


def test_sesi_selesai_semua_kasus_selesai_termasuk_gagal_teknis(db_session, produk, pembuat):
    """Positif: gagal teknis adalah hasil akhir, jadi kasusnya tetap selesai."""
    # Arrange
    sesi = _sesi(
        db_session, produk, pembuat, [[S, S], [F, F]], status=SessionStatus.FINISHED_WITH_FAILURES
    )

    # Act
    hasil = repository.case_completion(db_session, [sesi.id])

    # Assert
    assert hasil == {sesi.id: (2, 2)}


def test_coba_ulang_membuat_kasus_kembali_belum_selesai(db_session, produk, pembuat):
    """Edge: reopen_for_retry mengembalikan gagal teknis ke pending sampai coba ulangnya selesai."""
    # Arrange
    sesi = _sesi(
        db_session, produk, pembuat, [[S, S], [S, F]], status=SessionStatus.FINISHED_WITH_FAILURES
    )
    gagal = db_session.query(Attempt).filter(Attempt.status == F).one()

    # Act
    repository.reopen_for_retry(db_session, sesi.id, [gagal.id], at=SAAT)
    hasil = repository.case_completion(db_session, [sesi.id])

    # Assert
    assert hasil == {sesi.id: (1, 2)}


def test_jawaban_manual_terisi_dihitung_selesai(db_session, produk, pembuat):
    """Positif: percobaan manual tetap pending, filled_at yang menandai selesai (D8)."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, [[P, P], [P, P]], terisi=True)

    # Act
    hasil = repository.case_completion(db_session, [sesi.id])

    # Assert
    assert hasil == {sesi.id: (2, 2)}


def test_kasus_sesi_lain_tidak_tercampur(db_session, produk, pembuat):
    """Negatif: dua sesi dibaca sekaligus, hitungannya terpisah; sesi di luar daftar tidak ikut."""
    # Arrange
    satu = _sesi(db_session, produk, pembuat, [[S], [S]], status=SessionStatus.FINISHED)
    dua = _sesi(db_session, produk, pembuat, [[P], [S], [R]], status=SessionStatus.FINISHED)
    lain = _sesi(db_session, produk, pembuat, [[S]], status=SessionStatus.FINISHED)

    # Act
    hasil = repository.case_completion(db_session, [satu.id, dua.id])

    # Assert
    assert hasil == {satu.id: (2, 2), dua.id: (1, 3)}
    assert lain.id not in hasil


def test_tanpa_id_sesi_hasilnya_kosong(db_session):
    """Edge: daftar kosong tidak perlu query."""
    # Act + Assert
    assert repository.case_completion(db_session, []) == {}
