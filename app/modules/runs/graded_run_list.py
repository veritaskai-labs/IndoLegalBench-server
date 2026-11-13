"""Daftar laporan: satu baris per grading run (PBI-14 AC8, PBI-22 AC9, SCRUM-296).

Feedback UAT klien 9 Nov (K-21): hasil penilaian baru harus muncul sebagai entri sendiri di
halaman Laporan, dan nilai ulang tidak menghapus baris lama. Run dibaca lewat GradingStore,
sama seperti riwayat penilaian, jadi judge, versi rubrik, dan skornya tidak dihitung ulang
di sini.
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.providers import service as providers_service
from app.modules.runs.models import Attempt, CallLog, MeasurementSession
from app.modules.runs.schemas import GradedRunListItem, SessionGradingResponse
from app.modules.runs.session_grade import MASKED_READER_ROLES, DbGradingStore, GradingStore
from app.modules.runs.session_rules import has_ended
from app.modules.runs.stored_cost import recorded_sum, rupiah_sum
from app.shared.exceptions import ForbiddenError
from app.shared.security import Role


def _status_text(session: MeasurementSession) -> str:
    return getattr(session.status, "value", str(session.status))


def to_list_item(
    session: MeasurementSession,
    product_name: str,
    run: SessionGradingResponse,
    *,
    measurement_cost_idr: Decimal | None,
):
    """Satu baris laporan dari satu run dan sesi pemiliknya.

    Biaya pengukuran dibaca dari call_logs sesi itu. Biaya penilai dibaca dari
    run. Keduanya tidak dihitung ulang di sini.
    """
    judge_cost = None if run.judge is None else run.judge.cost_idr
    return GradedRunListItem(
        grading_run_id=run.grading_run_id,
        session_id=session.id,
        label=session.label,
        product_name=product_name,
        session_type=session.session_type.value,
        judge=run.judge,
        rubric_version=run.rubric_version,
        graded_at=run.triggered_at,
        status=run.status,
        is_latest=run.is_latest,
        scores=run.scores,
        measurement_cost_idr=measurement_cost_idr,
        judge_cost_idr=judge_cost,
        total_cost_idr=rupiah_sum(measurement_cost_idr, judge_cost),
    )


def list_graded_runs(
    db: Session,
    *,
    viewer_role: Role | str,
    store: GradingStore | None = None,
) -> list[GradedRunListItem]:
    """Satu baris per grading run dari sesi selesai yang tidak diarsipkan, terbaru di atas.

    Admin, Viewer, dan Kontributor boleh membaca (SCRUM-276); peran lain ditolak 403. Sesi
    tanpa hasil penilaian tidak menghasilkan baris.
    """
    role = Role(viewer_role) if isinstance(viewer_role, str) else viewer_role
    if role != Role.ADMIN and role not in MASKED_READER_ROLES:
        raise ForbiddenError(
            "Peran Anda tidak berwenang melihat daftar hasil penilaian.",
            code="FORBIDDEN",
        )

    sessions = [
        s
        for s in db.scalars(
            select(MeasurementSession).where(MeasurementSession.archived_at.is_(None))
        ).all()
        if has_ended(_status_text(s))
    ]
    if not sessions:
        return []

    grading_store = store or DbGradingStore(db)
    product_names = providers_service.product_names(db, {s.product_id for s in sessions})
    costs = _measurement_costs(db, sessions)
    items = [
        to_list_item(
            session,
            product_names.get(session.product_id, ""),
            run,
            measurement_cost_idr=costs[session.id],
        )
        for session in sessions
        for run in grading_store.get_grading_runs(session.id)
    ]
    return sorted(items, key=lambda item: item.graded_at, reverse=True)


def _measurement_costs(db: Session, sessions: list[MeasurementSession]) -> dict:
    """Manual selalu 0. Otomatis tanpa panggilan 0. Panggilan yang semuanya kosong tetap kosong."""
    costs: dict = {}
    automatic: list = []
    for session in sessions:
        if _is_manual(session):
            costs[session.id] = Decimal("0.00")
        else:
            automatic.append(session.id)
            costs[session.id] = Decimal("0.00")
    if not automatic:
        return costs
    rows = db.execute(
        select(
            Attempt.session_id,
            func.count(CallLog.id),
            func.count(CallLog.cost_idr),
            func.coalesce(func.sum(CallLog.cost_idr), 0),
        )
        .join(CallLog, CallLog.attempt_id == Attempt.id)
        .where(Attempt.session_id.in_(automatic))
        .group_by(Attempt.session_id)
    )
    for session_id, call_count, priced_count, total in rows:
        costs[session_id] = recorded_sum(call_count, priced_count, total)
    return costs


def _is_manual(session: MeasurementSession) -> bool:
    return getattr(session.session_type, "value", session.session_type) == "manual"
