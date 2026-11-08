"""Endpoint HTTP modul runs.

PBI-11, PBI-12, PBI-13 Eksekusi pengukuran ke produk AI.

Router hanya menerjemahkan HTTP ke pemanggilan service. Tidak ada
logika bisnis dan tidak ada query database di file ini.

Prefix /measurements mengikuti tiket SCRUM-206 (POST /measurements/summary).
TODO(PBI-13): penilaian sesi menyusul.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.runs.attempt_calls import attempt_calls
from app.modules.runs.dependencies import (
    get_progress_service,
    get_session_detail_service,
    get_session_list_service,
    get_start_service,
    get_summary_service,
)
from app.modules.runs.schemas import (
    AttemptCalls,
    GradedSessionListItem,
    GradeSessionRequest,
    GradingRunSummary,
    ProgressResponse,
    SessionAcceptRequest,
    SessionAcceptResponse,
    SessionArchiveResponse,
    SessionDetail,
    SessionGradingResponse,
    SessionListItem,
    SessionRetryRequest,
    SessionRetryResponse,
    SessionUnarchiveResponse,
    StartRequest,
    StartResponse,
    SummaryRequest,
    SummaryResponse,
)
from app.modules.runs.service import ProgressService, StartSessionService, SummaryService
from app.modules.runs.session_accept import accept_session, clear_acceptance
from app.modules.runs.session_archive import archive_session
from app.modules.runs.session_control_router import router as control_router
from app.modules.runs.session_detail import SessionDetailService
from app.modules.runs.session_grade import (
    get_session_results,
    grade_session,
    list_graded_sessions,
    list_grading_runs,
)
from app.modules.runs.session_list import SessionListService
from app.modules.runs.session_retry import retry_failed_attempts
from app.modules.runs.session_unarchive import unarchive_session
from app.shared.database import get_db
from app.shared.security import CurrentUser, Role, get_current_user, require_roles

# Admin dan Developer AI boleh membuat pengukuran (PBI-11 user story).
_summary_roles = require_roles(Role.ADMIN, Role.DEVELOPER_AI)
# AC6: Admin dan Developer AI boleh memantau progres pengukuran.
_progress_roles = require_roles(Role.ADMIN, Role.DEVELOPER_AI)
# AC10: peran yang sama memantau sesi di halaman Eksekusi.
_session_list_roles = require_roles(Role.ADMIN, Role.DEVELOPER_AI)
# Peran yang melihat daftar boleh merapikannya. Developer AI hanya sesi miliknya
# (D10 kontrol sesi), dicek di session_archive.
_archive_roles = require_roles(Role.ADMIN, Role.DEVELOPER_AI)
# PBI-12 AC9: hanya Admin yang menerima sesi apa adanya atau menghapus tandanya.
_accept_roles = require_roles(Role.ADMIN)
# PBI-12 AC3, D10 kontrol sesi: Developer AI hanya sesi miliknya, dicek di session_retry.
_retry_roles = require_roles(Role.ADMIN, Role.DEVELOPER_AI)

router = APIRouter(prefix="/measurements", tags=["measurements"])
router.include_router(control_router)

# Deskripsi 404 yang sama di semua route yang membaca satu sesi.
_SESI_TIDAK_ADA = "Sesi tidak ditemukan (`MEASUREMENT_NOT_FOUND`)."

_RESPONS_RINGKASAN = {
    404: {
        "model": ErrorBody,
        "description": "Suite atau produk AI tidak ditemukan (`AI_PRODUCT_NOT_FOUND`).",
    },
    409: {
        "model": ErrorBody,
        "description": "Salah satu suite diarsipkan dan tidak bisa dipilih (`SUITE_ARCHIVED`).",
    },
    422: {
        "model": ErrorBody,
        "description": (
            "Isian tidak valid, produk manual atau nonaktif (`PRODUCT_NOT_AUTOMATIC`, "
            "`PRODUCT_INACTIVE`), tidak ada kasus layak (`NO_ELIGIBLE_CASES`), atau "
            "perkiraan biaya melebihi sisa anggaran (`BUDGET_EXCEEDED`, berisi "
            "`product_name` dan `remaining_budget_idr`)."
        ),
    },
    500: {
        "model": ErrorBody,
        "description": (
            "Konfigurasi server belum lengkap (`PRICE_NOT_CONFIGURED`, "
            "`EXCHANGE_RATE_NOT_CONFIGURED`, `AVG_ANSWER_TOKENS_NOT_CONFIGURED`)."
        ),
    },
}


@router.post(
    "/summary",
    response_model=SummaryResponse,
    summary="Ringkasan dan perkiraan biaya sebelum pengukuran dimulai",
    dependencies=[Depends(_summary_roles)],
    responses=_RESPONS_RINGKASAN,
)
def summarize(
    payload: SummaryRequest,
    service: SummaryService = Depends(get_summary_service),
) -> SummaryResponse:
    return service.summarize(payload)


_RESPONS_PROGRES = {
    404: {
        "model": ErrorBody,
        "description": (
            "Pengukuran tidak ditemukan (`MEASUREMENT_NOT_FOUND`) atau produknya "
            "sudah dihapus (`AI_PRODUCT_NOT_FOUND`)."
        ),
    },
    500: {
        "model": ErrorBody,
        "description": "Anggaran bulanan produk belum disetel (`BUDGET_NOT_CONFIGURED`).",
    },
}


@router.get(
    "/{measurement_id}/progress",
    response_model=ProgressResponse,
    summary="Angka progres pengukuran untuk layar pemantauan",
    dependencies=[Depends(_progress_roles)],
    responses=_RESPONS_PROGRES,
)
def read_progress(
    measurement_id: uuid.UUID,
    service: ProgressService = Depends(get_progress_service),
) -> ProgressResponse:
    return service.read(measurement_id)


@router.get(
    "",
    response_model=list[SessionListItem],
    summary="Daftar sesi pengukuran, terbaru di atas. archived=true menampilkan sesi arsip",
    dependencies=[Depends(_session_list_roles)],
)
def list_sessions(
    archived: bool = False,
    service: SessionListService = Depends(get_session_list_service),
) -> list[SessionListItem]:
    return service.list(archived=archived)


@router.get(
    "/{session_id}",
    response_model=SessionDetail,
    summary="Detail sesi dengan jawaban mentah per kasus dan percobaan",
    responses={404: {"model": ErrorBody, "description": _SESI_TIDAK_ADA}},
)
def get_session(
    session_id: uuid.UUID,
    user: CurrentUser = Depends(_session_list_roles),
    service: SessionDetailService = Depends(get_session_detail_service),
) -> SessionDetail:
    """Developer AI mendapat kasus test yang disamarkan (masked = true)."""
    return service.get(session_id, viewer_role=user.role)


_RESPONS_ARSIP = {
    403: {
        "model": ErrorBody,
        "description": (
            "Peran tidak berwenang (`FORBIDDEN`), atau Developer AI mengarsipkan sesi "
            "buatan orang lain (`NOT_YOUR_SESSION`)."
        ),
    },
    404: {"model": ErrorBody, "description": _SESI_TIDAK_ADA},
    409: {
        "model": ErrorBody,
        "description": (
            "Sesi masih antre atau berjalan (`SESSION_NOT_ARCHIVABLE`), atau sudah "
            "diarsipkan (`SESSION_ALREADY_ARCHIVED`)."
        ),
    },
}


@router.patch(
    "/{session_id}/archive",
    response_model=SessionArchiveResponse,
    summary="Arsipkan sesi yang sudah berakhir supaya tidak tampil di daftar",
    responses=_RESPONS_ARSIP,
)
def archive(
    session_id: uuid.UUID,
    user: CurrentUser = Depends(_archive_roles),
    db: Session = Depends(get_db),
) -> SessionArchiveResponse:
    return archive_session(
        db,
        session_id,
        archived_by=uuid.UUID(str(user.user_id)),
        actor_role=user.role,
        now=datetime.now(UTC),
    )


_RESPONS_UNARCHIVE = {
    403: {
        "model": ErrorBody,
        "description": (
            "Peran tidak berwenang (`FORBIDDEN`), atau Developer AI memulihkan sesi "
            "buatan orang lain (`NOT_YOUR_SESSION`)."
        ),
    },
    404: {"model": ErrorBody, "description": _SESI_TIDAK_ADA},
    409: {"model": ErrorBody, "description": "Sesi belum diarsipkan (`SESSION_NOT_ARCHIVED`)."},
}


@router.patch(
    "/{session_id}/unarchive",
    response_model=SessionUnarchiveResponse,
    summary="Kembalikan sesi dari arsip ke daftar utama tanpa mengubah status",
    responses=_RESPONS_UNARCHIVE,
)
def unarchive(
    session_id: uuid.UUID,
    user: CurrentUser = Depends(_archive_roles),
    db: Session = Depends(get_db),
) -> SessionUnarchiveResponse:
    return unarchive_session(
        db,
        session_id,
        actor_id=uuid.UUID(str(user.user_id)),
        actor_role=user.role,
    )


_RESPONS_TERIMA = {
    403: {"model": ErrorBody, "description": "Hanya Admin (`FORBIDDEN`)."},
    404: {"model": ErrorBody, "description": _SESI_TIDAK_ADA},
}


@router.patch(
    "/{session_id}/accept",
    response_model=SessionAcceptResponse,
    summary="Terima apa adanya: sesi dengan kegagalan dianggap selesai sepenuhnya",
    responses={
        **_RESPONS_TERIMA,
        409: {
            "model": ErrorBody,
            "description": (
                "Sesi bukan Selesai dengan kegagalan (`SESSION_NOT_ACCEPTABLE`), atau sudah "
                "diterima (`ALREADY_ACCEPTED`)."
            ),
        },
        422: {"model": ErrorBody, "description": "Alasan kosong (`REASON_REQUIRED`)."},
    },
)
def accept(
    session_id: uuid.UUID,
    payload: SessionAcceptRequest,
    user: CurrentUser = Depends(_accept_roles),
    db: Session = Depends(get_db),
) -> SessionAcceptResponse:
    return accept_session(
        db,
        session_id,
        reason=payload.reason,
        accepted_by=uuid.UUID(str(user.user_id)),
        now=datetime.now(UTC),
    )


@router.delete(
    "/{session_id}/accept",
    response_model=SessionAcceptResponse,
    summary="Hapus tanda terima apa adanya; sesi bisa dicoba ulang lagi",
    responses={
        **_RESPONS_TERIMA,
        409: {"model": ErrorBody, "description": "Sesi belum diterima (`NOT_ACCEPTED`)."},
    },
    dependencies=[Depends(_accept_roles)],
)
def clear_accept(session_id: uuid.UUID, db: Session = Depends(get_db)) -> SessionAcceptResponse:
    return clear_acceptance(db, session_id)


@router.post(
    "/{session_id}/retry",
    response_model=SessionRetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Coba ulang percobaan yang gagal; sesi berjalan lagi dan runner memanggil ulang",
    responses={
        403: {
            "model": ErrorBody,
            "description": (
                "Peran tidak berwenang (`FORBIDDEN`), atau Developer AI mencoba ulang sesi "
                "buatan orang lain (`NOT_YOUR_SESSION`)."
            ),
        },
        404: {"model": ErrorBody, "description": _SESI_TIDAK_ADA},
        409: {
            "model": ErrorBody,
            "description": (
                "Sesi diarsipkan (`SESSION_ARCHIVED`), manual (`SESSION_NOT_AUTOMATIC`), "
                "bukan Selesai dengan kegagalan (`SESSION_NOT_RETRYABLE`), sudah diterima "
                "apa adanya (`SESSION_ACCEPTED`), atau sesi otomatis lain sedang berjalan "
                "(`ANOTHER_SESSION_RUNNING`)."
            ),
        },
        422: {
            "model": ErrorBody,
            "description": (
                "Pilih `attempt_ids` atau `all_failed`. Percobaan yang bukan gagal teknis di "
                "sesi ini (`ATTEMPT_NOT_RETRYABLE`, berisi `attempt_ids`), atau tidak ada yang "
                "gagal (`NO_FAILED_ATTEMPTS`)."
            ),
        },
    },
)
def retry(
    session_id: uuid.UUID,
    payload: SessionRetryRequest,
    user: CurrentUser = Depends(_retry_roles),
    db: Session = Depends(get_db),
) -> SessionRetryResponse:
    return retry_failed_attempts(
        db,
        session_id,
        payload,
        actor_id=uuid.UUID(str(user.user_id)),
        actor_role=user.role,
        now=datetime.now(UTC),
    )


@router.get(
    "/{session_id}/attempts/{attempt_id}/calls",
    response_model=AttemptCalls,
    summary="Detail teknis setiap upaya panggilan satu percobaan (lihat detail teknis)",
    responses={
        404: {
            "model": ErrorBody,
            "description": (
                "Sesi tidak ditemukan (`MEASUREMENT_NOT_FOUND`), atau percobaan bukan milik "
                "sesi ini (`ATTEMPT_NOT_FOUND`)."
            ),
        },
    },
)
def get_attempt_calls(
    session_id: uuid.UUID,
    attempt_id: uuid.UUID,
    user: CurrentUser = Depends(_session_list_roles),
    db: Session = Depends(get_db),
) -> AttemptCalls:
    """Developer AI hanya melihat detail lengkap kasus dev pada sesi yang sudah berakhir."""
    return attempt_calls(db, session_id, attempt_id, viewer_role=user.role)


# Tanpa endpoint duplikasi sesi (PBI-11 AC1): setiap sesi dibuat lewat Mulai.
@router.post(
    "",
    response_model=StartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Mulai pengukuran: validasi ulang lalu sesi masuk antrean",
    responses=_RESPONS_RINGKASAN,
)
def start(
    payload: StartRequest,
    user: CurrentUser = Depends(_summary_roles),
    service: StartSessionService = Depends(get_start_service),
) -> StartResponse:
    return service.start(payload, created_by=uuid.UUID(str(user.user_id)))


_RESPONS_GRADE = {
    403: {
        "model": ErrorBody,
        "description": "Hanya Admin yang dapat memicu penilaian (`FORBIDDEN`).",
    },
    404: {
        "model": ErrorBody,
        "description": "Pengukuran tidak ditemukan (`MEASUREMENT_NOT_FOUND`).",
    },
    409: {
        "model": ErrorBody,
        "description": (
            "Sesi diarsipkan (`SESSION_ARCHIVED`) atau sesi dalam antrean belum "
            "bisa dinilai (`SESSION_NOT_GRADABLE`). Sesi manual: masih ada jawaban "
            "kosong (`NOT_COMPLETE`) atau statusnya bukan status manual "
            "(`MANUAL_SESSION_STATUS_INVALID`). Jawaban ditimpa selama judge berjalan "
            "(`ANSWERS_CHANGED_DURING_GRADING`) atau sesi diarsipkan di tengah penilaian "
            "(`SESSION_ARCHIVED`): tidak ada hasil yang tersimpan, picu lagi."
        ),
    },
}

_RESPONS_GRADING_RUNS = {
    403: {
        "model": ErrorBody,
        "description": "Hanya Admin yang dapat melihat riwayat penilaian (`FORBIDDEN`).",
    },
    404: {
        "model": ErrorBody,
        "description": "Pengukuran tidak ditemukan (`MEASUREMENT_NOT_FOUND`).",
    },
}

_RESPONS_RESULTS = {
    403: {
        "model": ErrorBody,
        "description": (
            "Hasil sesi yang masih berjalan hanya dapat dilihat Admin (`FORBIDDEN`), "
            "atau peran tidak berwenang (`FORBIDDEN`)."
        ),
    },
    404: {
        "model": ErrorBody,
        "description": (
            "Pengukuran tidak ditemukan (`MEASUREMENT_NOT_FOUND`) atau hasil penilaian "
            "belum tersedia (`GRADING_NOT_FOUND`)."
        ),
    },
}


results_router = APIRouter(prefix="/results", tags=["results"])


@results_router.get(
    "/sessions",
    response_model=list[GradedSessionListItem],
    summary="Daftar sesi selesai yang telah dinilai untuk Admin dan Viewer (SCRUM-226)",
)
def list_graded_sessions_endpoint(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[GradedSessionListItem]:
    """Daftar sesi selesai yang telah dinilai. Dapat diakses oleh Admin dan Viewer."""
    return list_graded_sessions(db, viewer_role=user.role)


@router.post(
    "/{session_id}/grade",
    response_model=SessionGradingResponse,
    summary="Memicu penilaian manual untuk sesi pengukuran (Admin)",
    dependencies=[Depends(require_roles(Role.ADMIN))],
    responses=_RESPONS_GRADE,
)
def trigger_grade_endpoint(
    session_id: uuid.UUID,
    payload: GradeSessionRequest | None = None,
    user: CurrentUser = Depends(require_roles(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> SessionGradingResponse:
    """Admin memicu penilaian manual untuk sesi otomatis atau manual (PBI-14 AC6, AC7)."""
    return grade_session(
        db,
        session_id,
        payload,
        actor_id=uuid.UUID(str(user.user_id)),
        actor_role=user.role,
        now=datetime.now(UTC),
    )


@router.get(
    "/{session_id}/grading-runs",
    response_model=list[GradingRunSummary],
    summary="Riwayat penilaian sesi pengukuran, terbaru di atas (Admin)",
    dependencies=[Depends(require_roles(Role.ADMIN))],
    responses=_RESPONS_GRADING_RUNS,
)
def list_grading_runs_endpoint(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[GradingRunSummary]:
    """Semua penilaian sesi ini, termasuk hasil lama sebelum nilai ulang (PBI-14 AC7)."""
    return list_grading_runs(db, session_id)


@router.get(
    "/{session_id}/results",
    response_model=SessionGradingResponse,
    summary="Hasil penilaian sesi pengukuran dengan perincian skor per kasus dan agregat",
    responses=_RESPONS_RESULTS,
)
def get_results_endpoint(
    session_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionGradingResponse:
    """Hasil sesi berjalan hanya untuk Admin. Sesi selesai untuk Admin dan Viewer. Developer AI mendapat kasus test tersamar (PBI-14 AC8, PBI-11 AC9)."""
    return get_session_results(
        db,
        session_id,
        viewer_role=user.role,
    )
