"""Add measurement_sessions.

Revision ID: 16014b26f0bf
Revises: a1f8c7e3d092
Create Date: 2026-10-31 21:00:00.000000

PBI-11 SCRUM-76 (SCRUM-205), D2:
- Tabel measurement_sessions: satu sesi = satu produk AI.
- repetitions 1 sampai 5 (CHECK), berlaku untuk Resmi maupun Uji Coba.
- label wajib dan tidak boleh hanya spasi (CHECK).
- Status dasar queued, running, finished, finished_with_failures, failed.
  Dihentikan dan Dibatalkan ditambah PBI-12.
- Trigger PBI-23 (a1f8c7e3d092) mulai melihat riwayat lewat product_id.
- Sesi tidak bisa dihapus atau di-TRUNCATE, hanya diarsipkan (AC10). Arsip dan
  "terima apa adanya" wajib lengkap dengan waktu dan pelakunya (CHECK).
- Hanya satu sesi otomatis yang berstatus running (partial UNIQUE index).
- measurement_session_suites: satu sesi bisa banyak suite (AC2).
- session_product_copies: satu salinan produk per sesi, tanpa kredensial (AC6).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "16014b26f0bf"  # pragma: allowlist secret
down_revision: str | None = "a1f8c7e3d092"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

purpose_enum = postgresql.ENUM(
    "official", "trial", name="measurement_purpose_enum", create_type=False
)
session_type_enum = postgresql.ENUM(
    "automatic", "manual", name="measurement_session_type_enum", create_type=False
)
split_filter_enum = postgresql.ENUM(
    "dev", "test", "both", name="measurement_split_filter_enum", create_type=False
)
status_enum = postgresql.ENUM(
    "queued",
    "running",
    "finished",
    "finished_with_failures",
    "failed",
    name="measurement_session_status_enum",
    create_type=False,
)

_ENUMS = (purpose_enum, session_type_enum, split_filter_enum, status_enum)

# Milik ai_products (PBI-10, PBI-23). Dipakai ulang, tidak dibuat atau dihapus di sini.
product_measurement_type_enum = postgresql.ENUM(
    name="ai_product_measurement_type_enum", create_type=False
)
product_provider_type_enum = postgresql.ENUM(
    name="ai_product_provider_type_enum", create_type=False
)

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        for enum in _ENUMS:
            enum.create(bind, checkfirst=True)

    def _enum(enum: postgresql.ENUM) -> sa.types.TypeEngine:
        return enum if is_postgres else sa.String(32)

    op.create_table(
        "measurement_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("purpose", _enum(purpose_enum), nullable=False),
        sa.Column("session_type", _enum(session_type_enum), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("ai_products.id"), nullable=False),
        sa.Column("repetitions", sa.SmallInteger(), nullable=False),
        sa.Column("split_filter", _enum(split_filter_enum), nullable=False),
        sa.Column("status", _enum(status_enum), server_default="queued", nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("accepted_as_is_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_as_is_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("accepted_as_is_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "repetitions BETWEEN 1 AND 5", name="ck_measurement_sessions_repetitions"
        ),
        sa.CheckConstraint(
            "length(trim(label)) > 0", name="ck_measurement_sessions_label_not_blank"
        ),
        sa.CheckConstraint(
            "(archived_at IS NULL) = (archived_by IS NULL)",
            name="ck_measurement_sessions_archived_complete",
        ),
        sa.CheckConstraint(
            "(accepted_as_is_at IS NULL AND accepted_as_is_by IS NULL "
            "AND accepted_as_is_reason IS NULL) OR "
            "(accepted_as_is_at IS NOT NULL AND accepted_as_is_by IS NOT NULL "
            # coalesce: tanpanya alasan NULL membuat CHECK bernilai NULL, dan NULL lolos.
            "AND length(trim(coalesce(accepted_as_is_reason, ''))) > 0)",
            name="ck_measurement_sessions_accepted_as_is_complete",
        ),
    )
    op.create_index("ix_measurement_sessions_product_id", "measurement_sessions", ["product_id"])
    # Satu sesi otomatis berjalan. Sesi otomatis lain menunggu, sesi manual tidak dibatasi.
    op.create_index(
        "uq_measurement_sessions_one_running_automatic",
        "measurement_sessions",
        ["session_type"],
        unique=True,
        postgresql_where=sa.text("status = 'running' AND session_type = 'automatic'"),
    )

    if is_postgres:
        # Sesi tidak pernah dihapus, hanya diarsipkan (AC10). TRUNCATE tidak memicu
        # trigger baris, jadi punya trigger statement sendiri, seperti audit_logs.
        op.execute(
            """
            CREATE FUNCTION measurement_sessions_forbid_delete() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% cannot be deleted, archive it instead', TG_TABLE_NAME
                    USING ERRCODE = 'insufficient_privilege';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER measurement_sessions_block_delete
            BEFORE DELETE ON measurement_sessions
            FOR EACH ROW EXECUTE FUNCTION measurement_sessions_forbid_delete()
            """
        )
        op.execute(
            """
            CREATE TRIGGER measurement_sessions_block_truncate
            BEFORE TRUNCATE ON measurement_sessions
            FOR EACH STATEMENT EXECUTE FUNCTION measurement_sessions_forbid_delete()
            """
        )

    # AC2: kasus dipilih per suite. Tanpa CASCADE, suite yang pernah diukur tidak
    # bisa dihapus permanen (suite memakai arsip, bukan hapus).
    op.create_table(
        "measurement_session_suites",
        sa.Column(
            "session_id", sa.Uuid(), sa.ForeignKey("measurement_sessions.id"), primary_key=True
        ),
        sa.Column("suite_id", sa.Uuid(), sa.ForeignKey("suites.id"), primary_key=True),
    )
    op.create_index(
        "ix_measurement_session_suites_suite_id", "measurement_session_suites", ["suite_id"]
    )

    # AC6: data produk saat sesi dibuat. Kredensial sengaja tidak disalin.
    op.create_table(
        "session_product_copies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("measurement_sessions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("ai_products.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("measurement_type", _enum(product_measurement_type_enum), nullable=False),
        sa.Column("provider_type", _enum(product_provider_type_enum), nullable=True),
        sa.Column("base_url", sa.String(length=2048), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("model_params", _JSONB, nullable=True),
        sa.Column("model_version_at_start", sa.String(length=200), nullable=True),
        sa.Column("price_input_usd_per_mtok", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("price_output_usd_per_mtok", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("monthly_budget_idr", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column(
            "copied_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("session_product_copies")
    op.drop_index("ix_measurement_session_suites_suite_id", table_name="measurement_session_suites")
    op.drop_table("measurement_session_suites")
    op.drop_index(
        "uq_measurement_sessions_one_running_automatic", table_name="measurement_sessions"
    )
    op.drop_index("ix_measurement_sessions_product_id", table_name="measurement_sessions")
    op.drop_table("measurement_sessions")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS measurement_sessions_forbid_delete()")
        for enum in reversed(_ENUMS):
            enum.drop(bind, checkfirst=True)
