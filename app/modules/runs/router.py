"""Endpoint HTTP modul runs.

PBI-11, PBI-12, PBI-13 Eksekusi pengukuran ke produk AI.

Router hanya menerjemahkan HTTP ke pemanggilan service. Tidak ada
logika bisnis dan tidak ada query database di file ini.

Prefix /measurements mengikuti tiket SCRUM-206 (POST /measurements/summary).
TODO(PBI-11, PBI-12, PBI-13): daftar dan detail sesi menyusul.
"""

import uuid

from fastapi import APIRouter, Depends, status

from app.modules.auth.schemas import ErrorBody
from app.modules.runs.dependencies import get_start_service, get_summary_service
from app.modules.runs.schemas import StartRequest, StartResponse, SummaryRequest, SummaryResponse
from app.modules.runs.service import StartSessionService, SummaryService
from app.shared.security import CurrentUser, Role, require_roles

# Admin dan Developer AI boleh membuat pengukuran (PBI-11 user story).
_summary_roles = require_roles(Role.ADMIN, Role.DEVELOPER_AI)

router = APIRouter(prefix="/measurements", tags=["measurements"])

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
