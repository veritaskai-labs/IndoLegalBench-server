"""Bentuk response modul legal_refs (PBI-21, SCRUM-160)."""

from pydantic import BaseModel, ConfigDict


class RegulationTypeRead(BaseModel):
    """Satu jenis peraturan untuk dropdown editor (AC2)."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    label: str
    singkatan: str | None
    is_active: bool
