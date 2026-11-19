"""Pembaca data sesi dari database untuk pembuat laporan. PBI-15 SCRUM-79 SCRUM-313.

Adapter D2 yang dijanjikan di docstring comparison_rules: mengisi SessionFacts menurut
kontraknya, ditambah kasus yang sudah dinilai dan jumlah upaya pemanggilan.
Memenuhi port SessionRecordReader.

Skor per kasus adalah rata-rata semua percobaan pada presisi penuh (percobaan tanpa skor
dilewati, bukan dianggap nol). Skor LLM adalah jalur kualitas dari penilaian terakhir yang
selesai; jalur format tidak ikut skor laporan. Impor model runs dan grading di sini mengikuti
pola yang sudah dipakai comparison_rules: modul ini hanya membaca.
"""

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.cases.models import SplitTag
from app.modules.grading.models import (
    GradingResult,
    GradingRun,
    JudgeStatus,
    LlmJudgeResult,
)
from app.modules.providers.models import AiProduct
from app.modules.reports.comparison_rules import CaseRef, SessionFacts
from app.modules.reports.report_scoring import CallCounts, GradedCase
from app.modules.reports.session_record import SessionRecord
from app.modules.runs.adapters import SqlGradingReader
from app.modules.runs.models import (
    Attempt,
    CallLog,
    CaseSnapshot,
    CaseSnapshotItem,
    MeasurementSession,
    MeasurementSessionSuite,
    SessionProductCopy,
)
from app.modules.runs.score_aggregation import raw_mean_score
from app.modules.runs.snapshot_cases import CaseFacts, case_facts_from_body


