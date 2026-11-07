"""Endpoint sesi pengukuran manual. PBI-22 (SCRUM-176), SCRUM-230.

Prefix sendiri (/manual-measurements) sesuai tiket: sesi manual tidak lewat ringkasan
biaya, anggaran, maupun antrean sesi otomatis (AC10).
"""

import uuid
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.runs.dependencies import (
    get_manual_evidence_service,
    get_manual_session_service,
    get_manual_status_service,
)
from app.modules.runs.manual_answers import (
    manual_answer_history,
    save_manual_answer,
)
from app.modules.runs.manual_evidence import ManualEvidenceService
from app.modules.runs.manual_session import ManualSessionService
from app.modules.runs.manual_session_status import ManualSessionStatusService
from app.modules.runs.schemas import (
    EvidenceRead,
    ManualAnswerHistoryEntry,
    ManualAnswerResponse,
    ManualAnswerSaveRequest,
    ManualFinalizeResponse,
    ManualSessionDetail,
    ManualStartRequest,
    ManualStartResponse,
)
from app.shared.config import Settings, get_settings
from app.shared.database import get_db
from app.shared.exceptions import ValidationError
from app.shared.security import CurrentUser, Role, require_roles

router = APIRouter(prefix="/manual-measurements", tags=["manual-measurements"])

# AC1: Admin membuat sesi manual. AC11: semua Admin dan Kontributor melihat sesi orang lain.
_admin = require_roles(Role.ADMIN)
_viewers = require_roles(Role.ADMIN, Role.CONTRIBUTOR)


@router.post(
    "",
    response_model=ManualStartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Buat sesi manual: snapshot kasus saat itu juga, status Belum terisi",
    responses={
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
                "Isian tidak valid, produk otomatis atau nonaktif (`PRODUCT_NOT_MANUAL`, "
                "`PRODUCT_INACTIVE`), atau tidak ada kasus layak (`NO_ELIGIBLE_CASES`)."
            ),
        },
    },
)
def create_manual_session(
    payload: ManualStartRequest,
    user: CurrentUser = Depends(_admin),
    service: ManualSessionService = Depends(get_manual_session_service),
) -> ManualStartResponse:
    return service.create(payload, created_by=uuid.UUID(str(user.user_id)))


@router.post(
    "/{session_id}/finalize",
    response_model=ManualFinalizeResponse,
    summary="Tandai sesi manual selesai: final, tidak dapat dibuka kembali",
    responses={
        404: {
            "model": ErrorBody,
            "description": "Sesi manual tidak ditemukan (`MANUAL_SESSION_NOT_FOUND`).",
        },
        409: {
            "model": ErrorBody,
            "description": (
                "Masih ada jawaban kosong (`MANUAL_SESSION_INCOMPLETE`) atau sesi sudah "
                "selesai (`MANUAL_SESSION_FINALIZED`)."
            ),
        },
    },
)
def finalize_manual(
    session_id: uuid.UUID,
    # Siapa yang boleh menandai Selesai masih TBA; standar tiket: Admin (AC8).
    user: CurrentUser = Depends(_admin),
    service: ManualSessionStatusService = Depends(get_manual_status_service),
) -> ManualFinalizeResponse:
    return service.finalize(
        session_id, finalized_by=uuid.UUID(str(user.user_id)), now=datetime.now(UTC)
    )


@router.get(
    "/{session_id}",
    response_model=ManualSessionDetail,
    summary="Progres sesi manual: X dari N jawaban terisi dan kartu per kasus",
    responses={
        404: {
            "model": ErrorBody,
            "description": "Sesi manual tidak ditemukan (`MANUAL_SESSION_NOT_FOUND`).",
        },
    },
)
def read_manual_session(
    session_id: uuid.UUID,
    unfilled: bool = Query(
        default=False, description="Filter 'belum terisi': hanya kasus yang masih kosong"
    ),
    _user: CurrentUser = Depends(_viewers),
    service: ManualSessionStatusService = Depends(get_manual_status_service),
    settings: Settings = Depends(get_settings),
) -> ManualSessionDetail:
    return service.detail(
        session_id,
        unfilled_only=unfilled,
        warning_days=settings.manual_answer_date_warning_days,
    )


@router.put(
    "/{session_id}/answers",
    response_model=ManualAnswerResponse,
    summary="Simpan atau timpa jawaban manual",
    responses={
        409: {
            "model": ErrorBody,
            "description": "Konfirmasi overwrite diperlukan atau sesi sudah selesai.",
        },
        422: {
            "model": ErrorBody,
            "description": ("Jawaban atau catatan penolakan kosong, atau percobaan tidak valid."),
        },
    },
)
def put_manual_answer(
    session_id: uuid.UUID,
    payload: ManualAnswerSaveRequest,
    user: CurrentUser = Depends(_viewers),
    db: Session = Depends(get_db),
) -> ManualAnswerResponse:
    return save_manual_answer(
        db,
        session_id,
        payload,
        actor=uuid.UUID(str(user.user_id)),
    )


@router.get(
    "/{session_id}/answers/{snapshot_item_id}/{attempt_no}/history",
    response_model=list[ManualAnswerHistoryEntry],
    summary="Riwayat perubahan jawaban manual",
)
def get_manual_answer_history(
    session_id: uuid.UUID,
    snapshot_item_id: uuid.UUID,
    attempt_no: int,
    _user: CurrentUser = Depends(_viewers),
    db: Session = Depends(get_db),
) -> list[ManualAnswerHistoryEntry]:
    return manual_answer_history(
        db,
        session_id,
        snapshot_item_id,
        attempt_no,
    )


