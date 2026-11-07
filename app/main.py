"""Titik masuk aplikasi IndoLegalBench API.

Arsitektur: monolith modular. Tiap modul domain ada di app/modules/,
berlapis di dalamnya (router, service, repository, models, schemas).

Satu-satunya tugas file ini:
1. Membuat aplikasi FastAPI
2. Mendaftarkan router tiap modul
3. Menerjemahkan exception domain jadi HTTP response

Jangan menaruh logika bisnis di file ini.
"""

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.modules.audit import listener as audit_listener
from app.modules.audit.router import router as audit_router
from app.modules.auth.cookies import clear_session_cookie
from app.modules.auth.oidc import ensure_fake_oidc_allowed
from app.modules.auth.router import router as auth_router
from app.modules.cases.router import router as cases_router
from app.modules.cases.validation import response_from_pydantic
from app.modules.dashboard.router import router as dashboard_router
from app.modules.grading.router import router as grading_router
from app.modules.health.router import router as health_router
from app.modules.legal_refs.router import router as legal_refs_router
from app.modules.notifications.router import router as notifications_router
from app.modules.providers.judge_router import options_router as judge_options_router
from app.modules.providers.judge_router import router as judge_providers_router
from app.modules.providers.router import options_router as product_options_router
from app.modules.providers.router import router as providers_router
from app.modules.reports.router import router as reports_router
from app.modules.reviews.router import router as reviews_router
from app.modules.reviews.validation import verdict_error_from_pydantic
from app.modules.runs.manual_router import router as manual_runs_router
from app.modules.runs.router import router as runs_router
from app.modules.suites.router import router as suites_router
from app.shared.config import get_settings
from app.shared.dev_db import bootstrap_local_sqlite
from app.shared.exceptions import DomainError, SessionExpiredError
from app.shared.security import (
    ABSOLUTE_EXPIRES_HEADER,
    IDLE_DEADLINE_HEADER,
    IDLE_TIMEOUT_HEADER,
)
from app.shared.validation_errors import detail_aman

settings = get_settings()
logger = logging.getLogger(__name__)

# PBI-18: setiap perubahan di tabel katalog D6a tercatat otomatis.
audit_listener.install()


def _recover_sessions_on_startup() -> None:
    """SCRUM-217: Saat API server menyala, pulihkan sesi RUNNING jika worker lease bebas."""
    try:
        from app.modules.runs.execution import worker_lease
        from app.modules.runs.recovery import recover_after_restart
        from app.shared.database import get_session_factory

        with get_session_factory()() as db, worker_lease(db.get_bind()) as acquired:
            if not acquired:
                logger.info("Worker lease currently held by active worker; skipping API recovery.")
                return
            recover_after_restart(db)
    except Exception:
        # Menjaga server tetap boot bila database belum siap atau migrasi belum selesai.
        logger.exception("Failed to recover sessions on startup")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # TODO(SCRUM-89): drop SQLite create_all / DEV_ZITADEL_SUB
    bootstrap_local_sqlite()
    _recover_sessions_on_startup()
    yield


app = FastAPI(
    title=settings.app_name,
    description=(
        "API IndoLegalBench, platform penulisan dan pengukuran test case "
        "hukum untuk Veritask. Dokumentasi ini dihasilkan otomatis dari "
        "schema Pydantic dan menjadi kontrak resmi antara server dan client."
    ),
    version="0.1.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Credentialed fetches only see response headers named here. The session
# clocks are set on every successful get_current_user response.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        IDLE_TIMEOUT_HEADER,
        IDLE_DEADLINE_HEADER,
        ABSOLUTE_EXPIRES_HEADER,
    ],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """PBI-18: satu id per request, mengikat baris audit yang lahir bersama."""
    request.state.request_id = uuid.uuid4()
    response = await call_next(request)
    response.headers["X-Request-ID"] = str(request.state.request_id)
    return response


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    """Menerjemahkan exception domain jadi HTTP response.

    Berkat handler ini, service.py tidak perlu tahu apa-apa soal HTTP.
    """
    # details lebih dulu supaya code dan message tidak bisa tertimpa.
    content = {**exc.details, "code": exc.code, "message": exc.message}
    if exc.field:
        content["field"] = exc.field
    response = JSONResponse(
        status_code=exc.status_code,
        content=content,
    )
    if isinstance(exc, SessionExpiredError):
        clear_session_cookie(response, get_settings())
    return response


def _tulisan_kasus(request: Request) -> bool:
    """True for case create and update, which use the cases validation error codes.

    Matches POST /suites/{suite_id}/cases and PUT /cases/{case_id}, plus the
    legal reference preview (SCRUM-162). Update the sites together if those
    routes move.
    """
    if request.method not in {"POST", "PUT"}:
        return False
    path = request.url.path.rstrip("/")
    if path.startswith("/cases/") or path == "/legal-refs/preview":
        return True
    bagian = path.strip("/").split("/")
    return len(bagian) == 3 and bagian[0] == "suites" and bagian[2] == "cases"


def _penilaian(request: Request) -> bool:
    """True for POST /reviews/{assignment_id}/verdict, which uses the reviews error codes."""
    bagian = request.url.path.strip("/").split("/")
    return (
        request.method == "POST"
        and len(bagian) == 3
        and bagian[0] == "reviews"
        and bagian[2] == "verdict"
    )


@app.exception_handler(RequestValidationError)
async def case_validation_handler(request: Request, exc: RequestValidationError):
    """Map case and verdict body failures to the team's error shape.

    Other routes keep FastAPI's detail, with non-finite numbers written as text.
    """
    if _tulisan_kasus(request):
        return JSONResponse(status_code=422, content=response_from_pydantic(exc.errors()))
    if _penilaian(request):
        return JSONResponse(status_code=422, content=verdict_error_from_pydantic(exc.errors()))
    return JSONResponse(status_code=422, content={"detail": detail_aman(exc.errors())})


# Urutan pendaftaran mengikuti urutan PBI di Sprint 1
app.include_router(health_router)
app.include_router(auth_router)  # PBI-1
if settings.auth_oidc_mode == "fake":
    ensure_fake_oidc_allowed(settings)
    from app.modules.auth.oidc_fake_router import fake_router

    app.include_router(fake_router)
app.include_router(suites_router)  # PBI-2
app.include_router(cases_router)  # PBI-3
app.include_router(providers_router)  # PBI-10
app.include_router(product_options_router)  # PBI-23 SCRUM-201
app.include_router(reviews_router)  # PBI-6
app.include_router(notifications_router)  # PBI-19 SCRUM-153
app.include_router(runs_router)  # Sprint 3
app.include_router(manual_runs_router)  # PBI-22
app.include_router(reports_router)  # Sprint 4
app.include_router(audit_router)  # PBI-18
app.include_router(legal_refs_router)  # PBI-21
app.include_router(dashboard_router)  # PBI-19
app.include_router(grading_router)  # PBI-14
app.include_router(judge_providers_router)  # PBI-14 SCRUM-258
app.include_router(judge_options_router)  # PBI-14 SCRUM-258
