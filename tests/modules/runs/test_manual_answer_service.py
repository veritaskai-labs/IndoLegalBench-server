"""Unit test ManualAnswerService dengan penyimpanan palsu. PBI-22 AC4 sampai AC7; SCRUM-231.

Tanpa database: yang diuji keputusan layanannya, bukan SQL-nya. Penyimpanan palsu mencatat
setiap panggilan, jadi urutan kunci, arsip, tulis, dan commit ikut terbukti.

Label: Positif = perilaku yang diharapkan, Negatif = harus ditolak, Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.modules.runs.manual_answers import ManualAnswerService
from app.modules.runs.models import AttemptSource, SessionStatus
from app.modules.runs.schemas import ManualAnswerSaveRequest
from app.modules.runs.session_ports import ManualAnswerHistoryRow, ManualAttemptRow
from app.shared.exceptions import (
    ConfirmationRequiredError,
    ConflictError,
    NotFoundError,
    ValidationError,
)

SESI = uuid.UUID("11111111-1111-1111-1111-111111111111")
KASUS = uuid.UUID("22222222-2222-2222-2222-222222222222")
PERCOBAAN = uuid.UUID("33333333-3333-3333-3333-333333333333")
AKTOR = uuid.UUID("44444444-4444-4444-4444-444444444444")
SAAT = datetime(2026, 11, 7, 14, 0, tzinfo=UTC)


def _slot(*, filled: bool) -> ManualAttemptRow:
    return ManualAttemptRow(
        id=PERCOBAAN,
        snapshot_item_id=KASUS,
        repetition_no=1,
        filled=filled,
        answered_at_platform=SAAT if filled else None,
        source=AttemptSource.MANUAL,
        answer_md="Jawaban lama" if filled else None,
        platform_refused=False,
        note=None,
        filled_by=AKTOR if filled else None,
        filled_at=SAAT if filled else None,
    )


class FakeStore:
    """Penyimpanan palsu yang mencatat panggilan, tanpa database."""

    def __init__(
        self,
        *,
        status: SessionStatus | None = SessionStatus.PARTIALLY_FILLED,
        slot: ManualAttemptRow | None = None,
        manual: bool = True,
        case_count: int = 1,
        history: list[ManualAnswerHistoryRow] | None = None,
    ) -> None:
        self._status = status
        self._slot = slot
        self._manual = manual
        self._case_count = case_count
        self._history = history or []
        self.calls: list[str] = []

    def lock_manual(self, session_id: uuid.UUID) -> SessionStatus | None:
        self.calls.append("lock_manual")
        return self._status

    def is_manual_session(self, session_id: uuid.UUID) -> bool:
        return self._manual

    def answer_slot(self, session_id, snapshot_item_id, attempt_no, *, lock=False):
        self.calls.append(f"answer_slot(lock={lock})")
        return self._slot

    def case_attempt_count(self, session_id, snapshot_item_id) -> int:
        return self._case_count

    def archive_answer(self, attempt_id, *, overwritten_by, overwritten_at) -> None:
        self.calls.append("archive_answer")

    def write_answer(self, attempt_id, **kwargs: Any) -> ManualAttemptRow:
        self.calls.append("write_answer")
        return ManualAttemptRow(
            id=attempt_id,
            snapshot_item_id=KASUS,
            repetition_no=1,
            filled=True,
            answered_at_platform=kwargs["obtained_at"],
            source=AttemptSource.MANUAL,
            answer_md=kwargs["answer_md"],
            platform_refused=kwargs["platform_refused"],
            note=kwargs["note"],
            filled_by=kwargs["filled_by"],
            filled_at=kwargs["filled_at"],
        )

    def answer_history(self, attempt_id) -> list[ManualAnswerHistoryRow]:
        return self._history

    def manual_attempts(self, session_id) -> list[ManualAttemptRow]:
        return [_slot(filled=True)]

    def set_answer_dates(self, session_id, start, end) -> None:
        self.calls.append("set_answer_dates")

    def change_status(self, session_id, target, *, at, finalized_by=None, commit) -> None:
        self.calls.append(f"change_status({target.value})")

    def read_manual(self, session_id):  # pragma: no cover - tidak dipakai layanan ini
        return None

    def snapshot_cases(self, session_id):  # pragma: no cover - tidak dipakai layanan ini
        return []

    def commit(self) -> None:
        self.calls.append("commit")

    def rollback(self) -> None:
        self.calls.append("rollback")


def _payload(**ubah: Any) -> ManualAnswerSaveRequest:
    data: dict[str, Any] = {
        "snapshot_item_id": KASUS,
        "attempt_no": 1,
        "answer_md": "**Jawaban baru**",
        "platform_refused": False,
        "confirm": False,
    }
    data.update(ubah)
    return ManualAnswerSaveRequest(**data)


# Menyimpan jawaban pertama


def test_jawaban_pertama_ditulis_tanpa_mengarsipkan_apa_pun():
    """Positif: slot yang masih kosong tidak punya versi lama untuk disimpan (AC4)."""
    store = FakeStore(slot=_slot(filled=False))

    hasil = ManualAnswerService(store).save(SESI, _payload(), actor=AKTOR, now=SAAT)

    assert "archive_answer" not in store.calls
    assert hasil.answer_md == "**Jawaban baru**"
    assert hasil.filled_by == AKTOR
    assert hasil.filled_at == SAAT


def test_sesi_dikunci_sebelum_percobaannya_dibaca():
    """Edge: urutan kuncinya sesi dulu lalu percobaan, supaya tidak saling mendahului."""
    store = FakeStore(slot=_slot(filled=False))

    ManualAnswerService(store).save(SESI, _payload(), actor=AKTOR, now=SAAT)

    assert store.calls[0] == "lock_manual"
    assert store.calls[1] == "answer_slot(lock=True)"
    assert store.calls[-1] == "commit"


def test_waktu_diperoleh_kosong_memakai_waktu_sekarang():
    """Positif: tanggal dan jam jawaban diperoleh terisi otomatis (AC7)."""
    store = FakeStore(slot=_slot(filled=False))

    hasil = ManualAnswerService(store).save(SESI, _payload(), actor=AKTOR, now=SAAT)

    assert hasil.obtained_at == SAAT


# Menimpa jawaban


def test_menimpa_tanpa_konfirmasi_ditolak_dan_tidak_menulis_apa_pun():
    """Negatif: jawaban yang sudah ada hanya boleh ditimpa dengan confirm=true (AC5)."""
    store = FakeStore(slot=_slot(filled=True))

    with pytest.raises(ConfirmationRequiredError):
        ManualAnswerService(store).save(SESI, _payload(), actor=AKTOR, now=SAAT)

    assert "write_answer" not in store.calls
    assert "archive_answer" not in store.calls
    assert store.calls[-1] == "rollback"


def test_menimpa_dengan_konfirmasi_mengarsipkan_dulu_lalu_menulis():
    """Positif: versi lama masuk riwayat sebelum yang baru ditulis (AC5)."""
    store = FakeStore(slot=_slot(filled=True))

    ManualAnswerService(store).save(SESI, _payload(confirm=True), actor=AKTOR, now=SAAT)

    urut = [
        panggilan for panggilan in store.calls if panggilan in ("archive_answer", "write_answer")
    ]
    assert urut == ["archive_answer", "write_answer"]


# Sesi yang tidak bisa diisi


def test_sesi_tidak_ada_menghasilkan_404():
    """Negatif: sesi yang tidak ada bukan galat isian (AC5)."""
    store = FakeStore(status=None)

    with pytest.raises(NotFoundError) as galat:
        ManualAnswerService(store).save(SESI, _payload(), actor=AKTOR, now=SAAT)

    assert galat.value.code == "MANUAL_SESSION_NOT_FOUND"
    assert store.calls[-1] == "rollback"


def test_sesi_berstatus_sesi_otomatis_menghasilkan_409():
    """Negatif: baris manual lama berstatus D4 ditolak 409, bukan 500 (review #116)."""
    store = FakeStore(status=SessionStatus.RUNNING, slot=_slot(filled=False))

    with pytest.raises(ConflictError) as galat:
        ManualAnswerService(store).save(SESI, _payload(), actor=AKTOR, now=SAAT)

    assert galat.value.code == "MANUAL_SESSION_STATUS_INVALID"


# Kasus dan nomor percobaan yang tidak ada


def test_kasus_di_luar_snapshot_memakai_kodenya_sendiri():
    """Negatif: dua syarat terpisah di tiket tidak boleh satu kode (AC4)."""
    store = FakeStore(slot=None, case_count=0)

    with pytest.raises(ValidationError) as galat:
        ManualAnswerService(store).save(SESI, _payload(), actor=AKTOR, now=SAAT)

    assert galat.value.code == "MANUAL_CASE_NOT_IN_SNAPSHOT"


def test_nomor_percobaan_di_luar_pengulangan_memakai_kodenya_sendiri():
    """Negatif: kasusnya ada, nomornya yang tidak (AC4)."""
    store = FakeStore(slot=None, case_count=3)

    with pytest.raises(ValidationError) as galat:
        ManualAnswerService(store).save(SESI, _payload(), actor=AKTOR, now=SAAT)

    assert galat.value.code == "MANUAL_ATTEMPT_NO_OUT_OF_RANGE"


# Riwayat


def test_riwayat_sesi_bukan_manual_menghasilkan_404():
    """Negatif: sesi otomatis tidak punya riwayat jawaban manual (AC5)."""
    store = FakeStore(manual=False)

    with pytest.raises(NotFoundError) as galat:
        ManualAnswerService(store).history(SESI, KASUS, 1)

    assert galat.value.code == "MANUAL_SESSION_NOT_FOUND"


def test_riwayat_jawaban_yang_tidak_ada_menghasilkan_404():
    """Negatif: pada GET, kasus atau nomor yang tidak ada berarti sumbernya tidak ada."""
    store = FakeStore(slot=None)

    with pytest.raises(NotFoundError) as galat:
        ManualAnswerService(store).history(SESI, KASUS, 1)

    assert galat.value.code == "MANUAL_ANSWER_NOT_FOUND"


def test_riwayat_kosong_bukan_galat():
    """Edge: jawaban yang belum pernah ditimpa punya riwayat kosong, bukan 404."""
    store = FakeStore(slot=_slot(filled=True), history=[])

    assert ManualAnswerService(store).history(SESI, KASUS, 1) == []
