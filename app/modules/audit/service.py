"""Logika bisnis modul audit.

PBI-18 Jejak audit menyeluruh.

Ini satu-satunya pintu masuk yang boleh dipanggil modul lain. Service
tidak boleh menyentuh HTTP. Kalau aturan bisnis dilanggar, lempar
exception dari app.shared.exceptions.

record() mengikuti diagram D6b: memakai session yang sama dengan
perubahannya dan tidak pernah commit sendiri. Kalau salah satu gagal,
keduanya batal.
"""

import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.modules.audit import redaction, repository
from app.modules.audit.models import AuditEntityType, AuditLog
from app.shared import request_context
from app.shared.exceptions import ValidationError

# D6a: action = <entitas>.<kata kerja lampau, snake_case>.
_FORMAT_ACTION = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*$")

# D6a: event yang alasannya wajib diisi.
_WAJIB_ALASAN = frozenset({"review.reviewer_replaced"})

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
