"""Daftar laporan: satu baris per grading run (PBI-14 AC8, PBI-22 AC9, SCRUM-296).

Feedback UAT klien 9 Nov (K-21): hasil penilaian baru harus muncul sebagai entri sendiri di
halaman Laporan, dan nilai ulang tidak menghapus baris lama. Run dibaca lewat GradingStore,
sama seperti riwayat penilaian, jadi judge, versi rubrik, dan skornya tidak dihitung ulang
di sini.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.providers import service as providers_service
from app.modules.runs.models import MeasurementSession
from app.modules.runs.schemas import GradedRunListItem, SessionGradingResponse
from app.modules.runs.session_grade import MASKED_READER_ROLES, DbGradingStore, GradingStore
from app.modules.runs.session_rules import has_ended
from app.shared.exceptions import ForbiddenError
from app.shared.security import Role


def _status_text(session: MeasurementSession) -> str:
    return getattr(session.status, "value", str(session.status))


def to_list_item(session: MeasurementSession, product_name: str, run: SessionGradingResponse):
    """Satu baris laporan dari satu run dan sesi pemiliknya."""
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
    items = [
        to_list_item(session, product_names.get(session.product_id, ""), run)
        for session in sessions
        for run in grading_store.get_grading_runs(session.id)
    ]
    return sorted(items, key=lambda item: item.graded_at, reverse=True)
