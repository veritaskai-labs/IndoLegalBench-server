"""Read-time session aggregation with an injected grading reader."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.runs import repository
from app.modules.runs.adapters import SnapshotCaseAdapter
from app.modules.runs.grading_ports import GradeScoreSnapshot as GradeScoreSnapshot, GradingReaderPort
from app.modules.runs.session_scores import AttemptCase, SessionScores, aggregate_attempt_maps
from app.modules.runs.snapshot_cases import CaseFacts


@dataclass(frozen=True)
class SessionScoreReport:
    run_id: uuid.UUID | None
    rubric_version_id: uuid.UUID | None
    quality: SessionScores
    format: SessionScores


def _aggregate(
    facts: tuple[CaseFacts, ...],
    attempts: list[tuple[uuid.UUID, uuid.UUID]],
    deterministic: Mapping[uuid.UUID, Decimal | None],
    judge: Mapping[uuid.UUID, Decimal | None],
) -> SessionScores:
    return aggregate_attempt_maps(
        facts,
        [AttemptCase(attempt_id, case_id) for attempt_id, case_id in attempts],
        deterministic,
        judge,
    )


def read_session_report(
    db: Session,
    session_id: uuid.UUID,
    reader: GradingReaderPort,
) -> SessionScoreReport:
    """Calculate quality and format independently, without persisting them."""
    facts = SnapshotCaseAdapter(db).case_facts(session_id)
    attempts = repository.session_attempt_items(db, session_id)
    stored = reader.latest(session_id)

    return SessionScoreReport(
        run_id=stored.run_id,
        rubric_version_id=stored.rubric_version_id,
        quality=_aggregate(facts, attempts, stored.deterministic, stored.quality),
        format=_aggregate(facts, attempts, stored.deterministic, stored.format),
    )
