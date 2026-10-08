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

Penghapusan fisik tidak dicatat di sini. Katalog D6a hanya mengenal
soft delete (deleted_at), dan users tidak pernah dihapus.
"""

from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.modules.audit import service as audit_service
from app.modules.audit.trackers import AuditEvent, Change, Tracker, tracker_for

_PENDING = "audit_pending_events"


def install() -> None:
    """Pasang listener ke seluruh Session. Aman dipanggil berkali-kali."""
    if not event.contains(Session, "after_flush", _collect):
        event.listen(Session, "after_flush", _collect)
        event.listen(Session, "after_flush_postexec", _write)


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
    for tracker, item in session.info.pop(_PENDING, []):
        audit_service.record(
            session,
            action=item.action,
            entity_type=tracker.entity_type,
            entity_id=item.entity_id,
            case_id=item.case_id,
            before=item.before,
            after=item.after,
        )


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
