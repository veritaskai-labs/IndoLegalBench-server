"""Pencatat audit otomatis lewat event ORM (pola Observer).

PBI-18 AC1. Modul lain tidak perlu ingat memanggil audit: setiap flush
yang membuat atau mengubah baris di tabel katalog D6a otomatis
menghasilkan baris audit_logs di transaksi yang sama.

Dua tahap, karena SQLAlchemy tidak mengizinkan menambah objek ke flush
yang sedang berjalan:

1. after_flush: id baris baru sudah terisi dan riwayat perubahan
   atribut masih ada. Event dikumpulkan di session.info.
2. after_flush_postexec: event ditulis lewat audit.service.record().
   Baris audit ikut di-flush sebelum commit selesai, jadi kalau salah
   satu gagal, keduanya batal (D6b).

Kolom yang dipantau tracker memakai active_history: nilai lama dimuat
dulu sebelum diganti. Tanpa itu, baris yang kedaluwarsa karena commit di
tengah request (mis. nonaktifkan anggota menghapus sesinya dulu) tercatat
dengan before = null.

Penghapusan fisik tidak dicatat di sini. Katalog D6a hanya mengenal
soft delete (deleted_at), dan users tidak pernah dihapus.
"""

import logging
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Mapper, Session

from app.modules.audit import service as audit_service
from app.modules.audit.trackers import AuditEvent, Change, Tracker, tracker_for
from app.shared.database import Base

logger = logging.getLogger(__name__)

_PENDING = "audit_pending_events"


def install() -> None:
    """Pasang listener ke seluruh Session. Aman dipanggil berkali-kali."""
    if not event.contains(Session, "after_flush", _collect):
        event.listen(Session, "after_flush", _collect)
        event.listen(Session, "after_flush_postexec", _write)
    for mapper in Base.registry.mappers:
        _keep_old_values(mapper)
    if not event.contains(Mapper, "mapper_configured", _on_mapper_configured):
        event.listen(Mapper, "mapper_configured", _on_mapper_configured)


def _on_mapper_configured(mapper: Mapper, _class: type) -> None:
    _keep_old_values(mapper)


def _keep_old_values(mapper: Mapper) -> None:
    """Muat nilai lama kolom yang dipantau sebelum diganti (active_history)."""
    tracker = tracker_for(mapper.local_table.name)
    if tracker is None:
        return
    for field in tracker.fields:
        attribute = getattr(mapper.class_, field)
        if not event.contains(attribute, "set", _no_op):
            event.listen(attribute, "set", _no_op, active_history=True)


def _no_op(_target: Any, value: Any, _oldvalue: Any, _initiator: Any) -> Any:
    """Listener kosong. Yang dibutuhkan hanya efek active_history=True."""
    return value


def _collect(session: Session, _flush_context: Any) -> None:
    pending: list[tuple[Tracker, AuditEvent]] = session.info.setdefault(_PENDING, [])
    for row in session.new:
        tracker = _tracker(row)
        if tracker is not None:
            pending.extend((tracker, item) for item in tracker.created(row))
    for row in session.dirty:
        tracker = _tracker(row)
        if tracker is None:
            continue
        changes = _changes(row, tracker.fields)
        if changes:
            pending.extend((tracker, item) for item in tracker.updated(row, changes))


def _write(session: Session, _flush_context: Any) -> None:
    """Tulis event yang terkumpul. Kegagalan sengaja tidak ditelan.

    D6b: tidak ada perubahan tanpa log. Kalau baris audit gagal ditulis,
    perubahannya ikut batal. Errornya dicatat dulu dengan konteks supaya
    500 yang muncul bisa langsung ditelusuri dari log server.
    """
    for tracker, item in session.info.pop(_PENDING, []):
        try:
            audit_service.record(
                session,
                action=item.action,
                entity_type=tracker.entity_type,
                entity_id=item.entity_id,
                case_id=item.case_id,
                before=item.before,
                after=item.after,
            )
        except Exception:
            logger.exception(
                "audit write failed for %s on %s %s, change rolled back",
                item.action,
                tracker.entity_type.value,
                item.entity_id,
            )
            raise


def _tracker(row: Any) -> Tracker | None:
    return tracker_for(inspect(row).mapper.local_table.name)


def _changes(row: Any, fields: tuple[str, ...]) -> dict[str, Change]:
    """Field yang nilainya benar-benar berubah. Simpan ulang nilai yang sama diabaikan."""
    state = inspect(row)
    changes: dict[str, Change] = {}
    for field in fields:
        history = state.attrs[field].history
        if not history.has_changes():
            continue
        old = history.deleted[0] if history.deleted else None
        new = history.added[0] if history.added else None
        if old != new:
            changes[field] = Change(old, new)
    return changes
