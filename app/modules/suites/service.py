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

from app.modules.auth import service as auth_service
from app.modules.cases import service as cases_service
from app.modules.cases.schemas import ActorRead
from app.modules.suites import repository
from app.modules.suites.models import Suite, SuiteSnapshot, SuiteSnapshotItem, SuiteStatus
from app.modules.suites.schemas import (
    SnapshotItemRead,
    SnapshotRead,
    SnapshotSummary,
    SuiteCreate,
    SuiteRead,
    SuiteUpdate,
)
from app.shared.exceptions import ConflictError, NotFoundError, ValidationError
from app.shared.pagination import Page

NAME_TAKEN = "SUITE_NAME_TAKEN"
HAS_APPROVED_CASES = "SUITE_HAS_APPROVED_CASES"
NOTHING_TO_SNAPSHOT = "NOTHING_TO_SNAPSHOT"


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

    # JSON null ada di model_fields_set dan mengosongkan. Kunci yang tidak
    # dikirim tidak ada di sana, jadi deskripsi lama tetap.
    if "description" in payload.model_fields_set:
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


def create_snapshot(db: Session, suite_id: uuid.UUID, *, created_by: uuid.UUID) -> SnapshotRead:
    """Freeze every latest approved version in the suite.

    There is no name. A suite with no approved case is rejected. An archived
    suite is allowed when it still has one.
    """
    _wajib_ada(db, suite_id)
    copies = cases_service.approved_copies_for_suite(db, suite_id)
    if not copies:
        raise ValidationError(
            "Suite tidak punya kasus yang sudah disetujui",
            code=NOTHING_TO_SNAPSHOT,
        )
    snapshot = SuiteSnapshot(suite_id=suite_id, created_by=created_by)
    snapshot.items = [
        SuiteSnapshotItem(
            case_id=copy["case_id"],
            case_version_id=copy["case_version_id"],
            body=copy["body"],
        )
        for copy in copies
    ]
    stored = repository.create_snapshot(db, snapshot)
    return _snapshot_read(db, stored)


def list_snapshots(
    db: Session, suite_id: uuid.UUID, *, page: int, size: int
) -> Page[SnapshotSummary]:
    """Paged snapshot list for a suite that exists. Newest first."""
    _wajib_ada(db, suite_id)
    offset = (page - 1) * size
    rows = repository.list_snapshots(db, suite_id, offset=offset, limit=size)
    counts = repository.item_counts(db, [row.id for row in rows])
    names = auth_service.user_names(db, {row.created_by for row in rows})
    return Page[SnapshotSummary](
        items=[
            SnapshotSummary(
                id=row.id,
                created_at=row.created_at,
                author=_author(row.created_by, names),
                case_count=counts.get(row.id, 0),
            )
            for row in rows
        ],
        total=repository.count_snapshots(db, suite_id),
        page=page,
        size=size,
    )


def get_snapshot(db: Session, snapshot_id: uuid.UUID) -> SnapshotRead:
    """The frozen items. The body is the stored copy, not the live case."""
    snapshot = repository.get_snapshot(db, snapshot_id)
    if snapshot is None:
        raise NotFoundError("Snapshot tidak ditemukan")
    return _snapshot_read(db, snapshot)


def is_exportable(db: Session, suite_id: uuid.UUID) -> bool:
    """AC5: suite kosong atau arsip tidak ikut ekspor atau pengukuran.

    Dipakai modul lain (runs, reports) lewat service ini, bukan dengan
    mengecek tabel suites sendiri.
    """
    return get_suite(db, suite_id).exportable


def _author(user_id: uuid.UUID, names: dict[uuid.UUID, str]) -> ActorRead:
    """Id plus display name. A missing user row leaves the name empty."""
    return ActorRead(id=user_id, name=names.get(user_id, ""))


def _snapshot_read(db: Session, snapshot: SuiteSnapshot) -> SnapshotRead:
    """Build the detail from the stored copy. Suite name is not read back."""
    names = auth_service.user_names(db, {snapshot.created_by})
    items = sorted(
        snapshot.items,
        key=lambda item: item.body.get("sections", {}).get("case_code", ""),
    )
    return SnapshotRead(
        id=snapshot.id,
        suite_id=snapshot.suite_id,
        created_at=snapshot.created_at,
        author=_author(snapshot.created_by, names),
        items=[
            SnapshotItemRead(
                case_id=item.case_id, case_version_id=item.case_version_id, body=item.body
            )
            for item in items
        ],
    )


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
