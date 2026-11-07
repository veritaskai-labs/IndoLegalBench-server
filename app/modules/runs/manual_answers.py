"""Menyimpan jawaban manual, menimpanya, dan membaca jejak versi lama. SCRUM-231.

Jawaban disimpan apa adanya di attempts dengan sumber manual: markdown utuh, tanpa
batas panjang, tabel dan tautan tidak dibuang (AC4). Menimpa jawaban yang sudah ada
butuh konfirmasi, dan versi lamanya pindah ke answer_history lengkap dengan pengisi,
waktu isi, penimpa, dan waktu timpa (AC5).

Layanan ini hanya berbicara lewat ManualAnswerStore, jadi ia tidak mengenal tabel
maupun SQLAlchemy. Query dan transaksinya ada di ManualSessionRepository, sama seperti
alur sesi manual yang lain (review #119).
"""

import uuid
from datetime import UTC, datetime

from app.modules.runs.manual_session_rules import (
    MANUAL_STATUSES,
    ensure_answers_open,
)
from app.modules.runs.manual_session_status import ManualSessionStatusService
from app.modules.runs.schemas import (
    ManualAnswerHistoryEntry,
    ManualAnswerResponse,
    ManualAnswerSaveRequest,
)
from app.modules.runs.session_ports import ManualAnswerStore, ManualAttemptRow
from app.shared.exceptions import (
    ConfirmationRequiredError,
    ConflictError,
    NotFoundError,
    ValidationError,
)


def _session_not_found() -> NotFoundError:
    return NotFoundError("Sesi manual tidak ditemukan.", code="MANUAL_SESSION_NOT_FOUND")


class ManualAnswerService:
    """Simpan, timpa, dan baca riwayat jawaban manual satu sesi."""

    def __init__(self, store: ManualAnswerStore) -> None:
        self._store = store

    def save(
        self,
        session_id: uuid.UUID,
        payload: ManualAnswerSaveRequest,
        *,
        actor: uuid.UUID,
        now: datetime | None = None,
    ) -> ManualAnswerResponse:
        """Simpan satu jawaban; menimpa yang sudah ada butuh confirm=true (AC5)."""
        saat = now or datetime.now(UTC)
        try:
            # Kunci sesi dulu, lalu percobaannya, supaya simpan, timpa, dan Selesai
            # tidak saling mendahului.
            self._open_session(session_id)
            slot = self._slot(session_id, payload.snapshot_item_id, payload.attempt_no, lock=True)

            if slot.filled_at is not None:
                if not payload.confirm:
                    raise ConfirmationRequiredError(
                        "Jawaban sudah ada. Konfirmasi sebelum menimpa.",
                        affected_count=1,
                    )
                self._store.archive_answer(slot.id, overwritten_by=actor, overwritten_at=saat)

            tersimpan = self._store.write_answer(
                slot.id,
                answer_md=payload.answer_md,
                platform_refused=payload.platform_refused,
                note=payload.note,
                obtained_at=payload.obtained_at or saat,
                filled_by=actor,
                filled_at=saat,
            )
            status = ManualSessionStatusService(self._store).recompute(session_id, now=saat)

            jawaban = _response(tersimpan, status)
            self._store.commit()
            return jawaban
        except Exception:
            self._store.rollback()
            raise

    def history(
        self,
        session_id: uuid.UUID,
        snapshot_item_id: uuid.UUID,
        attempt_no: int,
    ) -> list[ManualAnswerHistoryEntry]:
        """Versi lama satu jawaban, terlama dulu. Semua yang bisa membuka sesi (AC5)."""
        if not self._store.is_manual_session(session_id):
            raise _session_not_found()

        # GET: kasus atau nomor yang tidak ada berarti sumbernya tidak ada, jadi 404.
        # Pada PUT hal yang sama tetap 422, karena di sana itu isian yang salah.
        slot = self._store.answer_slot(session_id, snapshot_item_id, attempt_no)
        if slot is None:
            raise NotFoundError(
                "Jawaban manual tidak ditemukan.",
                code="MANUAL_ANSWER_NOT_FOUND",
            )
        return [
            ManualAnswerHistoryEntry.model_validate(baris, from_attributes=True)
            for baris in self._store.answer_history(slot.id)
        ]

    def _open_session(self, session_id: uuid.UUID) -> None:
        status = self._store.lock_manual(session_id)
        if status is None:
            raise _session_not_found()
        if status not in MANUAL_STATUSES:
            raise ConflictError(
                "Status sesi manual tidak valid.",
                code="MANUAL_SESSION_STATUS_INVALID",
            )
        ensure_answers_open(status)

    def _slot(
        self,
        session_id: uuid.UUID,
        snapshot_item_id: uuid.UUID,
        attempt_no: int,
        *,
        lock: bool = False,
    ) -> ManualAttemptRow:
        slot = self._store.answer_slot(session_id, snapshot_item_id, attempt_no, lock=lock)
        if slot is not None:
            return slot
        raise self._missing_slot_error(session_id, snapshot_item_id)

    def _missing_slot_error(
        self, session_id: uuid.UUID, snapshot_item_id: uuid.UUID
    ) -> ValidationError:
        """Pisahkan dua validasi AC yang berbeda, supaya pengisi tahu mana yang salah.

        "Kasus ada di snapshot sesi" dan "nomor percobaan dalam jumlah percobaan sesi"
        adalah dua syarat terpisah di tiket, jadi keduanya tidak boleh keluar dengan
        satu kode yang sama. Hitungan ini hanya jalan di jalur gagal.
        """
        if not self._store.case_attempt_count(session_id, snapshot_item_id):
            return ValidationError(
                "Kasus tidak ada di snapshot sesi ini.",
                code="MANUAL_CASE_NOT_IN_SNAPSHOT",
            )
        return ValidationError(
            "Nomor percobaan di luar jumlah pengulangan sesi.",
            code="MANUAL_ATTEMPT_NO_OUT_OF_RANGE",
        )


def _response(slot: ManualAttemptRow, status) -> ManualAnswerResponse:
    return ManualAnswerResponse(
        attempt_id=slot.id,
        snapshot_item_id=slot.snapshot_item_id,
        attempt_no=slot.repetition_no,
        source=slot.source,
        answer_md=slot.answer_md,
        obtained_at=slot.answered_at_platform,
        platform_refused=slot.platform_refused,
        note=slot.note,
        filled_by=slot.filled_by,
        filled_at=slot.filled_at,
        session_status=status,
    )