class SqlSessionRecordReader:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._grades = SqlGradingReader(db)

    def list_ids(self) -> list[uuid.UUID]:
        """Id semua sesi aktif (tidak diarsipkan), yang terbaru lebih dulu."""
        return list(
            self._db.scalars(
                sa.select(MeasurementSession.id)
                .where(MeasurementSession.archived_at.is_(None))
                .order_by(MeasurementSession.created_at.desc(), MeasurementSession.id)
            )
        )

    def read(self, session_ids: Sequence[uuid.UUID]) -> list[SessionRecord]:
        """Satu rekaman per id yang diminta, urut sama dan id berulang dipertahankan.

        Id yang tidak ada dilewati; pemanggil yang memutuskan itu 404. Id berulang tidak
        dibuang supaya aturan seleksi (produk atau sesi ganda) masih bisa melihatnya.
        """
        unique = list(dict.fromkeys(session_ids))
        if not unique:
            return []
        sessions = {
            row.id: row
            for row in self._db.scalars(
                sa.select(MeasurementSession).where(MeasurementSession.id.in_(unique))
            )
        }
        copies = self._product_copies(unique)
        names = self._master_names({row.product_id for row in sessions.values()})
        suites = self._suites(unique)
        snapshot = self._snapshot_items(unique)
        attempts = self._attempts(unique)
        calls = self._call_counts(unique)

        records = {
            session_id: self._record(
                sessions[session_id],
                copies.get(session_id),
                names,
                suites.get(session_id, frozenset()),
                snapshot.get(session_id, []),
                attempts.get(session_id, []),
                calls.get(session_id, {}),
            )
            for session_id in unique
            if session_id in sessions
        }
        return [records[session_id] for session_id in session_ids if session_id in records]

    def _record(
        self,
        session: MeasurementSession,
        copy: SessionProductCopy | None,
        master_names: Mapping[uuid.UUID, str],
        suite_ids: frozenset[uuid.UUID],
        items: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID, dict]],
        attempts: list[tuple[uuid.UUID, uuid.UUID]],
        calls: Mapping[uuid.UUID, CallCounts],
    ) -> SessionRecord:
        grades = self._grades.latest(session.id, round_judge_scores=False)
        refs = {}
        for item_id, case_id, version_id, body in items:
            facts = case_facts_from_body(item_id, body)
            refs[item_id] = (
                CaseRef(case_id, version_id, facts.case_code, facts.split_tag.value),
                facts,
            )
        graded = _graded_cases(refs, attempts, grades.deterministic, grades.quality)
        judge_provider_id, judge_model_version = self._judge_identity(grades.run_id)

        session_facts = SessionFacts(
            session_id=session.id,
            product_id=session.product_id,
            product_name=copy.name if copy else master_names.get(session.product_id, ""),
            session_type=session.session_type,
            purpose=session.purpose,
            status=session.status,
            accepted_as_is_at=session.accepted_as_is_at,
            rubric_version_id=grades.rubric_version_id,
            judge_provider_id=judge_provider_id,
            judge_model_version=judge_model_version,
            suite_ids=suite_ids,
            model_version=copy.model_version_at_start if copy else None,
            repetitions=session.repetitions,
            answer_date_from=session.answer_date_from,
            answer_date_to=session.answer_date_to,
            scored_cases=tuple(kasus.ref for kasus in graded),
            snapshot_cases=tuple(ref for ref, _ in refs.values()),
            split_filter=session.split_filter,
        )
        return SessionRecord(
            facts=session_facts,
            graded_cases=graded,
            calls_by_split=_calls_by_split(refs, calls),
            label=session.label,
            created_at=session.created_at,
        )

    def _judge_identity(self, run_id: uuid.UUID | None) -> tuple[uuid.UUID | None, str | None]:
        """Penyedia penilai dan versi modelnya; versi hanya terisi bila satu dan konsisten."""
        if run_id is None:
            return None, None
        provider_id = self._db.scalar(
            sa.select(GradingRun.judge_provider_id).where(GradingRun.id == run_id)
        )
        versions = set(
            self._db.scalars(
                sa.select(LlmJudgeResult.model_version)
                .join(GradingResult, GradingResult.id == LlmJudgeResult.grading_result_id)
                .where(
                    GradingResult.grading_run_id == run_id,
                    LlmJudgeResult.status == JudgeStatus.SUCCEEDED,
                )
                .distinct()
            )
        )
        only = next(iter(versions)) if len(versions) == 1 else None
        return provider_id, only

    def _product_copies(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, SessionProductCopy]:
        rows = self._db.scalars(
            sa.select(SessionProductCopy).where(SessionProductCopy.session_id.in_(ids))
        )
        return {row.session_id: row for row in rows}

    def _master_names(self, product_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        rows = self._db.execute(
            sa.select(AiProduct.id, AiProduct.name).where(AiProduct.id.in_(product_ids))
        )
        return dict(rows.tuples().all())

    def _suites(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, frozenset[uuid.UUID]]:
        grouped: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        rows = self._db.execute(
            sa.select(MeasurementSessionSuite.session_id, MeasurementSessionSuite.suite_id).where(
                MeasurementSessionSuite.session_id.in_(ids)
            )
        )
        for session_id, suite_id in rows:
            grouped[session_id].add(suite_id)
        return {session_id: frozenset(suites) for session_id, suites in grouped.items()}

    def _snapshot_items(
        self, ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[tuple[uuid.UUID, uuid.UUID, uuid.UUID, dict]]]:
        grouped: dict[uuid.UUID, list] = defaultdict(list)
        rows = self._db.execute(
            sa.select(
                CaseSnapshot.session_id,
                CaseSnapshotItem.id,
                CaseSnapshotItem.case_id,
                CaseSnapshotItem.case_version_id,
                CaseSnapshotItem.body,
            )
            .join(CaseSnapshotItem, CaseSnapshotItem.snapshot_id == CaseSnapshot.id)
            .where(CaseSnapshot.session_id.in_(ids))
            .order_by(CaseSnapshotItem.id)
        )
        for session_id, item_id, case_id, version_id, body in rows:
            grouped[session_id].append((item_id, case_id, version_id, body))
        return grouped

    def _attempts(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[tuple[uuid.UUID, uuid.UUID]]]:
        grouped: dict[uuid.UUID, list] = defaultdict(list)
        rows = self._db.execute(
            sa.select(Attempt.session_id, Attempt.id, Attempt.snapshot_item_id)
            .where(Attempt.session_id.in_(ids))
            .order_by(Attempt.snapshot_item_id, Attempt.repetition_no, Attempt.id)
        )
        for session_id, attempt_id, item_id in rows:
            grouped[session_id].append((attempt_id, item_id))
        return grouped

    def _call_counts(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[uuid.UUID, CallCounts]]:
        """Upaya per item snapshot: semua baris call_logs, gagal bila failure_kind terisi."""
        failed = sa.func.sum(sa.case((CallLog.failure_kind.is_not(None), 1), else_=0))
        rows = self._db.execute(
            sa.select(
                Attempt.session_id, Attempt.snapshot_item_id, sa.func.count(CallLog.id), failed
            )
            .join(CallLog, CallLog.attempt_id == Attempt.id)
            .where(Attempt.session_id.in_(ids))
            .group_by(Attempt.session_id, Attempt.snapshot_item_id)
        )
        grouped: dict[uuid.UUID, dict[uuid.UUID, CallCounts]] = defaultdict(dict)
        for session_id, item_id, total, gagal in rows:
            grouped[session_id][item_id] = CallCounts(total=int(total), failed=int(gagal or 0))
        return grouped


def _graded_cases(
    refs: Mapping[uuid.UUID, tuple[CaseRef, CaseFacts]],
    attempts: list[tuple[uuid.UUID, uuid.UUID]],
    deterministic: Mapping[uuid.UUID, Decimal | None],
    quality: Mapping[uuid.UUID, Decimal | None],
) -> tuple[GradedCase, ...]:
    """Kasus yang punya setidaknya satu skor; skor kasus = rata-rata percobaannya."""
    by_item: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for attempt_id, item_id in attempts:
        by_item[item_id].append(attempt_id)

    graded = []
    for item_id, (ref, facts) in refs.items():
        ids = by_item.get(item_id, [])
        det = raw_mean_score(deterministic.get(attempt_id) for attempt_id in ids)
        llm = raw_mean_score(quality.get(attempt_id) for attempt_id in ids)
        if det is not None or llm is not None:
            graded.append(GradedCase(ref=ref, split=facts.split_tag, det=det, llm=llm))
    return tuple(graded)


def _calls_by_split(
    refs: Mapping[uuid.UUID, tuple[CaseRef, CaseFacts]],
    calls: Mapping[uuid.UUID, CallCounts],
) -> dict[SplitTag, CallCounts]:
    totals: dict[SplitTag, list[int]] = defaultdict(lambda: [0, 0])
    for item_id, counts in calls.items():
        split = refs[item_id][1].split_tag
        totals[split][0] += counts.total
        totals[split][1] += counts.failed
    return {split: CallCounts(total=t, failed=f) for split, (t, f) in totals.items()}
