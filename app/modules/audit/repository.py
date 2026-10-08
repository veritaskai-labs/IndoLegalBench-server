"""Akses database modul audit.

ATURAN: hanya audit/service.py yang boleh memanggil file ini. Modul lain
tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis. Tabel audit_logs
append-only, jadi di sini tidak ada fungsi ubah atau hapus.
"""

from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog


def add(db: Session, row: AuditLog) -> AuditLog:
    """Sengaja tanpa commit. Baris ikut tersimpan saat perubahannya di-commit."""
    db.add(row)
    return row
