"""Bentuk request dan response modul suites.

Schema di sini yang menjadi sumber kontrak OpenAPI. Kalau file ini
berubah, kontrak API ikut berubah, jadi wajib diumumkan ke tim dan
frontend perlu regenerate tipenya.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _nama_bersih(value: str) -> str:
    bersih = value.strip()
    if not bersih:
        raise ValueError("Nama suite tidak boleh kosong")
    return bersih


class SuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="Nama suite, harus unik")
    description: str | None = Field(
        default=None, max_length=1000, description="Tema atau deskripsi singkat suite"
    )

    @field_validator("name")
    @classmethod
    def nama_tidak_kosong(cls, value: str) -> str:
        return _nama_bersih(value)


class SuiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)

    @field_validator("name")
    @classmethod
    def nama_tidak_kosong(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _nama_bersih(value)


class SuiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    status: str
    case_count: int = Field(description="Jumlah kasus di dalam suite ini")
    is_empty: bool = Field(description="True kalau suite belum punya kasus sama sekali")
    exportable: bool = Field(
        description="False kalau suite kosong atau diarsipkan, jadi tidak ikut pengukuran"
    )
    created_at: datetime
    updated_at: datetime
