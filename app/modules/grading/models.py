"""Tabel database milik modul grading.

ATURAN: file ini hanya boleh diimpor dari dalam app/modules/grading/.

PBI-14 SCRUM-78 (SCRUM-222), D6, dengan keputusan PO 6 Nov 2026:
- Rubrik berversi. Dimensi rubrik disimpan sebagai rubric_aspects dengan bobot boleh
  kosong (kalau terisi wajib total 100), level 0–5 sebagai rubric_levels dengan penanda
  lulus, dan deskriptor per level per dimensi di rubric_level_descriptors. Bentuk ini
  menampung rubrik holistik klien tanpa bobot dan tetap membuka penilaian per dimensi.
- Penyetuju berupa teks nama dan tanggal, bukan akun aplikasi.

Tabel rubrik dibuat migrasi ac845acfa98d, yang juga memasang trigger PostgreSQL: isi
versi tidak bisa diubah atau dihapus, versi terkunci (locked_at) sejak pertama kali
aktif, dan isi rubrik hanya bisa di-INSERT ke versi yang belum terkunci.

grading_runs, grading_results, reason_codes, dan grading_result_reasons dibuat migrasi
8ead8669ff79; llm_judge_results dan llm_criterion_results dibuat migrasi 57f872b138f0. Run mengunci versi rubrik yang dipakai; hasil hanya di-INSERT; tidak ada
kolom skor gabungan (AC1). Aturan perpindahan status run ada di trigger migrasi itu.
Model di sini harus sama dengan kedua migrasi.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
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
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.text import sql_not_blank

# JSONB on PostgreSQL, plain JSON on SQLite so the tests keep running.
_JSONB = JSON().with_variant(JSONB(), "postgresql")
_VERSION_FK = "rubric_versions.id"
_VERSION_COLUMN = "rubric_version_id"
# Partial unique index: satu baris bertanda per kelompok. Sama untuk PostgreSQL dan SQLite.
_ONLY_ACTIVE = text("is_active")
_ONLY_LATEST = text("is_latest")


class GraderPath(StrEnum):
    """Jalur penilaian satu dimensi rubrik (D6): deterministik atau LLM judge."""

    DETERMINISTIC = "deterministic"
    LLM = "llm"


class GradingRunKind(StrEnum):
    """Nilai pertama atau Nilai ulang (AC7). Nilai ulang tidak menghapus run lama."""

    INITIAL = "initial"
    REGRADE = "regrade"


class GradingRunStatus(StrEnum):
    """queued -> running -> finished/failed; queued -> failed kalau run tidak pernah mulai."""

    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"


class DetFormula(StrEnum):
    """Rumus skor deterministik #107. Disimpan sebagai teks dengan CHECK (catatan SA)."""

    RECALL = "recall"
    PRECISION = "precision"


class JudgeKind(StrEnum):
    """Jenis penilaian LLM per pengulangan (#108). Tidak ada jenis jebakan (AC4)."""

    QUALITY = "quality"
    FORMAT = "format"


class JudgeStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _enum_values(members) -> list[str]:
    return [member.value for member in members]


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=_enum_values)


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
            postgresql_where=_ONLY_ACTIVE,
            sqlite_where=_ONLY_ACTIVE,
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
        UniqueConstraint(_VERSION_COLUMN, "name", name="uq_rubric_aspects_version_name"),
        UniqueConstraint(_VERSION_COLUMN, "sort_order", name="uq_rubric_aspects_version_order"),
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
        _enum(GraderPath, "grader_path_enum"), nullable=False
    )
    weight: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class RubricLevel(Base):
    __tablename__ = "rubric_levels"
    __table_args__ = (
        UniqueConstraint(_VERSION_COLUMN, "score", name="uq_rubric_levels_version_score"),
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


_DET_COLUMNS = (
    "det_score",
    "det_regulation_matches",
    "det_article_matches",
    "det_ungradable_refs",
    "det_all_chained_cited",
    "det_reference_total",
    "det_reference_correct",
    "det_extracted_total",
    "det_extracted_correct",
    "det_formula",
)
# det_score dan det_all_chained_cited boleh kosong walau dinilai (acuan tak bisa dinilai).
_DET_REQUIRED = tuple(
    kolom for kolom in _DET_COLUMNS if kolom not in {"det_score", "det_all_chained_cited"}
)
_DET_COMPONENTS = (
    "(is_technical_disruption AND "
    + " AND ".join(f"{kolom} IS NULL" for kolom in _DET_COLUMNS)
    + ") OR (NOT is_technical_disruption AND "
    + " AND ".join(f"{kolom} IS NOT NULL" for kolom in _DET_REQUIRED)
    + ")"
)


_FORMULA_VALUES = ", ".join(f"'{formula.value}'" for formula in DetFormula)


class GradingRun(Base):
    __tablename__ = "grading_runs"
    __table_args__ = (
        CheckConstraint(
            "(status IN ('finished', 'failed')) = (finished_at IS NOT NULL)",
            name="ck_grading_runs_finished_at",
        ),
        Index(
            "uq_grading_runs_one_latest",
            "session_id",
            unique=True,
            postgresql_where=_ONLY_LATEST,
            sqlite_where=_ONLY_LATEST,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("measurement_sessions.id"), nullable=False, index=True
    )
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_VERSION_FK), nullable=False
    )
    judge_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_products.id", name="fk_grading_runs_judge_provider", ondelete="RESTRICT"),
        nullable=True,
    )
    kind: Mapped[GradingRunKind] = mapped_column(
        _enum(GradingRunKind, "grading_run_kind_enum"), nullable=False
    )
    status: Mapped[GradingRunStatus] = mapped_column(
        _enum(GradingRunStatus, "grading_run_status_enum"),
        nullable=False,
        default=GradingRunStatus.QUEUED,
        server_default=GradingRunStatus.QUEUED.value,
    )
    is_latest: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    triggered_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GradingResult(Base):
    """Komponen deterministik satu percobaan dalam satu run (#107 CitationScore, AC2)."""

    __tablename__ = "grading_results"
    __table_args__ = (
        UniqueConstraint("grading_run_id", "attempt_id", name="uq_grading_results_run_attempt"),
        CheckConstraint(
            "det_score IS NULL OR det_score BETWEEN 0 AND 1", name="ck_grading_results_det_score"
        ),
        CheckConstraint(
            f"det_formula IS NULL OR det_formula IN ({_FORMULA_VALUES})",
            name="ck_grading_results_det_formula",
        ),
        CheckConstraint(
            "det_reference_correct BETWEEN 0 AND det_reference_total "
            "AND det_extracted_correct BETWEEN 0 AND det_extracted_total",
            name="ck_grading_results_det_counts",
        ),
        CheckConstraint(_DET_COMPONENTS, name="ck_grading_results_det_components"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grading_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("grading_runs.id"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("attempts.id"), nullable=False, index=True
    )
    is_technical_disruption: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    det_score: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    det_regulation_matches: Mapped[list | None] = mapped_column(_JSONB, nullable=True)
    det_article_matches: Mapped[list | None] = mapped_column(_JSONB, nullable=True)
    det_ungradable_refs: Mapped[list | None] = mapped_column(_JSONB, nullable=True)
    det_all_chained_cited: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    det_reference_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    det_reference_correct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    det_extracted_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    det_extracted_correct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    det_formula: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReasonCode(Base):
    """Katalog alasan standar per jalur (AC5). is_proposed = usulan tim, bukan daftar klien."""

    __tablename__ = "reason_codes"
    __table_args__ = (CheckConstraint(sql_not_blank("label"), name="ck_reason_codes_label"),)

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    grader_path: Mapped[GraderPath] = mapped_column(
        _enum(GraderPath, "grader_path_enum"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    is_proposed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GradingResultReason(Base):
    __tablename__ = "grading_result_reasons"

    grading_result_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("grading_results.id"), primary_key=True
    )
    reason_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("reason_codes.code"), primary_key=True
    )


_JUDGE_STATUS = (
    "(status = 'succeeded' AND criteria_met IS NOT NULL AND criteria_total IS NOT NULL "
    "AND criteria_score IS NOT NULL AND failure_kind IS NULL AND error IS NULL) OR "
    "(status = 'failed' AND failure_kind IS NOT NULL AND criteria_met IS NULL "
    "AND criteria_total IS NULL AND criteria_score IS NULL AND rubric_level IS NULL)"
)


class LlmJudgeResult(Base):
    """Satu panggilan penilai LLM: satu pengulangan, satu jenis (#108 JudgeRow, K3)."""

    __tablename__ = "llm_judge_results"
    __table_args__ = (
        UniqueConstraint(
            "grading_result_id", "judge_run_no", "kind", name="uq_llm_judge_results_run_kind"
        ),
        # Satu panggilan penilai per pengulangan (klien No. 62); sesi punya 1–5 pengulangan
        # (ck_measurement_sessions_repetitions). Ubah keduanya bersamaan.
        CheckConstraint("judge_run_no BETWEEN 1 AND 5", name="ck_llm_judge_results_judge_run_no"),
        CheckConstraint(sql_not_blank("prompt"), name="ck_llm_judge_results_prompt"),
        CheckConstraint(
            "criteria_met BETWEEN 0 AND criteria_total", name="ck_llm_judge_results_criteria"
        ),
        CheckConstraint(
            "criteria_score IS NULL OR criteria_score BETWEEN 0 AND 1",
            name="ck_llm_judge_results_criteria_score",
        ),
        CheckConstraint(
            "rubric_level IS NULL OR (rubric_level BETWEEN 0 AND 5 AND kind = 'quality')",
            name="ck_llm_judge_results_rubric_level",
        ),
        CheckConstraint(_JUDGE_STATUS, name="ck_llm_judge_results_status"),
        CheckConstraint(
            "coalesce(input_tokens, 0) >= 0 AND coalesce(output_tokens, 0) >= 0",
            name="ck_llm_judge_results_tokens",
        ),
        CheckConstraint("cost_idr IS NULL OR cost_idr >= 0", name="ck_llm_judge_results_cost"),
        CheckConstraint(
            "usd_idr_rate IS NULL OR usd_idr_rate > 0", name="ck_llm_judge_results_rate"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grading_result_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("grading_results.id"), nullable=False
    )
    judge_run_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    kind: Mapped[JudgeKind] = mapped_column(_enum(JudgeKind, "llm_judge_kind_enum"), nullable=False)
    status: Mapped[JudgeStatus] = mapped_column(
        _enum(JudgeStatus, "llm_judge_status_enum"), nullable=False
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    criteria_met: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    criteria_total: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # "Skor LLM" di tabel hasil (keputusan PO K2): proporsi kriteria terpenuhi, dua digit.
    criteria_score: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    # Skor rubrik 0–5, hanya di baris quality; butuh keluaran penilai baru (K1).
    rubric_level: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_idr: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    usd_idr_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    raw_response: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LlmCriterionResult(Base):
    """Satu kriteria jawaban dari snapshot (must_contain, must_not_contain, kesimpulan)."""

    __tablename__ = "llm_criterion_results"
    __table_args__ = (
        UniqueConstraint(
            "llm_judge_result_id", "criterion_key", name="uq_llm_criterion_results_key"
        ),
        CheckConstraint(sql_not_blank("criterion_text"), name="ck_llm_criterion_results_text"),
        CheckConstraint(
            "reason_code IS NULL OR NOT is_met", name="ck_llm_criterion_results_reason_unmet"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    llm_judge_result_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("llm_judge_results.id"), nullable=False
    )
    criterion_key: Mapped[str] = mapped_column(String(64), nullable=False)
    criterion_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_met: Mapped[bool] = mapped_column(Boolean, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("reason_codes.code"), nullable=True
    )
