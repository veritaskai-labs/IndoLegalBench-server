"""Halaman sesi manual: progres dan kartu kasus. PBI-22 (SCRUM-176) AC3, AC4, AC8, AC11-AC13.

Kasus dibaca dari snapshot sesi (AC2), bukan kasus live, jadi pertanyaan yang disalin tim
legal sama dengan yang dibekukan saat sesi dibuat. Progres "X dari N" selalu untuk seluruh
sesi; filter "belum terisi" hanya menyaring kartu yang dikirim.
"""

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.runs.manual_session_rules import date_range_exceeds
from app.modules.runs.models import (
    Attempt,
    CaseSnapshot,
    CaseSnapshotItem,
    MeasurementSession,
    SessionProductCopy,
    SessionType,
)
from app.modules.runs.schemas import (
    CaseFilter,
    ManualAttemptSlot,
    ManualCaseProgress,
    ManualSessionDetail,
    MeasurementPurpose,
)
from app.shared.exceptions import NotFoundError


def manual_session_detail(
    db: Session,
    session_id: uuid.UUID,
    *,
    unfilled_only: bool,
    warning_days: int | None,
) -> ManualSessionDetail:
    session = db.get(MeasurementSession, session_id)
    if session is None or session.session_type != SessionType.MANUAL:
        raise NotFoundError(
            "Sesi pengukuran manual tidak ditemukan.", code="MANUAL_SESSION_NOT_FOUND"
        )
    product = db.scalars(
        select(SessionProductCopy).where(SessionProductCopy.session_id == session_id)
    ).one()
    slots: dict[uuid.UUID, list[ManualAttemptSlot]] = defaultdict(list)
    for attempt in db.scalars(
        select(Attempt)
        .where(Attempt.session_id == session_id)
        .order_by(Attempt.snapshot_item_id, Attempt.repetition_no)
    ):
        slots[attempt.snapshot_item_id].append(
            ManualAttemptSlot(
                attempt_id=attempt.id,
                repetition_no=attempt.repetition_no,
                filled=attempt.filled_at is not None,
                answered_at_platform=attempt.answered_at_platform,
            )
        )
    cases = sorted(
        (_case(item, slots[item.id]) for item in _snapshot_items(db, session_id)),
        key=lambda case: case.case_code,
    )
    all_slots = [slot for case in cases for slot in case.attempts]
    return ManualSessionDetail(
        id=session.id,
        label=session.label,
        purpose=MeasurementPurpose(session.purpose.value),
        status=session.status,
        product_name=product.name,
        vendor_name=product.vendor_name,
        site_url=product.site_url,
        case_filter=CaseFilter(session.split_filter.value),
        repetitions=session.repetitions,
        filled_answers=sum(slot.filled for slot in all_slots),
        total_answers=len(all_slots),
        answer_date_from=session.answer_date_from,
        answer_date_to=session.answer_date_to,
        date_range_warning=date_range_exceeds(
            session.answer_date_from, session.answer_date_to, limit_days=warning_days
        ),
        created_by=session.created_by,
        created_at=session.created_at,
        finalized_at=session.finalized_at,
        finalized_by=session.finalized_by,
        cases=[case for case in cases if not (unfilled_only and case.filled)],
    )


def _snapshot_items(db: Session, session_id: uuid.UUID) -> list[CaseSnapshotItem]:
    return list(
        db.scalars(
            select(CaseSnapshotItem)
            .join(CaseSnapshot, CaseSnapshot.id == CaseSnapshotItem.snapshot_id)
            .where(CaseSnapshot.session_id == session_id)
        )
    )


def _case(item: CaseSnapshotItem, slots: list[ManualAttemptSlot]) -> ManualCaseProgress:
    # Bentuk snapshot_body() PBI-8: bagian kasus ada di body["sections"].
    sections = item.body.get("sections") or {}
    return ManualCaseProgress(
        snapshot_item_id=item.id,
        case_id=item.case_id,
        case_code=sections.get("case_code") or "",
        title=sections.get("identity.title") or "",
        question=sections.get("identity.question") or "",
        split_tag=sections.get("split_tag") or "",
        filled=all(slot.filled for slot in slots),
        attempts=slots,
    )
