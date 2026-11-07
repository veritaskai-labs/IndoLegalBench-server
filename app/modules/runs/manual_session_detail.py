"""Halaman sesi manual: progres dan kartu kasus. PBI-22 (SCRUM-176) AC3, AC4, AC8, AC11-AC13.

Fungsi murni: baris dari ManualStatusStore masuk, respons keluar (review #116). Kasus
dibaca dari snapshot sesi (AC2), bukan kasus live, jadi pertanyaan yang disalin tim legal
sama dengan yang dibekukan saat sesi dibuat. Progres "X dari N" selalu untuk seluruh sesi;
filter "belum terisi" hanya menyaring kartu yang dikirim.
"""

import uuid
from collections import defaultdict
from collections.abc import Sequence

from app.modules.runs.manual_session_rules import date_range_exceeds
from app.modules.runs.schemas import (
    CaseFilter,
    ManualAttemptSlot,
    ManualCaseProgress,
    ManualSessionDetail,
    MeasurementPurpose,
)
from app.modules.runs.session_ports import ManualAttemptRow, ManualCaseRow, ManualSessionRow


def build_manual_detail(
    row: ManualSessionRow,
    cases: Sequence[ManualCaseRow],
    attempts: Sequence[ManualAttemptRow],
    *,
    unfilled_only: bool,
    warning_days: int | None,
) -> ManualSessionDetail:

    slots = _slots_per_case(attempts)
    cards = sorted(
        (_card(case, slots[case.snapshot_item_id]) for case in cases),
        key=lambda card: card.case_code,
    )
    return ManualSessionDetail(
        id=row.id,
        label=row.label,
        purpose=MeasurementPurpose(row.purpose),
        status=row.status,
        product_name=row.product_name,
        vendor_name=row.vendor_name,
        site_url=row.site_url,
        manual_params=row.manual_params,
        case_filter=CaseFilter(row.case_filter),
        repetitions=row.repetitions,
        filled_answers=sum(attempt.filled for attempt in attempts),
        total_answers=len(attempts),
        answer_date_from=row.answer_date_from,
        answer_date_to=row.answer_date_to,
        date_range_warning=date_range_exceeds(
            row.answer_date_from, row.answer_date_to, limit_days=warning_days
        ),
        created_by=row.created_by,
        created_at=row.created_at,
        finalized_at=row.finalized_at,
        finalized_by=row.finalized_by,
        cases=[card for card in cards if not (unfilled_only and card.filled)],
    )


def _slots_per_case(
    attempts: Sequence[ManualAttemptRow],
) -> dict[uuid.UUID, list[ManualAttemptSlot]]:
    slots: dict[uuid.UUID, list[ManualAttemptSlot]] = defaultdict(list)
    for attempt in sorted(attempts, key=lambda a: (a.snapshot_item_id, a.repetition_no)):
        slots[attempt.snapshot_item_id].append(
            ManualAttemptSlot(
                attempt_id=attempt.id,
                repetition_no=attempt.repetition_no,
                filled=attempt.filled,
                answered_at_platform=attempt.answered_at_platform,
                source=attempt.source,
                answer_md=attempt.answer_md,
                platform_refused=attempt.platform_refused,
                note=attempt.note,
                filled_by=attempt.filled_by,
                filled_at=attempt.filled_at,
            )
        )
    return slots


def _card(case: ManualCaseRow, slots: list[ManualAttemptSlot]) -> ManualCaseProgress:
    # Bentuk snapshot_body() PBI-8: bagian kasus ada di body["sections"].
    sections = case.body.get("sections") or {}
    return ManualCaseProgress(
        snapshot_item_id=case.snapshot_item_id,
        case_id=case.case_id,
        case_code=sections.get("case_code") or "",
        title=sections.get("identity.title") or "",
        question=sections.get("identity.question") or "",
        split_tag=sections.get("split_tag") or "",
        filled=all(slot.filled for slot in slots),
        attempts=slots,
    )
