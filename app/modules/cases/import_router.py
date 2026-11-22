"""Endpoint impor kasus lewat CSV.

GET /case-imports/template   template CSV
POST /case-imports           unggah satu berkas, kembalikan ringkasan
GET /case-imports/{batch_id} buka lagi ringkasan satu batch

Hanya Kontributor dan Admin, sama dengan menulis kasus. Path terpisah dari /cases supaya
tidak bertabrakan dengan /cases/{case_id}.
"""

import uuid

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.types import Message

from app.modules.auth.schemas import ErrorBody
from app.modules.cases import import_service
from app.modules.cases.csv_import import IMPORT_FILE_TOO_LARGE, ImportFileError, template_csv
from app.modules.cases.import_schemas import ImportSummaryRead
from app.shared.database import get_db
from app.shared.exceptions import ValidationError
from app.shared.security import WRITER_ROLES, CurrentUser, Role, require_roles

router = APIRouter(tags=["case-imports"])

_can_write = require_roles(*WRITER_ROLES, Role.ADMIN)

IMPORT_FILE_REQUIRED = "IMPORT_FILE_REQUIRED"
TEMPLATE_FILE_NAME = "template-impor-kasus.csv"
# Ruang untuk boundary dan header multipart di atas batas berkas.
_MULTIPART_OVERHEAD = 64 * 1024

_UPLOAD_FORM = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {
                            "type": "string",
                            "format": "binary",
                            "description": "Berkas .csv dari template, UTF-8, pemisah koma",
                        }
                    },
                }
            }
        },
    }
}


def _user_id(user: CurrentUser) -> uuid.UUID:
    return uuid.UUID(str(user.user_id))


def _too_large(limit_bytes: int) -> ImportFileError:
    return ImportFileError(
        f"Berkas melebihi {limit_bytes // (1024 * 1024)} MB.", code=IMPORT_FILE_TOO_LARGE
    )


def _reject_oversized(request: Request, limit_bytes: int) -> None:
    """Starlette menampung seluruh body multipart sebelum handler membaca berkas, jadi unggahan
    raksasa ditolak dari Content-Length lebih dulu."""
    try:
        length = int(request.headers.get("content-length", ""))
    except ValueError:
        return
    if length > limit_bytes + _MULTIPART_OVERHEAD:
        raise _too_large(limit_bytes)


def _capped_body(request: Request, limit_bytes: int) -> Request:
    """Tanpa Content-Length (chunked), body dihitung saat diterima dan dihentikan lewat batas."""
    cap = limit_bytes + _MULTIPART_OVERHEAD
    received = 0

    async def receive() -> Message:
        nonlocal received
        message = await request.receive()
        received += len(message.get("body", b""))
        if received > cap:
            raise _too_large(limit_bytes)
        return message

    return Request(request.scope, receive)


@router.get(
    "/case-imports/template",
    response_class=Response,
    summary="Unduh template CSV impor kasus",
    responses={
        200: {
            "content": {"text/csv": {"schema": {"type": "string"}}},
            "description": (
                "Header delapan kolom (title, question, category, split_tag, legal_refs, "
                "must_contain, must_not_contain, expected_conclusion), UTF-8 dengan BOM, "
                "pemisah koma. Kolom legal_refs, must_contain, must_not_contain berisi list "
                'JSON, misalnya ["Pasal 1 KUHPerdata"]; dari Google Sheets tulis apa adanya, '
                "kutip ganda di-escape otomatis saat diekspor."
            ),
        },
        403: {"model": ErrorBody, "description": "Bukan Kontributor atau Admin."},
    },
)
def download_import_template(_: CurrentUser = Depends(_can_write)) -> Response:
    """Template CSV dari sistem. case_code tidak ada karena dibuat sistem."""
    return Response(
        content=template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{TEMPLATE_FILE_NAME}"'},
    )


@router.post(
    "/case-imports",
    response_model=ImportSummaryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Impor kasus dari satu berkas CSV",
    openapi_extra=_UPLOAD_FORM,
    responses={
        403: {"model": ErrorBody, "description": "Bukan Kontributor atau Admin."},
        422: {
            "model": ErrorBody,
            "description": (
                "Seluruh berkas ditolak, tidak ada yang disimpan: tanpa berkas "
                "(`IMPORT_FILE_REQUIRED`), bukan .csv (`IMPORT_FILE_NOT_CSV`), bukan UTF-8 "
                "(`IMPORT_FILE_NOT_UTF8`), terlalu besar (`IMPORT_FILE_TOO_LARGE`), rusak "
                "(`IMPORT_FILE_UNREADABLE`), header beda dari template "
                "(`IMPORT_HEADER_MISMATCH`, `expected` berisi header yang benar), tanpa baris "
                "kasus (`IMPORT_FILE_EMPTY`), atau lebih dari 500 baris (`IMPORT_TOO_MANY_ROWS`)."
            ),
        },
    },
)
async def import_cases(
    request: Request,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_can_write),
) -> ImportSummaryRead:
    """Baris valid menjadi kasus draft di satu suite sementara, baris gagal dilaporkan dengan
    alasan, baris kembar dilewati. Nomor baris mengikuti spreadsheet."""
    limit = import_service.upload_limit_bytes()
    _reject_oversized(request, limit)
    async with _capped_body(request, limit).form(max_files=1, max_fields=1) as form:
        berkas = form.get("file")
        if berkas is None or isinstance(berkas, str):
            raise ValidationError("Pilih satu berkas CSV untuk diimpor.", code=IMPORT_FILE_REQUIRED)
        data = await berkas.read()
        nama = berkas.filename or ""
    # Query database sinkron dijalankan di threadpool, bukan di event loop.
    return await run_in_threadpool(
        import_service.import_cases, db, file_name=nama, data=data, actor_id=_user_id(user)
    )


@router.get(
    "/case-imports/{batch_id}",
    response_model=ImportSummaryRead,
    summary="Buka lagi ringkasan satu impor CSV",
    responses={
        403: {"model": ErrorBody, "description": "Bukan Kontributor atau Admin."},
        404: {"model": ErrorBody, "description": "Batch tidak ada (`IMPORT_BATCH_NOT_FOUND`)."},
    },
)
def read_import_summary(
    batch_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_can_write),
) -> ImportSummaryRead:
    """Ringkasan yang sama dengan respons impor, dibaca dari tabel impor."""
    return import_service.read_summary(db, batch_id)
