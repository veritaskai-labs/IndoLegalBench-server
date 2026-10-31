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
        sa.CheckConstraint(
            "repetitions BETWEEN 1 AND 5", name="ck_measurement_sessions_repetitions"
        ),
        sa.CheckConstraint(
            "length(trim(label)) > 0", name="ck_measurement_sessions_label_not_blank"
        ),
    )
    op.create_index("ix_measurement_sessions_product_id", "measurement_sessions", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_measurement_sessions_product_id", table_name="measurement_sessions")
    op.drop_table("measurement_sessions")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum in reversed(_ENUMS):
            enum.drop(bind, checkfirst=True)
