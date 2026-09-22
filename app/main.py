"""Titik masuk aplikasi IndoLegalBench API.

Arsitektur: monolith modular. Tiap modul domain ada di app/modules/,
berlapis di dalamnya (router, service, repository, models, schemas).

Satu-satunya tugas file ini:
1. Membuat aplikasi FastAPI
2. Mendaftarkan router tiap modul
3. Menerjemahkan exception domain jadi HTTP response

Jangan menaruh logika bisnis di file ini.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.modules.audit.router import router as audit_router
from app.modules.auth.oidc import ensure_fake_oidc_allowed
from app.modules.auth.router import router as auth_router
from app.modules.cases.router import router as cases_router
from app.modules.health.router import router as health_router
from app.modules.providers.router import router as providers_router
from app.modules.reports.router import router as reports_router
from app.modules.runs.router import router as runs_router
from app.modules.suites.router import router as suites_router
from app.shared.config import get_settings
from app.shared.dev_db import bootstrap_local_sqlite
from app.shared.exceptions import DomainError

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # TODO(SCRUM-89): drop SQLite create_all / DEV_ZITADEL_SUB
    bootstrap_local_sqlite()
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    """Menerjemahkan exception domain jadi HTTP response.

    Berkat handler ini, service.py tidak perlu tahu apa-apa soal HTTP.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message},
    )


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
app.include_router(runs_router)  # Sprint 3
app.include_router(reports_router)  # Sprint 4
app.include_router(audit_router)  # Sprint 4
