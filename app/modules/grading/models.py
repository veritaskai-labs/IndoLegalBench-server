"""Tabel database milik modul grading.

ATURAN: file ini hanya boleh diimpor dari dalam app/modules/grading/.

PBI-14 SCRUM-78 (SCRUM-222), D6, dengan keputusan PO 6 Nov 2026:
- Rubrik berversi. Dimensi rubrik disimpan sebagai rubric_aspects dengan bobot boleh
  kosong (kalau terisi wajib total 100), level 0–5 sebagai rubric_levels dengan penanda
  lulus, dan deskriptor per level per dimensi di rubric_level_descriptors. Bentuk ini
  menampung rubrik holistik klien tanpa bobot dan tetap membuka penilaian per dimensi.
- Penyetuju berupa teks nama dan tanggal, bukan akun aplikasi.

Tabelnya dibuat migrasi ac845acfa98d, yang juga memasang trigger PostgreSQL: isi versi
tidak bisa diubah atau dihapus, versi terkunci (locked_at) sejak pertama kali aktif,
dan isi rubrik hanya bisa di-INSERT ke versi yang belum terkunci. Model di sini harus
sama dengan migrasi itu.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.text import sql_not_blank

_VERSION_FK = "rubric_versions.id"


class GraderPath(StrEnum):
    """Jalur penilaian satu dimensi rubrik (D6): deterministik atau LLM judge."""

    DETERMINISTIC = "deterministic"
    LLM = "llm"


def _enum_values(members) -> list[str]:
    return [member.value for member in members]


class RubricVersion(Base):
    __tablename__ = "rubric_versions"
    __table_args__ = (
        CheckConstraint("version_no >= 1", name="ck_rubric_versions_version_no"),
        CheckConstraint(
            sql_not_blank("judge_prompt_quality"), name="ck_rubric_versions_prompt_quality"
        ),
        CheckConstraint(
            sql_not_blank("judge_prompt_format"), name="ck_rubric_versions_prompt_format"
        ),
        CheckConstraint(
            f"approved_by_name IS NULL OR {sql_not_blank('approved_by_name')}",
            name="ck_rubric_versions_approved_by_name",
        ),
        CheckConstraint(
            "NOT is_active OR locked_at IS NOT NULL", name="ck_rubric_versions_active_locked"
        ),
        Index(
            "uq_rubric_versions_one_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # Diisi trigger saat versi pertama kali aktif; sejak itu isinya tertutup.
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    judge_prompt_quality: Mapped[str] = mapped_column(Text, nullable=False)
    judge_prompt_format: Mapped[str] = mapped_column(Text, nullable=False)
    approved_by_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RubricAspect(Base):
    __tablename__ = "rubric_aspects"
    __table_args__ = (
        UniqueConstraint("rubric_version_id", "name", name="uq_rubric_aspects_version_name"),
        UniqueConstraint("rubric_version_id", "sort_order", name="uq_rubric_aspects_version_order"),
        CheckConstraint(sql_not_blank("name"), name="ck_rubric_aspects_name"),
        CheckConstraint(sql_not_blank("definition"), name="ck_rubric_aspects_definition"),
        CheckConstraint(
            "weight IS NULL OR (weight > 0 AND weight <= 100)", name="ck_rubric_aspects_weight"
        ),
        CheckConstraint("sort_order >= 1", name="ck_rubric_aspects_sort_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_VERSION_FK), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    grader_path: Mapped[GraderPath] = mapped_column(
        Enum(GraderPath, name="grader_path_enum", values_callable=_enum_values), nullable=False
    )
    weight: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class RubricLevel(Base):
    __tablename__ = "rubric_levels"
    __table_args__ = (
        UniqueConstraint("rubric_version_id", "score", name="uq_rubric_levels_version_score"),
        CheckConstraint("score BETWEEN 0 AND 5", name="ck_rubric_levels_score"),
        CheckConstraint(sql_not_blank("label"), name="ck_rubric_levels_label"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_VERSION_FK), nullable=False
    )
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    # Lulus 4–5 menurut jawaban klien No. 48, tapi tetap data supaya rubrik baru bisa beda.
    is_passing: Mapped[bool] = mapped_column(Boolean, nullable=False)


class RubricLevelDescriptor(Base):
    __tablename__ = "rubric_level_descriptors"
    __table_args__ = (
        CheckConstraint(sql_not_blank("descriptor"), name="ck_rubric_level_descriptors_text"),
    )

    rubric_level_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rubric_levels.id"), primary_key=True
    )
    rubric_aspect_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rubric_aspects.id"), primary_key=True
    )
    descriptor: Mapped[str] = mapped_column(Text, nullable=False)
