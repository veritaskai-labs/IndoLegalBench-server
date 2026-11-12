"""Perkiraan biaya penilaian satu sesi sebelum tombol Nilai (PBI-14 AC6, SCRUM-286).

Memuat percobaan sesi, melewati gangguan teknis, menghitung token prompt kualitas dan format,
lalu menyerahkannya ke estimate_grade (fungsi murni). Aturan kelayakan sesi, pemilihan judge,
versi rubrik, harga, kurs, dan sisa anggaran memakai kode penilaian yang sama dengan
grade_session, supaya angka di sini tidak berbeda dengan biaya nyata. Judge tidak dipanggil
dan tidak ada yang disimpan.
"""

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.providers import judge_service
from app.modules.runs.call_log import month_start
from app.modules.runs.exchange_rate import ExchangeRateProvider
from app.modules.runs.grade_estimate import estimate_grade
from app.modules.runs.judge import format_prompt, quality_prompt
from app.modules.runs.judge_budget import JudgeMeter, JudgePricingMissing, open_judge_meter
from app.modules.runs.manual_grade_gate import ManualGradeGate
from app.modules.runs.models import MeasurementSession
from app.modules.runs.repository import CallLogRepository, ManualSessionRepository
from app.modules.runs.schemas import GradeEstimateResponse, GradeSessionRequest
from app.modules.runs.session_grade import (
    MEASUREMENT_NOT_FOUND_MESSAGE,
    _build_judge_inputs,
    _load_case_grade_inputs,
    _resolve_rubric_version,
    _should_grade_with_judge,
    ensure_session_gradable,
    resolve_judge_id,
)
from app.modules.runs.tokenizer import TokenCounter
from app.shared.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.shared.security import Role

logger = logging.getLogger(__name__)

JUDGE_NOT_CONFIGURED = "JUDGE_NOT_CONFIGURED"
JUDGE_PRICING_MISSING = "JUDGE_PRICING_MISSING"
_JUDGE_FIELD = "judge_provider_id"
_CENT = Decimal("0.01")


def estimate_session_grade(
    db: Session,
    session_id: uuid.UUID,
    payload: GradeSessionRequest | None = None,
    *,
    actor_role: Role,
    rates: ExchangeRateProvider,
    counter: TokenCounter,
    output_tokens_per_call: int,
    now: datetime | None = None,
) -> GradeEstimateResponse:
    """Perkiraan biaya menilai sesi ini dengan judge dan versi rubrik yang dipilih Admin."""
    if actor_role != Role.ADMIN:
        raise ForbiddenError("Hanya Admin yang dapat melihat perkiraan biaya penilaian.")
    session = db.get(MeasurementSession, session_id)
    if session is None:
        raise NotFoundError(MEASUREMENT_NOT_FOUND_MESSAGE, code="MEASUREMENT_NOT_FOUND")
    _ensure_gradable(db, session)

    judge_id = _require_judge(resolve_judge_id(db, session, payload and payload.judge_provider_id))
    rubric_version = _resolve_rubric_version(db, payload.rubric_version if payload else None)
    pricing = judge_service.judge_pricing(db, judge_id)
    meter = _open_meter(db, pricing, rates, now or datetime.now(UTC))

    estimate = estimate_grade(
        attempt_prompt_tokens=_attempt_prompt_tokens(
            db, session_id, counter, pricing.provider_type
        ),
        output_tokens_per_call=output_tokens_per_call,
        prices=meter.prices,
    )
    remaining = max(meter.remaining_idr, Decimal(0)).quantize(_CENT)
    return GradeEstimateResponse(
        judge_provider_id=judge_id,
        rubric_version=rubric_version,
        attempts_to_grade=estimate.attempts_to_grade,
        judge_calls=estimate.judge_calls,
        estimated_input_tokens=estimate.input_tokens,
        estimated_output_tokens=estimate.output_tokens,
        estimated_cost_idr=estimate.cost_idr,
        usd_idr_rate=meter.prices.usd_idr_rate,
        remaining_budget_idr=remaining,
        budget_sufficient=estimate.cost_idr <= remaining,
    )


def _ensure_gradable(db: Session, session: MeasurementSession) -> None:
    """Prasyarat yang sama dengan tombol Nilai: sesi manual D7, sesi otomatis D4, dan arsip."""
    gate = ManualGradeGate(ManualSessionRepository(db))
    if gate.before_grading(session.id) is None:
        ensure_session_gradable(session)


def _require_judge(judge_id: uuid.UUID | None) -> uuid.UUID:
    if judge_id is None:
        raise ValidationError(
            "Pilih LLM judge: sesi ini tidak punya judge.",
            code=JUDGE_NOT_CONFIGURED,
            field=_JUDGE_FIELD,
        )
    return judge_id


def _open_meter(
    db: Session,
    pricing: judge_service.JudgePricing,
    rates: ExchangeRateProvider,
    now: datetime,
) -> JudgeMeter:
    """Meter yang sama dengan penilaian, supaya harga, kurs, dan sisa anggaran identik."""
    spent = CallLogRepository(db).sum_cost_since(pricing.judge_provider_id, month_start(now))
    try:
        return open_judge_meter(binding=pricing, spent_idr=spent, rates=rates)
    except JudgePricingMissing:
        # Hanya id penilai yang dicatat, tanpa kredensial.
        logger.warning("Judge %s has no complete token price", pricing.judge_provider_id)
        raise ValidationError(
            "Harga token masuk dan keluar produk judge ini belum lengkap.",
            code=JUDGE_PRICING_MISSING,
            field=_JUDGE_FIELD,
        ) from None


def _attempt_prompt_tokens(
    db: Session, session_id: uuid.UUID, counter: TokenCounter, provider_type: str
) -> list[tuple[int, int]]:
    """Token prompt kualitas dan format tiap percobaan yang akan dikirim ke judge."""
    tokens: list[tuple[int, int]] = []
    for case in _load_case_grade_inputs(db, session_id):
        for attempt in case.attempts:
            if not _should_grade_with_judge(attempt):
                continue
            inputs = _build_judge_inputs(case, attempt.answer_text)
            tokens.append(
                (
                    counter.count(quality_prompt(inputs), provider_type),
                    counter.count(format_prompt(inputs), provider_type),
                )
            )
    return tokens
