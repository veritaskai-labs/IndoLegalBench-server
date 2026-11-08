"""Bukti jawaban manual: unggah, tautan, daftar, unduh, hapus. PBI-22 (SCRUM-176) AC7, AC9; SCRUM-233.

D9 attempt_evidence: bukti opsional berupa berkas PNG/JPG/PDF atau tautan, maksimal 3 per
jawaban. Aturannya ada di manual_evidence_rules (murni); baris bukti lewat port
EvidenceStore dan isi berkas lewat port EvidenceStorage, jadi service ini diuji dengan
penyimpanan palsu (pola review #116).

Berkas divalidasi sebelum sesi dikunci: berkas yang ditolak tidak pernah menahan Tandai
selesai. Bukti bisa ditambah dan dihapus sampai sesi Selesai, sama seperti jawabannya (AC8).
Setiap tambah dan hapus tercatat di audit_logs dengan label manual (AC9, PBI-18); isi
berkas dan tautan tidak disalin ke audit.

Yang tidak boleh melihat bukti (flag MANUAL_EVIDENCE_VISIBILITY, AC7 TBA) mendapat 404,
bukan 403, supaya keberadaan bukti tidak terbaca. Pengunggah tetap boleh menghapus buktinya
sendiri, supaya salah unggah tidak terjebak (review #121).
"""

import uuid
from dataclasses import dataclass
from typing import Any

from app.modules.runs import manual_evidence_rules as rules
from app.modules.runs.manual_session_rules import MANUAL_STATUSES, ensure_answers_open
from app.modules.runs.models import AttemptSource, EvidenceKind, SessionStatus
from app.modules.runs.schemas import EvidenceRead
from app.modules.runs.session_ports import (
    EvidenceRow,
    EvidenceStorage,
    EvidenceStore,
    EvidenceTarget,
    NewEvidence,
)
from app.shared.exceptions import ConflictError, NotFoundError
from app.shared.security import Role

ACTION_EVIDENCE_ADDED = "measurement_session.evidence_added"
ACTION_EVIDENCE_REMOVED = "measurement_session.evidence_removed"


@dataclass(frozen=True)
class EvidenceDownload:
    content: bytes
    mime_type: str
    file_name: str


def _session_not_found() -> NotFoundError:
    return NotFoundError("Sesi pengukuran manual tidak ditemukan.", code="MANUAL_SESSION_NOT_FOUND")


def _evidence_not_found() -> NotFoundError:
    return NotFoundError("Bukti tidak ditemukan.", code="EVIDENCE_NOT_FOUND")


def _read(row: EvidenceRow) -> EvidenceRead:
    return EvidenceRead(
        id=row.id,
        kind=row.kind,
        file_name=row.file_name,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        url=row.url,
        uploaded_by=row.uploaded_by,
        uploaded_at=row.uploaded_at,
    )


def _audit_payload(row: EvidenceRow, target: EvidenceTarget) -> dict[str, Any]:
    # Tanpa isi berkas dan url: audit menjawab siapa, kapan, bukti mana (PBI-18).
    return {
        "evidence_id": str(row.id),
        "attempt_id": str(target.attempt_id),
        "snapshot_item_id": str(target.snapshot_item_id),
        "repetition_no": target.repetition_no,
        "kind": EvidenceKind(row.kind).value,
        "mime_type": row.mime_type,
        "size_bytes": row.size_bytes,
        "source": AttemptSource.MANUAL.value,
    }


