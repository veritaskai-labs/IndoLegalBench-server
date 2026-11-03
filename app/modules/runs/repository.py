"""Akses database modul runs.

ATURAN: hanya runs/service.py yang boleh memanggil file ini. Modul lain
tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis.

TODO(PBI-11, PBI-12, PBI-13): query lain menyusul sesuai kebutuhan service.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Row, exists, func, select
from sqlalchemy.orm import Session

from app.modules.runs.models import (
    Attempt,
    AttemptStatus,
    CallLog,
    MeasurementSession,
)

# Panggilan yang sudah diproses dan tidak diulang lagi (PBI-12 AC5).
_SELESAI = (AttemptStatus.SUCCEEDED, AttemptStatus.TECHNICAL_FAILURE)


def product_has_sessions(db: Session, product_id: uuid.UUID) -> bool:
    """Sesi yang diarsipkan tetap terhitung: datanya tidak pernah dihapus (AC10)."""
    return bool(db.scalar(select(exists().where(MeasurementSession.product_id == product_id))))


def session_progress(db: Session, measurement_id: uuid.UUID) -> Row | None:
    """Angka satu sesi untuk layar progres, atau None bila sesinya tidak ada.

    Dihitung lewat subquery supaya satu panggilan database cukup: endpoint ini
    di-poll tiap beberapa detik. Biaya dijumlahkan lewat attempt_id, bukan lewat
    call_logs.product_id, karena yang diminta biaya sesi ini saja.
    """
    jumlah_attempt = (
        select(func.count())
        .select_from(Attempt)
        .where(Attempt.session_id == MeasurementSession.id)
        .correlate(MeasurementSession)
        .scalar_subquery()
    )
    jumlah_selesai = (
        select(func.count())
        .select_from(Attempt)
        .where(
            Attempt.session_id == MeasurementSession.id,
            Attempt.status.in_(_SELESAI),
        )
        .correlate(MeasurementSession)
        .scalar_subquery()
    )
    jumlah_gagal = (
        select(func.count())
        .select_from(Attempt)
        .where(
            Attempt.session_id == MeasurementSession.id,
            Attempt.status == AttemptStatus.TECHNICAL_FAILURE,
        )
        .correlate(MeasurementSession)
        .scalar_subquery()
    )
    biaya = (
        select(func.coalesce(func.sum(CallLog.cost_idr), 0))
        .select_from(CallLog)
        .join(Attempt, Attempt.id == CallLog.attempt_id)
        .where(Attempt.session_id == MeasurementSession.id)
        .correlate(MeasurementSession)
        .scalar_subquery()
    )
    return db.execute(
        select(
            MeasurementSession.product_id,
            MeasurementSession.started_at,
            MeasurementSession.updated_at,
            jumlah_attempt.label("total_calls"),
            jumlah_selesai.label("completed_calls"),
            jumlah_gagal.label("failed_calls"),
            biaya.label("running_cost_idr"),
        ).where(MeasurementSession.id == measurement_id)
    ).one_or_none()


class CallLogRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def add(self, row: CallLog) -> None:
        self._db.add(row)
        self._db.flush()

    def sum_cost_since(self, product_id: uuid.UUID, start: datetime) -> Decimal:
        total = self._db.scalar(
            select(func.coalesce(func.sum(CallLog.cost_idr), 0)).where(
                CallLog.product_id == product_id,
                CallLog.started_at >= start,
                CallLog.cost_idr.is_not(None),
            )
        )
        return Decimal(total)

    def output_token_average(self, product_id: uuid.UUID) -> Decimal | None:
        average = self._db.scalar(
            select(func.avg(CallLog.output_tokens)).where(
                CallLog.product_id == product_id,
                CallLog.output_tokens.is_not(None),
            )
        )
        if average is None:
            return None
        return Decimal(average)

    def attempt_counts(self, product_id: uuid.UUID) -> tuple[int, int]:
        total = self._db.scalar(
            select(func.count()).select_from(CallLog).where(CallLog.product_id == product_id)
        )
        failed = self._db.scalar(
            select(func.count())
            .select_from(CallLog)
            .where(CallLog.product_id == product_id, CallLog.failure_kind.is_not(None))
        )
        return int(total or 0), int(failed or 0)