# SCRUM-233 (AC7): bukti jawaban manual, D9 attempt_evidence. Siapa yang boleh melihat
# bukti dicek service dari flag MANUAL_EVIDENCE_VISIBILITY; route hanya menjaga peran D10.
_EVIDENCE = "/{session_id}/answers/{snapshot_item_id}/{attempt_no}/evidence"
_ONE_EVIDENCE = "/{session_id}/evidence/{evidence_id}"
_EVIDENCE_404 = {
    "model": ErrorBody,
    "description": (
        "Sesi, percobaan, atau bukti tidak ditemukan, atau peran Anda tidak boleh melihat "
        "bukti (`MANUAL_SESSION_NOT_FOUND`, `MANUAL_ATTEMPT_NOT_FOUND`, `EVIDENCE_NOT_FOUND`)."
    ),
}
_EVIDENCE_409 = {
    "model": ErrorBody,
    "description": "Sesi sudah Selesai (`MANUAL_SESSION_FINALIZED`) atau sudah 3 bukti (`EVIDENCE_LIMIT_REACHED`).",
}


@router.post(
    _EVIDENCE,
    response_model=EvidenceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Tambah bukti jawaban manual: satu berkas PNG/JPG/PDF atau satu tautan",
    responses={
        404: _EVIDENCE_404,
        409: _EVIDENCE_409,
        422: {
            "model": ErrorBody,
            "description": (
                "Bukan tepat satu berkas atau tautan (`EVIDENCE_KIND_REQUIRED`), isi berkas bukan "
                "PNG/JPG/PDF (`EVIDENCE_TYPE_NOT_ALLOWED`, `EVIDENCE_TYPE_MISMATCH`), kosong atau "
                "lewat batas (`EVIDENCE_EMPTY`, `EVIDENCE_TOO_LARGE`), atau tautan tidak sah "
                "(`EVIDENCE_LINK_INVALID`)."
            ),
        },
    },
)
def add_manual_evidence(
    session_id: uuid.UUID,
    snapshot_item_id: uuid.UUID,
    attempt_no: int,
    file: UploadFile | None = File(default=None, description="Tangkapan layar PNG/JPG atau PDF"),
    url: str | None = Form(default=None, description="Tautan bukti, http atau https"),
    user: CurrentUser = Depends(_viewers),
    service: ManualEvidenceService = Depends(get_manual_evidence_service),
    settings: Settings = Depends(get_settings),
) -> EvidenceRead:
    if (file is None) == (url is None):
        raise ValidationError(
            "Kirim tepat satu bukti: berkas atau tautan.", code="EVIDENCE_KIND_REQUIRED"
        )
    actor = uuid.UUID(str(user.user_id))
    if url is not None:
        return service.add_link(session_id, snapshot_item_id, attempt_no, url=url, actor=actor)
    # Dibaca paling banyak batas + 1 byte: cukup untuk tahu berkasnya kelewat besar.
    content = file.file.read(settings.manual_evidence_max_bytes + 1)
    return service.add_file(
        session_id,
        snapshot_item_id,
        attempt_no,
        content=content,
        declared_type=file.content_type,
        file_name=file.filename,
        actor=actor,
    )


@router.get(
    _EVIDENCE,
    response_model=list[EvidenceRead],
    summary="Daftar bukti satu jawaban manual",
    responses={404: _EVIDENCE_404},
)
def list_manual_evidence(
    session_id: uuid.UUID,
    snapshot_item_id: uuid.UUID,
    attempt_no: int,
    user: CurrentUser = Depends(_viewers),
    service: ManualEvidenceService = Depends(get_manual_evidence_service),
) -> list[EvidenceRead]:
    return service.list(session_id, snapshot_item_id, attempt_no, role=user.role)


@router.get(
    _ONE_EVIDENCE + "/file",
    response_class=Response,
    summary="Unduh berkas bukti jawaban manual",
    responses={
        200: {
            "description": "Isi berkas, selalu sebagai lampiran",
            "content": {"image/png": {}, "image/jpeg": {}, "application/pdf": {}},
        },
        404: _EVIDENCE_404,
    },
)
def download_manual_evidence(
    session_id: uuid.UUID,
    evidence_id: uuid.UUID,
    user: CurrentUser = Depends(_viewers),
    service: ManualEvidenceService = Depends(get_manual_evidence_service),
) -> Response:
    berkas = service.download(session_id, evidence_id, role=user.role)
    return Response(
        content=berkas.content,
        media_type=berkas.mime_type,
        headers={
            # Lampiran, bukan inline: PDF dari platform luar tidak dibuka di origin aplikasi.
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(berkas.file_name)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    _ONE_EVIDENCE,
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Hapus bukti jawaban manual yang salah",
    responses={404: _EVIDENCE_404, 409: _EVIDENCE_409},
)
def delete_manual_evidence(
    session_id: uuid.UUID,
    evidence_id: uuid.UUID,
    user: CurrentUser = Depends(_viewers),
    service: ManualEvidenceService = Depends(get_manual_evidence_service),
) -> Response:
    service.delete(session_id, evidence_id, role=user.role)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