class ManualEvidenceService:
    def __init__(
        self,
        store: EvidenceStore,
        files: EvidenceStorage,
        *,
        max_bytes: int,
        visibility: rules.EvidenceVisibility,
    ) -> None:
        self._store = store
        self._files = files
        self._max_bytes = max_bytes
        self._visibility = visibility

    def add_file(
        self,
        session_id: uuid.UUID,
        snapshot_item_id: uuid.UUID,
        attempt_no: int,
        *,
        content: bytes,
        declared_type: str | None,
        file_name: str | None,
        actor: uuid.UUID,
    ) -> EvidenceRead:
        rules.ensure_size(len(content), limit_bytes=self._max_bytes)
        mime_type = rules.detect_file_type(content[:16], declared=declared_type)
        target = self._open_target(session_id, snapshot_item_id, attempt_no)
        key = self._files.put(content)
        return self._add(
            session_id,
            target,
            NewEvidence(
                attempt_id=target.attempt_id,
                kind=EvidenceKind.FILE,
                uploaded_by=actor,
                file_key=key,
                file_name=rules.safe_file_name(file_name),
                mime_type=mime_type,
                size_bytes=len(content),
            ),
        )

    def add_link(
        self,
        session_id: uuid.UUID,
        snapshot_item_id: uuid.UUID,
        attempt_no: int,
        *,
        url: str,
        actor: uuid.UUID,
    ) -> EvidenceRead:
        link = rules.normalize_link(url)
        target = self._open_target(session_id, snapshot_item_id, attempt_no)
        return self._add(
            session_id,
            target,
            NewEvidence(
                attempt_id=target.attempt_id, kind=EvidenceKind.LINK, uploaded_by=actor, url=link
            ),
        )

    def list(
        self, session_id: uuid.UUID, snapshot_item_id: uuid.UUID, attempt_no: int, *, role: Role
    ) -> list[EvidenceRead]:
        self._ensure_visible(role)
        if self._store.session_status(session_id, lock=False) is None:
            raise _session_not_found()
        target = self._attempt(session_id, snapshot_item_id, attempt_no)
        return [_read(row) for row in self._store.evidence_for(target.attempt_id)]

    def download(
        self, session_id: uuid.UUID, evidence_id: uuid.UUID, *, role: Role
    ) -> EvidenceDownload:
        self._ensure_visible(role)
        found = self._store.find_evidence(session_id, evidence_id)
        content = None
        if found is not None and found[0].file_key is not None:
            content = self._files.read(found[0].file_key)
        if content is None:
            raise _evidence_not_found()
        row = found[0]
        return EvidenceDownload(
            content=content, mime_type=row.mime_type, file_name=row.file_name or "bukti"
        )

    def delete(
        self, session_id: uuid.UUID, evidence_id: uuid.UUID, *, role: Role, actor: uuid.UUID
    ) -> None:
        """Yang boleh melihat bukti, atau pengunggahnya sendiri (review #121), boleh menghapus."""
        self._ensure_open(self._store.session_status(session_id, lock=True))
        found = self._store.find_evidence(session_id, evidence_id)
        if found is None or not (self._visible(role) or found[0].uploaded_by == actor):
            raise _evidence_not_found()
        row, target = found
        self._store.remove(row.id)
        if row.file_key is not None:
            self._files.delete(row.file_key)
        self._store.record(session_id, ACTION_EVIDENCE_REMOVED, before=_audit_payload(row, target))
        self._store.commit()

    def _add(self, session_id: uuid.UUID, target: EvidenceTarget, new: NewEvidence) -> EvidenceRead:
        row = self._store.add(new)
        self._store.record(session_id, ACTION_EVIDENCE_ADDED, after=_audit_payload(row, target))
        self._store.commit()
        return _read(row)

    def _open_target(
        self, session_id: uuid.UUID, snapshot_item_id: uuid.UUID, attempt_no: int
    ) -> EvidenceTarget:
        """Sesi dikunci dan masih terbuka, percobaannya ada, dan masih ada tempat."""
        self._ensure_open(self._store.session_status(session_id, lock=True))
        # Kunci attempt: kunci sesi FOR SHARE tidak saling menahan, jadi tanpa ini dua
        # unggahan bersamaan sama-sama melihat 2 bukti dan yang kalah ditolak trigger (500).
        target = self._attempt(session_id, snapshot_item_id, attempt_no, lock=True)
        rules.ensure_room(len(self._store.evidence_for(target.attempt_id)))
        return target

    def _attempt(
        self,
        session_id: uuid.UUID,
        snapshot_item_id: uuid.UUID,
        attempt_no: int,
        *,
        lock: bool = False,
    ) -> EvidenceTarget:
        target = self._store.find_attempt(session_id, snapshot_item_id, attempt_no, lock=lock)
        if target is None:
            raise NotFoundError(
                "Kasus atau percobaan tidak ditemukan pada sesi.", code="MANUAL_ATTEMPT_NOT_FOUND"
            )
        return target

    @staticmethod
    def _ensure_open(status: SessionStatus | None) -> None:
        if status is None:
            raise _session_not_found()
        if status not in MANUAL_STATUSES:
            # Baris manual lama berstatus D4 (sebelum SCRUM-230): 409, bukan 500 (review #116).
            raise ConflictError(
                "Sesi manual ini masih berstatus sesi otomatis dan tidak bisa diisi.",
                code="MANUAL_SESSION_STATUS_INVALID",
            )
        ensure_answers_open(status)

    def _visible(self, role: Role) -> bool:
        return rules.can_view_evidence(role, policy=self._visibility)

    def _ensure_visible(self, role: Role) -> None:
        if not self._visible(role):
            raise _evidence_not_found()
