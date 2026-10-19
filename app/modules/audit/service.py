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
from app.modules.cases import service as cases_service
from app.modules.providers import service as providers_service
from app.modules.reviews import service as review_service
from app.modules.suites import service as suites_service
from app.shared import request_context
from app.shared.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.shared.pagination import Page
from app.shared.security import Role

logger = logging.getLogger(__name__)

# D6a: action = <entitas>.<kata kerja lampau, snake_case>.
_FORMAT_ACTION = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*$")

# D6a: event yang alasannya wajib diisi.
_WAJIB_ALASAN = frozenset({"review.reviewer_replaced"})

# PBI-6 AC3: event yang membawa verdict reviewer di kolom after.
_AKSI_VERDICT = frozenset({"review.verdict_submitted", "review.approval_carried"})

# F-06: catatan yang objeknya diberi label kode kasus lewat case_id. Versi
# kasus dan penugasan review tidak punya nama sendiri yang dikenal pembaca.
_LABEL_KODE_KASUS = frozenset(
    {AuditEntityType.CASE, AuditEntityType.CASE_VERSION, AuditEntityType.REVIEW}
)

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
        items=_tampilkan(db, rows, viewer_id=viewer_id, viewer_role=viewer_role),
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
    rows = repository.search(db, criteria, offset=0, limit=EXPORT_LIMIT)
    return _tampilkan(db, rows, viewer_id=viewer_id, viewer_role=viewer_role)


def get_log(db: Session, log_id: int, *, viewer_id: uuid.UUID, viewer_role: str) -> AuditLogRead:
    """AC4: satu catatan dibuka kembali sebagai bukti."""
    scope = _case_scope(db, viewer_id=viewer_id, viewer_role=viewer_role)
    row = repository.get_by_id(db, log_id)
    if row is None:
        raise NotFoundError("Catatan audit tidak ditemukan")
    if scope is not None and row.case_id not in scope:
        raise ForbiddenError("Catatan ini bukan milik kasus yang ditugaskan kepada Anda")
    return _tampilkan(db, [row], viewer_id=viewer_id, viewer_role=viewer_role)[0]


def _criteria(
    db: Session, filters: AuditLogFilter, *, viewer_id: uuid.UUID, viewer_role: str
) -> SearchCriteria | None:
    """Saringan siap pakai, atau None kalau sudah pasti tidak ada hasil."""
    scope = _case_scope(db, viewer_id=viewer_id, viewer_role=viewer_role)
    if scope is not None and filters.case_id is not None and filters.case_id not in scope:
        raise ForbiddenError("Kasus ini tidak ditugaskan kepada Anda")
    actor_ids = _actor_ids(db, filters)
    case_ids = _case_ids(db, filters)
    if actor_ids == () or case_ids == ():
        return None
    return SearchCriteria(
        occurred_from=filters.occurred_from,
        occurred_to=filters.occurred_to,
        entity_type=filters.entity_type.value if filters.entity_type else None,
        actor_ids=actor_ids,
        case_id=filters.case_id,
        case_ids=case_ids,
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


def _case_ids(db: Session, filters: AuditLogFilter) -> tuple[uuid.UUID, ...] | None:
    """Kasus yang kodenya cocok (F-06). Tuple kosong berarti tidak ada yang cocok.

    Cakupan Reviewer tetap disaring lewat case_scope, jadi kode kasus yang
    tidak ditugaskan cukup tidak menghasilkan apa-apa, bukan 403.
    """
    if filters.case_code is None:
        return None
    return tuple(cases_service.find_case_ids(db, filters.case_code))


def _case_scope(
    db: Session, *, viewer_id: uuid.UUID, viewer_role: str
) -> frozenset[uuid.UUID] | None:
    """None berarti tanpa batas (Admin). Reviewer hanya kasus yang pernah ditugaskan (AC5)."""
    if viewer_role == Role.ADMIN:
        return None
    if viewer_role == Role.REVIEWER:
        return review_service.assigned_case_ids(db, viewer_id)
    raise ForbiddenError("Peran Anda tidak berwenang melihat catatan audit")


def _verdict_tersembunyi(
    db: Session, rows: list[AuditLog], *, viewer_id: uuid.UUID, viewer_role: str
) -> frozenset[int]:
    """Id catatan verdict yang isinya belum boleh dilihat viewer (PBI-6 AC3)."""
    verdicts = [row for row in rows if row.action in _AKSI_VERDICT]
    hidden = review_service.hidden_verdict_assignments(
        db, {row.entity_id for row in verdicts}, viewer_id=viewer_id, viewer_role=Role(viewer_role)
    )
    return frozenset(row.id for row in verdicts if row.entity_id in hidden)


# Jenis dan id objek yang namanya dicari, None kalau catatan tidak punya objek bernama.
_KunciLabel = tuple[AuditEntityType, uuid.UUID] | None


def _kunci_label(row: AuditLog) -> _KunciLabel:
    """Objek mana yang namanya dicari untuk satu catatan, atau None kalau tidak ada."""
    if row.entity_type in _LABEL_KODE_KASUS:
        return None if row.case_id is None else (AuditEntityType.CASE, row.case_id)
    # record() hanya menerima jenis dari katalog, jadi nilainya selalu dikenal.
    return AuditEntityType(row.entity_type), row.entity_id


def _ids(kunci: list[_KunciLabel], jenis: AuditEntityType) -> set[uuid.UUID]:
    return {k[1] for k in kunci if k is not None and k[0] == jenis}


def _label_objek(
    db: Session, kunci: list[_KunciLabel], user_names: dict[uuid.UUID, str]
) -> dict[AuditEntityType, dict[uuid.UUID, str]]:
    """Nama objek per jenis, satu query per jenis untuk satu halaman (F-06)."""
    return {
        AuditEntityType.CASE: cases_service.case_codes(db, _ids(kunci, AuditEntityType.CASE)),
        AuditEntityType.SUITE: suites_service.suite_names(db, _ids(kunci, AuditEntityType.SUITE)),
        AuditEntityType.AI_PRODUCT: providers_service.product_names(
            db, _ids(kunci, AuditEntityType.AI_PRODUCT)
        ),
        AuditEntityType.USER: user_names,
    }


def _tampilkan(
    db: Session, rows: list[AuditLog], *, viewer_id: uuid.UUID, viewer_role: str
) -> list[AuditLogRead]:
    kunci = [_kunci_label(row) for row in rows]
    # Nama pelaku dan nama objek pengguna diambil dalam satu query.
    names = auth_service.user_names(
        db,
        {row.actor_user_id for row in rows if row.actor_user_id is not None}
        | _ids(kunci, AuditEntityType.USER),
    )
    label = _label_objek(db, kunci, names)
    hidden = _verdict_tersembunyi(db, rows, viewer_id=viewer_id, viewer_role=viewer_role)
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
            entity_label=None if k is None else label[k[0]].get(k[1]),
            case_id=row.case_id,
            before=row.before,
            # Baris tetap tampil; hanya isi verdict yang disamarkan.
            after=None if row.id in hidden else row.after,
            reason=row.reason,
            request_id=row.request_id,
        )
        for row, k in zip(rows, kunci, strict=True)
    ]
