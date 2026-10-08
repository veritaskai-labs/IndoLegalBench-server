"""Akses database modul audit.

ATURAN: hanya audit/service.py yang boleh memanggil file ini. Modul lain
tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis. Tabel audit_logs
append-only: tidak ada fungsi ubah, dan satu-satunya penghapusan adalah
retensi, yang hanya lolos trigger lewat role ilb_retention.
"""

from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog


def add(db: Session, row: AuditLog) -> AuditLog:
    """Sengaja tanpa commit. Baris ikut tersimpan saat perubahannya di-commit."""
    db.add(row)
    return row


def delete_older_than(db: Session, cutoff: datetime) -> int:
    """Hapus catatan dengan occurred_at sebelum cutoff. Tanpa commit."""
    result = db.execute(delete(AuditLog).where(AuditLog.occurred_at < cutoff))
    return result.rowcount
