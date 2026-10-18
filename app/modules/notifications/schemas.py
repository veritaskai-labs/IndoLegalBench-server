"""Bentuk request dan response modul notifications.

Schema di sini yang menjadi sumber kontrak OpenAPI.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    title: str
    body: str
    link: str
    read_at: datetime | None
    created_at: datetime


class UnreadCount(BaseModel):
    count: int = Field(ge=0, description="Jumlah notifikasi belum dibaca milik pengguna saat ini")
