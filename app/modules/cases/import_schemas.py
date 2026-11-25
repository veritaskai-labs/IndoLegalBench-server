"""Bentuk respons impor CSV. PBI-4 SCRUM-338, AC7, diagram D7 dan D8."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.modules.cases.models import ImportRowStatus


class ImportRowRead(BaseModel):
    """Hasil satu baris. Nomor baris mengikuti spreadsheet (header = baris 1)."""

    row_no: int = Field(ge=2, description="Nomor baris di spreadsheet; header adalah baris 1")
    status: ImportRowStatus
    case_id: uuid.UUID | None = Field(default=None, description="Hanya untuk status created")
    case_code: str | None = Field(default=None, description="Kode buatan sistem, hanya created")
    reason: str | None = Field(default=None, description="Alasan gagal atau dilewati")


class ImportSummaryRead(BaseModel):
    """Ringkasan impor (AC7): berhasil, gagal beserta alasannya, dan dilewati."""

    batch_id: uuid.UUID
    file_name: str
    imported_by: uuid.UUID
    imported_at: datetime
    suite_id: uuid.UUID = Field(description="Suite sementara tempat semua kasus berkas ini")
    suite_name: str
    total_rows: int = Field(description="Baris kasus yang dibaca; baris kosong tidak dihitung")
    success_count: int
    failed_count: int
    skipped_count: int
    rows: list[ImportRowRead]
