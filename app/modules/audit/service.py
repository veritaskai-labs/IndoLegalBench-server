"""Logika bisnis modul audit.

PBI-18 Jejak audit menyeluruh.

Ini satu-satunya pintu masuk yang boleh dipanggil modul lain. Service
tidak boleh menyentuh HTTP. Kalau aturan bisnis dilanggar, lempar
exception dari app.shared.exceptions.

record() mengikuti diagram D6b: memakai session yang sama dengan
perubahannya dan tidak pernah commit sendiri. Kalau salah satu gagal,
keduanya batal.
"""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.modules.audit import redaction, repository
from app.modules.audit.models import AuditEntityType, AuditLog
from app.modules.audit.repository import SearchCriteria
from app.modules.audit.schemas import AuditLogFilter, AuditLogRead
from app.modules.auth import service as auth_service
from app.shared import request_context
from app.shared.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.shared.pagination import Page
from app.shared.security import Role

logger = logging.getLogger(__name__)

# D6a: action = <entitas>.<kata kerja lampau, snake_case>.
_FORMAT_ACTION = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*$")

# D6a: event yang alasannya wajib diisi.
_WAJIB_ALASAN = frozenset({"review.reviewer_replaced"})

# AC7: masa simpan tetap 90 hari, bukan setelan. Trigger di migration
# c5e7a1d93f40 memakai angka yang sama, dan tes PostgreSQL menjaga keduanya
# tetap cocok. Mengubahnya berarti migration baru, bukan isi .env.
RETENTION_DAYS = 90

# AC6: batas baris satu file ekspor. Lebih dari ini, Admin diminta mempersempit saringan.
EXPORT_LIMIT = 5000

# PBI-10 AC2. Diekspor ulang supaya modul lain cukup memanggil audit.service.
CREDENTIAL_ROTATED = redaction.CREDENTIAL_ROTATED


def record(
    db: Session,
    *,
    action: str,
    entity_type: AuditEntityType | str,
    entity_id: uuid.UUID,
    case_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
) -> AuditLog:
    """Tambahkan satu baris audit ke session yang sedang berjalan.

    Pelaku dan request_id dibaca dari session (lihat
    app.shared.request_context). Tanpa pelaku, kejadian dicatat sebagai
    sistem, misalnya penugasan reviewer otomatis.
    """
    if not _FORMAT_ACTION.fullmatch(action or ""):
        raise ValueError(f"Format action tidak sesuai konvensi D6a: {action!r}")
    jenis = AuditEntityType(entity_type)
    alasan = reason.strip() if reason else None
    if action in _WAJIB_ALASAN and not alasan:
        raise ValidationError("Alasan wajib diisi untuk aksi ini", field="reason")

    aktor = request_context.actor(db)
    row = AuditLog(
        actor_user_id=aktor.user_id if aktor else None,
        actor_role=aktor.role if aktor else None,
        action=action,
        entity_type=jenis.value,
        entity_id=entity_id,
        case_id=case_id,
        before=redaction.without_secrets(before),
        after=redaction.without_secrets(after),
        reason=alasan,
        request_id=request_context.request_id(db),
    )
    return repository.add(db, row)


