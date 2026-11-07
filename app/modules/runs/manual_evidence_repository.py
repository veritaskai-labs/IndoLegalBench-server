"""Query bukti jawaban manual: port EvidenceStore dan EvidenceStorage. PBI-22 (SCRUM-176); SCRUM-233.

Terpisah dari repository.py (sudah lebih dari 1000 baris) dengan peran yang sama: semua
SQL bukti ada di sini, service ManualEvidenceService tidak menulis query sendiri.

Kunci baris sesi FOR SHARE: beberapa pengunggah boleh jalan bersamaan, tetapi Tandai selesai
(FOR UPDATE, lock_manual) menunggu sampai bukti yang sedang diunggah ter-commit. Batas 3 per
percobaan tetap dijaga trigger attempt_evidence_guard yang mengunci baris attempt.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.modules.runs.models import (
    Attempt,
    AttemptEvidence,
    AttemptSource,
    EvidenceFile,
    EvidenceKind,
    MeasurementSession,
    SessionStatus,
    SessionType,
)
from app.modules.runs.session_ports import EvidenceRow, EvidenceTarget, NewEvidence


def _row(evidence: AttemptEvidence) -> EvidenceRow:
    return EvidenceRow(
        id=evidence.id,
        attempt_id=evidence.attempt_id,
        kind=EvidenceKind(evidence.kind),
        file_key=evidence.file_key,
        file_name=evidence.file_name,
        mime_type=evidence.mime_type,
        size_bytes=evidence.size_bytes,
        url=evidence.url,
        uploaded_by=evidence.uploaded_by,
        uploaded_at=evidence.uploaded_at,
    )


def _target(attempt: Attempt) -> EvidenceTarget:
    return EvidenceTarget(
        attempt_id=attempt.id,
        snapshot_item_id=attempt.snapshot_item_id,
        repetition_no=attempt.repetition_no,
    )


class EvidenceRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def session_status(self, session_id: uuid.UUID, *, lock: bool) -> SessionStatus | None:
        query = select(MeasurementSession.session_type, MeasurementSession.status).where(
            MeasurementSession.id == session_id
        )
        if lock:
            query = query.with_for_update(read=True)
        row = self._db.execute(query).first()
        if row is None or row.session_type != SessionType.MANUAL:
            return None
        return SessionStatus(row.status)

    def find_attempt(
        self, session_id: uuid.UUID, snapshot_item_id: uuid.UUID, attempt_no: int
    ) -> EvidenceTarget | None:
        attempt = self._db.scalars(
            select(Attempt).where(
                Attempt.session_id == session_id,
                Attempt.snapshot_item_id == snapshot_item_id,
                Attempt.repetition_no == attempt_no,
                Attempt.source == AttemptSource.MANUAL,
            )
        ).first()
        return None if attempt is None else _target(attempt)

    def evidence_for(self, attempt_id: uuid.UUID) -> list[EvidenceRow]:
        rows = self._db.scalars(
            select(AttemptEvidence)
            .where(AttemptEvidence.attempt_id == attempt_id)
            .order_by(AttemptEvidence.uploaded_at, AttemptEvidence.id)
        )
        return [_row(evidence) for evidence in rows]

    def add(self, new: NewEvidence) -> EvidenceRow:
        evidence = AttemptEvidence(
            attempt_id=new.attempt_id,
            kind=new.kind,
            file_key=new.file_key,
            file_name=new.file_name,
            mime_type=new.mime_type,
            size_bytes=new.size_bytes,
            url=new.url,
            uploaded_by=new.uploaded_by,
            # Diisi di sini, bukan now() database: urutan daftar tetap benar untuk dua
            # unggahan dalam satu detik (SQLite) atau satu transaksi (PostgreSQL).
            uploaded_at=datetime.now(UTC),
        )
        self._db.add(evidence)
        self._db.flush()
        return _row(evidence)

    def find_evidence(
        self, session_id: uuid.UUID, evidence_id: uuid.UUID
    ) -> tuple[EvidenceRow, EvidenceTarget] | None:
        found = self._db.execute(
            select(AttemptEvidence, Attempt)
            .join(Attempt, Attempt.id == AttemptEvidence.attempt_id)
            .where(AttemptEvidence.id == evidence_id, Attempt.session_id == session_id)
        ).first()
        if found is None:
            return None
        evidence, attempt = found
        return _row(evidence), _target(attempt)

    def remove(self, evidence_id: uuid.UUID) -> None:
        self._db.execute(delete(AttemptEvidence).where(AttemptEvidence.id == evidence_id))

    def record(
        self,
        session_id: uuid.UUID,
        action: str,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        # Diimpor di sini: audit.service mengimpor runs.service, yang mengimpor repository runs.
        from app.modules.audit import service as audit_service

        audit_service.record(
            self._db,
            action=action,
            entity_type="measurement_session",
            entity_id=session_id,
            before=before,
            after=after,
        )

    def commit(self) -> None:
        self._db.commit()


class DbEvidenceStorage:
    """Isi berkas di tabel evidence_files, transaksi yang sama dengan baris buktinya."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def put(self, content: bytes) -> str:
        key = uuid.uuid4().hex
        self._db.add(EvidenceFile(key=key, content=content))
        self._db.flush()
        return key

    def read(self, key: str) -> bytes | None:
        stored = self._db.get(EvidenceFile, key)
        return None if stored is None else stored.content

    def delete(self, key: str) -> None:
        self._db.execute(delete(EvidenceFile).where(EvidenceFile.key == key))
