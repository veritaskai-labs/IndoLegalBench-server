"""Logika bisnis modul suites.

Ini satu-satunya pintu masuk yang boleh dipanggil modul lain.
Service tidak boleh menyentuh HTTP (tidak ada Request, Response, atau
HTTPException di sini). Kalau ada aturan bisnis yang dilanggar, lempar
exception dari app.shared.exceptions.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.cases import service as cases_service
from app.modules.suites import repository
from app.modules.suites.models import Suite, SuiteStatus
from app.modules.suites.schemas import SuiteCreate, SuiteRead, SuiteUpdate
from app.shared.exceptions import ConflictError, NotFoundError
from app.shared.pagination import Page

NAME_TAKEN = "SUITE_NAME_TAKEN"
HAS_APPROVED_CASES = "SUITE_HAS_APPROVED_CASES"


def create_suite(db: Session, payload: SuiteCreate, *, created_by: uuid.UUID) -> SuiteRead:
    """AC2: nama suite yang sudah dipakai tidak bisa dipakai ulang."""
    _pastikan_nama_bebas(db, payload.name)
    suite = Suite(
        name=payload.name,
        description=payload.description,
        status=SuiteStatus.ACTIVE,
        created_by=created_by,
    )
    try:
        tersimpan = repository.create(db, suite)
    except IntegrityError:
        db.rollback()
        raise ConflictError(
            f"Nama suite '{payload.name}' sudah dipakai",
            code=NAME_TAKEN,
        ) from None
    return _tampilkan(db, tersimpan)


def get_suite(db: Session, suite_id: uuid.UUID) -> SuiteRead:
    return _tampilkan(db, _wajib_ada(db, suite_id))


def list_suites(db: Session, *, status: SuiteStatus, page: int, size: int) -> Page[SuiteRead]:
    offset = (page - 1) * size
    baris = repository.list_by_status(db, status, offset=offset, limit=size)
    return Page[SuiteRead](
        items=[_tampilkan(db, item) for item in baris],
        total=repository.count_by_status(db, status),
        page=page,
        size=size,
    )


def update_suite(db: Session, suite_id: uuid.UUID, payload: SuiteUpdate) -> SuiteRead:
    suite = _wajib_ada(db, suite_id)

    if payload.name is not None and payload.name.casefold() != suite.name.casefold():
        _pastikan_nama_bebas(db, payload.name)
        suite.name = payload.name
    elif payload.name is not None:
        suite.name = payload.name

    if payload.description is not None:
        suite.description = payload.description

    try:
        tersimpan = repository.save(db, suite)
    except IntegrityError:
        db.rollback()
        raise ConflictError(
            f"Nama suite '{payload.name}' sudah dipakai",
            code=NAME_TAKEN,
        ) from None
    return _tampilkan(db, tersimpan)


def delete_suite(db: Session, suite_id: uuid.UUID) -> None:
    """AC4: suite berisi kasus approved tidak dihapus. Yang dihapus hanya
    yang masih active, dan penghapusannya soft delete.
    """
    suite = _wajib_ada(db, suite_id)
    if suite.status != SuiteStatus.ACTIVE:
        raise ConflictError(
            "Suite yang diarsipkan tidak dapat dihapus. Aktifkan kembali sebelum menghapus."
        )
    if cases_service.has_approved_case(db, suite.id):
        raise ConflictError(
            "Suite berisi kasus yang sudah disetujui dan tidak dapat dihapus. "
            "Arsipkan suite ini sebagai gantinya.",
            code=HAS_APPROVED_CASES,
        )
    suite.deleted_at = datetime.now(UTC)
    repository.save(db, suite)


def archive_suite(db: Session, suite_id: uuid.UUID) -> SuiteRead:
    """AC4: suite arsip tidak muncul di daftar aktif dan tidak bisa dipilih
    untuk pengukuran baru, tapi seluruh isinya tetap tersimpan.
    """
    suite = _wajib_ada(db, suite_id)
    suite.status = SuiteStatus.ARCHIVED
    return _tampilkan(db, repository.save(db, suite))


def unarchive_suite(db: Session, suite_id: uuid.UUID) -> SuiteRead:
    suite = _wajib_ada(db, suite_id)
    suite.status = SuiteStatus.ACTIVE
    return _tampilkan(db, repository.save(db, suite))


def is_exportable(db: Session, suite_id: uuid.UUID) -> bool:
    """AC5: suite kosong atau arsip tidak ikut ekspor atau pengukuran.

    Dipakai modul lain (runs, reports) lewat service ini, bukan dengan
    mengecek tabel suites sendiri.
    """
    return get_suite(db, suite_id).exportable


def _wajib_ada(db: Session, suite_id: uuid.UUID) -> Suite:
    suite = repository.get_by_id(db, suite_id)
    if suite is None:
        raise NotFoundError("Suite tidak ditemukan")
    return suite


def _pastikan_nama_bebas(db: Session, name: str) -> None:
    if repository.get_by_name(db, name) is not None:
        raise ConflictError(f"Nama suite '{name}' sudah dipakai", code=NAME_TAKEN)


def _tampilkan(db: Session, suite: Suite) -> SuiteRead:
    jumlah = cases_service.count_for_suite(db, suite.id)
    kosong = jumlah == 0
    return SuiteRead(
        id=suite.id,
        name=suite.name,
        description=suite.description,
        status=suite.status,
        case_count=jumlah,
        is_empty=kosong,
        exportable=suite.status == SuiteStatus.ACTIVE and not kosong,
        created_at=suite.created_at,
        updated_at=suite.updated_at,
    )
