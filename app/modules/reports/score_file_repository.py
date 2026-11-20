"""Adapter SQL untuk unduhan skor per sesi. PBI-16 SCRUM-83 SCRUM-327.

SqlScoreSource memenuhi port ScoreSource: fakta sesi dan kasus yang dinilai dibaca lewat
SqlSessionRecordReader, pembaca yang sama dengan pembuat laporan, jadi skor di berkas sama
dengan skor di laporan. Di atasnya ditambah tanda arsip, grading run terakhir yang selesai,
dan nomor versi kasus serta rubrik yang terbaca manusia.

SqlScoreFileStore memenuhi port ScoreFileStore: berkas hanya ditambah, tidak pernah diubah
atau dihapus (trigger PostgreSQL menjaganya untuk semua peran).
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.cases.models import CaseVersion
from app.modules.grading.models import GradingRun, GradingRunStatus, RubricVersion
from app.modules.reports.models import SessionScoreFile
from app.modules.reports.score_files import ScoreFile, SessionScoreData
from app.modules.reports.session_reader import SqlSessionRecordReader
from app.modules.runs.models import MeasurementSession


class SqlScoreSource:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._records = SqlSessionRecordReader(db)

    def read(self, session_id: uuid.UUID) -> SessionScoreData | None:
        records = self._records.read([session_id])
        if not records:
            return None
        record = records[0]
        archived_at = self._db.scalar(
            sa.select(MeasurementSession.archived_at).where(MeasurementSession.id == session_id)
        )
        return SessionScoreData(
            record=record,
            archived=archived_at is not None,
            grading_run_id=self._latest_finished_run(session_id),
            rubric_version_no=self._rubric_version_no(record.facts.rubric_version_id),
            case_version_nos=self._case_version_nos(
                {ref.case_version_id for ref in record.facts.snapshot_cases}
            ),
        )

    def _latest_finished_run(self, session_id: uuid.UUID) -> uuid.UUID | None:
        """Run yang sama dengan sumber skor: terakhir dan sudah selesai (D2)."""
        return self._db.scalar(
            sa.select(GradingRun.id).where(
                GradingRun.session_id == session_id,
                GradingRun.is_latest.is_(True),
                GradingRun.status == GradingRunStatus.FINISHED,
            )
        )

    def _rubric_version_no(self, rubric_version_id: uuid.UUID | None) -> int | None:
        if rubric_version_id is None:
            return None
        return self._db.scalar(
            sa.select(RubricVersion.version_no).where(RubricVersion.id == rubric_version_id)
        )

    def _case_version_nos(self, version_ids: set[uuid.UUID]) -> dict[uuid.UUID, int]:
        if not version_ids:
            return {}
        rows = self._db.execute(
            sa.select(CaseVersion.id, CaseVersion.version_no).where(CaseVersion.id.in_(version_ids))
        )
        return dict(rows.tuples().all())


class SqlScoreFileStore:
    def __init__(self, db: Session) -> None:
        self._db = db

    def keep(
        self, file: ScoreFile, *, grading_run_id: uuid.UUID | None, created_by: uuid.UUID
    ) -> ScoreFile:
        """Simpan berkas lalu commit. Isi yang sama untuk sesi yang sama dipakai ulang.

        Dua unduhan bersamaan dengan isi sama bisa sama-sama lolos cek pertama; yang kalah
        mendapat IntegrityError dari unik session_id + sha256, lalu membaca baris pemenang.
        Unduhan adalah satu-satunya tulisan di permintaan ini, jadi rollback aman.
        """
        tersimpan = self._find(file.session_id, file.sha256)
        if tersimpan is not None:
            return tersimpan
        self._db.add(
            SessionScoreFile(
                session_id=file.session_id,
                grading_run_id=grading_run_id,
                file_name=file.file_name,
                content=file.content,
                sha256=file.sha256,
                size_bytes=len(file.content),
                created_by=created_by,
            )
        )
        try:
            self._db.commit()
        except IntegrityError:
            self._db.rollback()
            tersimpan = self._find(file.session_id, file.sha256)
            if tersimpan is None:
                raise
            return tersimpan
        return file

    def _find(self, session_id: uuid.UUID, sha256: str) -> ScoreFile | None:
        baris = self._db.scalar(
            sa.select(SessionScoreFile).where(
                SessionScoreFile.session_id == session_id, SessionScoreFile.sha256 == sha256
            )
        )
        if baris is None:
            return None
        return ScoreFile(
            session_id=baris.session_id,
            file_name=baris.file_name,
            content=bytes(baris.content),
            sha256=baris.sha256,
        )
