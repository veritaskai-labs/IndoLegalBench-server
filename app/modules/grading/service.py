"""Logika bisnis modul grading: versi rubrik (PBI-14 AC3, AC7, SCRUM-222 PR B).

POST membuat versi berikutnya dan langsung menjadikannya aktif di transaksi yang sama:
D7 dan SCRUM-228 selalu menilai dengan "versi rubrik aktif terbaru", jadi versi terbaru
dan versi aktif selalu sama. Versi lama tetap tersimpan sebagai riwayat. Kembali ke
rubrik lama berarti mengirim ulang isinya sebagai versi baru.

Urutan tulis mengikuti trigger migrasi ac845acfa98d: versi masuk belum aktif, isinya
ditambah, versi aktif lama dinonaktifkan, lalu versi baru diaktifkan dan terkunci.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.auth import service as auth_service
from app.modules.grading import repository, rubric_rules
from app.modules.grading.models import (
    RubricAspect,
    RubricLevel,
    RubricLevelDescriptor,
    RubricVersion,
)
from app.modules.grading.schemas import (
    RubricAspectRead,
    RubricDescriptorRead,
    RubricLevelRead,
    RubricVersionCreate,
    RubricVersionRead,
    RubricVersionSummary,
)
from app.shared.exceptions import ConflictError, NotFoundError, ValidationError

RUBRIC_VERSION_CONFLICT = "RUBRIC_VERSION_CONFLICT"
RUBRIC_VERSION_NOT_FOUND = "RUBRIC_VERSION_NOT_FOUND"


def create_version(
    db: Session, payload: RubricVersionCreate, *, created_by: uuid.UUID
) -> RubricVersionRead:
    """Simpan versi baru beserta isinya dan jadikan versi aktif."""
    masalah = rubric_rules.rubric_problem(payload)
    if masalah is not None:
        raise ValidationError(masalah.message, code=masalah.code, field=masalah.field)
    versi = RubricVersion(
        id=uuid.uuid4(),
        version_no=repository.max_version_no(db) + 1,
        effective_date=payload.effective_date,
        judge_prompt_quality=payload.judge_prompt_quality,
        judge_prompt_format=payload.judge_prompt_format,
        approved_by_name=payload.approved_by_name,
        approved_at=payload.approved_at,
        created_by=created_by,
    )
    aspek, level, deskriptor = _isi(versi.id, payload)
    try:
        repository.add_and_flush(db, [versi])
        repository.add_and_flush(db, [*aspek, *level])
        repository.add_and_flush(db, deskriptor)
        _aktifkan(db, versi)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Isi sudah lolos rubric_rules dan id baru dibuat di sini, jadi yang tersisa hanya
        # UNIQUE version_no atau satu-versi-aktif: POST lain menyimpan versi lebih dulu.
        raise ConflictError(
            "Versi rubrik lain baru saja disimpan. Muat ulang halaman lalu coba lagi.",
            code=RUBRIC_VERSION_CONFLICT,
        ) from exc
    return get_version(db, versi.id)


def list_versions(db: Session) -> list[RubricVersionSummary]:
    baris = repository.list_versions(db)
    nama = auth_service.user_names(db, {versi.created_by for versi, _, _ in baris})
    return [
        _ringkasan(versi, nama, aspect_count=jumlah_aspek, level_count=jumlah_level)
        for versi, jumlah_aspek, jumlah_level in baris
    ]


def get_version(db: Session, version_id: uuid.UUID) -> RubricVersionRead:
    versi = repository.get_version(db, version_id)
    if versi is None:
        raise NotFoundError("Versi rubrik tidak ditemukan.", code=RUBRIC_VERSION_NOT_FOUND)
    aspek = repository.aspects_of(db, versi.id)
    level = repository.levels_of(db, versi.id)
    deskriptor = repository.descriptors_of(db, [lv.id for lv in level])
    nama = auth_service.user_names(db, {versi.created_by})
    ringkasan = _ringkasan(versi, nama, aspect_count=len(aspek), level_count=len(level))
    return RubricVersionRead(
        **ringkasan.model_dump(),
        locked_at=versi.locked_at,
        judge_prompt_quality=versi.judge_prompt_quality,
        judge_prompt_format=versi.judge_prompt_format,
        aspects=[RubricAspectRead.model_validate(a, from_attributes=True) for a in aspek],
        levels=_level_dengan_deskriptor(level, aspek, deskriptor),
    )


def _isi(
    version_id: uuid.UUID, payload: RubricVersionCreate
) -> tuple[list[RubricAspect], list[RubricLevel], list[RubricLevelDescriptor]]:
    """Baris aspek, level, dan deskriptor untuk versi baru, dengan id yang sudah ditentukan."""
    aspek = [
        RubricAspect(
            id=uuid.uuid4(),
            rubric_version_id=version_id,
            name=isian.name,
            grader_path=isian.grader_path,
            weight=isian.weight,
            definition=isian.definition,
            sort_order=urutan,
        )
        for urutan, isian in enumerate(payload.aspects, start=1)
    ]
    # rubric_rules sudah memastikan setiap nama deskriptor merujuk aspek yang ada.
    aspek_per_nama = {a.name.casefold(): a.id for a in aspek}
    level: list[RubricLevel] = []
    deskriptor: list[RubricLevelDescriptor] = []
    for isian in payload.levels:
        baris = RubricLevel(
            id=uuid.uuid4(),
            rubric_version_id=version_id,
            score=isian.score,
            label=isian.label,
            is_passing=isian.is_passing,
        )
        level.append(baris)
        deskriptor.extend(
            RubricLevelDescriptor(
                rubric_level_id=baris.id,
                rubric_aspect_id=aspek_per_nama[d.aspect_name.casefold()],
                descriptor=d.descriptor,
            )
            for d in isian.descriptors
        )
    return aspek, level, deskriptor


def _aktifkan(db: Session, versi: RubricVersion) -> None:
    """Nonaktifkan versi aktif lama lebih dulu: index unik hanya membolehkan satu aktif."""
    lama = repository.active_version_for_update(db)
    if lama is not None:
        lama.is_active = False
        db.flush()
    versi.is_active = True
    # SQLite tidak punya trigger; di PostgreSQL trigger memakai nilai ini apa adanya.
    versi.locked_at = datetime.now(UTC)
    db.flush()


def _ringkasan(
    versi: RubricVersion, nama: dict[uuid.UUID, str], *, aspect_count: int, level_count: int
) -> RubricVersionSummary:
    return RubricVersionSummary(
        id=versi.id,
        version_no=versi.version_no,
        effective_date=versi.effective_date,
        is_active=versi.is_active,
        created_by=versi.created_by,
        created_by_name=nama.get(versi.created_by),
        created_at=versi.created_at,
        approved_by_name=versi.approved_by_name,
        approved_at=versi.approved_at,
        aspect_count=aspect_count,
        level_count=level_count,
    )


def _level_dengan_deskriptor(
    level: list[RubricLevel],
    aspek: list[RubricAspect],
    deskriptor: list[RubricLevelDescriptor],
) -> list[RubricLevelRead]:
    urutan = {a.id: (a.sort_order, a.name) for a in aspek}
    per_level: dict[uuid.UUID, list[RubricLevelDescriptor]] = {lv.id: [] for lv in level}
    for baris in deskriptor:
        per_level[baris.rubric_level_id].append(baris)
    return [
        RubricLevelRead(
            id=lv.id,
            score=lv.score,
            label=lv.label,
            is_passing=lv.is_passing,
            descriptors=[
                RubricDescriptorRead(
                    aspect_id=d.rubric_aspect_id,
                    aspect_name=urutan[d.rubric_aspect_id][1],
                    descriptor=d.descriptor,
                )
                for d in sorted(per_level[lv.id], key=lambda d: urutan[d.rubric_aspect_id][0])
            ],
        )
        for lv in level
    ]