def purge_expired(db: Session, *, now: datetime | None = None) -> int:
    """AC7: hapus permanen catatan yang lewat masa simpan, lalu catat jumlahnya.

    Hanya berhasil lewat koneksi role ilb_retention. Role lain ditolak
    trigger, dan errornya diteruskan tanpa commit.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=RETENTION_DAYS)
    deleted = repository.delete_older_than(db, cutoff)
    db.commit()
    logger.info(
        "audit retention: %d audit_logs rows deleted, occurred_at before %s",
        deleted,
        cutoff.isoformat(),
    )
    return deleted


def search_logs(
    db: Session, filters: AuditLogFilter, *, viewer_id: uuid.UUID, viewer_role: str
) -> Page[AuditLogRead]:
    """AC3: Admin mencari dan menyaring. AC5: Reviewer otomatis dibatasi."""
    criteria = _criteria(db, filters, viewer_id=viewer_id, viewer_role=viewer_role)
    if criteria is None:
        return Page[AuditLogRead](items=[], total=0, page=filters.page, size=filters.size)
    rows = repository.search(db, criteria, offset=filters.offset, limit=filters.size)
    return Page[AuditLogRead](
        items=_tampilkan(db, rows),
        total=repository.count(db, criteria),
        page=filters.page,
        size=filters.size,
    )


def list_for_export(
    db: Session, filters: AuditLogFilter, *, viewer_id: uuid.UUID, viewer_role: str
) -> list[AuditLogRead]:
    """AC6: seluruh hasil pencarian, tanpa paginasi, dengan hak akses yang sama."""
    criteria = _criteria(db, filters, viewer_id=viewer_id, viewer_role=viewer_role)
    if criteria is None:
        return []
    if repository.count(db, criteria) > EXPORT_LIMIT:
        raise ValidationError(
            f"Hasil lebih dari {EXPORT_LIMIT} catatan. Persempit rentang waktu atau saringan.",
            code="AUDIT_EXPORT_TOO_LARGE",
        )
    return _tampilkan(db, repository.search(db, criteria, offset=0, limit=EXPORT_LIMIT))


def get_log(db: Session, log_id: int, *, viewer_id: uuid.UUID, viewer_role: str) -> AuditLogRead:
    """AC4: satu catatan dibuka kembali sebagai bukti."""
    scope = _case_scope(db, viewer_id=viewer_id, viewer_role=viewer_role)
    row = repository.get_by_id(db, log_id)
    if row is None:
        raise NotFoundError("Catatan audit tidak ditemukan")
    if scope is not None and row.case_id not in scope:
        raise ForbiddenError("Catatan ini bukan milik kasus yang ditugaskan kepada Anda")
    return _tampilkan(db, [row])[0]


def _criteria(
    db: Session, filters: AuditLogFilter, *, viewer_id: uuid.UUID, viewer_role: str
) -> SearchCriteria | None:
    """Saringan siap pakai, atau None kalau sudah pasti tidak ada hasil."""
    scope = _case_scope(db, viewer_id=viewer_id, viewer_role=viewer_role)
    if scope is not None and filters.case_id is not None and filters.case_id not in scope:
        raise ForbiddenError("Kasus ini tidak ditugaskan kepada Anda")
    actor_ids = _actor_ids(db, filters)
    if actor_ids == ():
        return None
    return SearchCriteria(
        occurred_from=filters.occurred_from,
        occurred_to=filters.occurred_to,
        entity_type=filters.entity_type.value if filters.entity_type else None,
        actor_ids=actor_ids,
        case_id=filters.case_id,
        case_scope=scope,
    )


def _actor_ids(db: Session, filters: AuditLogFilter) -> tuple[uuid.UUID, ...] | None:
    """Gabungan saringan id dan nama pelaku. Tuple kosong berarti tidak ada yang cocok."""
    if filters.actor is None:
        return None if filters.actor_id is None else (filters.actor_id,)
    cocok = auth_service.find_user_ids(db, filters.actor)
    if filters.actor_id is not None:
        cocok = [user_id for user_id in cocok if user_id == filters.actor_id]
    return tuple(cocok)


def _case_scope(
    db: Session, *, viewer_id: uuid.UUID, viewer_role: str
) -> frozenset[uuid.UUID] | None:
    """None berarti tanpa batas (Admin). Reviewer hanya kasus yang ditugaskan (AC5)."""
    if viewer_role == Role.ADMIN:
        return None
    if viewer_role == Role.REVIEWER:
        return _assigned_case_ids(db, viewer_id)
    raise ForbiddenError("Peran Anda tidak berwenang melihat catatan audit")


def _assigned_case_ids(db: Session, reviewer_id: uuid.UUID) -> frozenset[uuid.UUID]:
    """Kasus yang pernah ditugaskan kepada reviewer ini.

    TODO(SCRUM-143): tabel review_assignments PBI-6 belum ada, jadi belum
    ada kasus yang ditugaskan. Ganti isi fungsi ini dengan pemanggilan
    review service begitu tabel itu tersedia.
    """
    return frozenset()


def _tampilkan(db: Session, rows: list[AuditLog]) -> list[AuditLogRead]:
    names = auth_service.user_names(
        db, {row.actor_user_id for row in rows if row.actor_user_id is not None}
    )
    return [
        AuditLogRead(
            id=row.id,
            occurred_at=row.occurred_at,
            actor_user_id=row.actor_user_id,
            actor_name=names.get(row.actor_user_id),
            actor_role=row.actor_role,
            action=row.action,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            case_id=row.case_id,
            before=row.before,
            after=row.after,
            reason=row.reason,
            request_id=row.request_id,
        )
        for row in rows
    ]
