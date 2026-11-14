"""Progres grading run di latar belakang dan satu run aktif per sesi. SCRUM-78 SCRUM-284.

Tabel baru grading_run_progress menyimpan progres run yang dikerjakan worker, terpisah dari
grading_runs supaya trigger grading_runs_guard (identitas run tidak berubah) tidak perlu
dilonggarkan. Index unik parsial menjaga hanya satu run queued atau running per sesi.

Run lama dari POST /grade selalu selesai (finished atau failed) di transaksi yang sama, jadi
index baru tidak bentrok dengan data yang sudah ada.
"""

import sqlalchemy as sa
from alembic import op

revision = "e5b1c7d93a20"  # pragma: allowlist secret
down_revision = "c9a4e2b71d08"  # pragma: allowlist secret
branch_labels = None
depends_on = None

_ACTIVE = sa.text("status IN ('queued', 'running')")


def upgrade() -> None:
    op.create_table(
        "grading_run_progress",
        sa.Column(
            "grading_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("grading_runs.id"),
            primary_key=True,
        ),
        sa.Column("attempts_total", sa.Integer(), nullable=False),
        sa.Column("attempts_graded", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "attempts_graded BETWEEN 0 AND attempts_total",
            name="ck_grading_run_progress_count",
        ),
    )
    op.create_index(
        "uq_grading_runs_one_active",
        "grading_runs",
        ["session_id"],
        unique=True,
        postgresql_where=_ACTIVE,
        sqlite_where=_ACTIVE,
    )


def downgrade() -> None:
    op.drop_index("uq_grading_runs_one_active", table_name="grading_runs")
    op.drop_table("grading_run_progress")
