"""Identitas pelaku dan request_id, disimpan di session database.

PBI-18. Pencatat audit perlu tahu siapa pelakunya tanpa setiap service
harus meneruskan pengguna. Sengaja disimpan di Session.info, bukan di
contextvar: dependency FastAPI yang sinkron jalan di thread terpisah,
dan contextvar yang diisi di sana tidak terbawa ke endpoint. Session
dari get_db dipakai bersama dependency dan endpoint dalam satu request.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

_AKTOR = "audit_actor"
_REQUEST_ID = "request_id"


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    role: str


def bind(
    db: Session,
    *,
    user_id: uuid.UUID,
    role: str,
    request_id: uuid.UUID | None = None,
) -> None:
    """Dipanggil penjaga sesi, sekali per request yang terautentikasi."""
    db.info[_AKTOR] = Actor(user_id=user_id, role=str(role))
    db.info[_REQUEST_ID] = request_id


def actor(db: Session) -> Actor | None:
    """None berarti tidak ada pengguna, jadi kejadiannya dicatat sebagai sistem."""
    return db.info.get(_AKTOR)


def request_id(db: Session) -> uuid.UUID | None:
    return db.info.get(_REQUEST_ID)
