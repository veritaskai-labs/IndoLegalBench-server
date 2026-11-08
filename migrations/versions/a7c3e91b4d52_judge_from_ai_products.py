"""Point the judge FKs of sessions and grading runs at ai_products.

SCRUM-78 SCRUM-271, PBI-14 AC1, AC6, decision of 8 Nov 2026.

The judge is now chosen from the active automatic AI products of the Provider
page, not from the separate judge_providers registry (SCRUM-258), so
measurement_sessions.judge_provider_id and grading_runs.judge_provider_id
reference ai_products(id). The judge_providers table and its admin routes stay,
unused, and nothing is dropped.

Existing values pointed to judge_providers rows, which are not ai_products ids,
so they are set to NULL first and the counts are logged. Started sessions and
grading runs are protected by guard triggers (16014b26f0bf, 8ead8669ff79), so
those triggers are disabled only for that UPDATE inside this migration's
transaction, as 4f844618b782 did. The downgrade does the same in the other
direction: values that point to ai_products are not judge_providers ids.

Revision ID: a7c3e91b4d52
Revises: d8a531f0c231
"""

import logging

import sqlalchemy as sa
from alembic import op

revision = "a7c3e91b4d52"  # pragma: allowlist secret
down_revision = "d8a531f0c231"  # pragma: allowlist secret
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

# (tabel, trigger penjaga, nama FK)
_RUJUKAN = (
    (
        "measurement_sessions",
        "measurement_sessions_guard_update",
        "fk_measurement_sessions_judge_provider",
    ),
    ("grading_runs", "grading_runs_guard", "fk_grading_runs_judge_provider"),
)


def upgrade() -> None:
    _arahkan_ke("ai_products")


def downgrade() -> None:
    _arahkan_ke("judge_providers")


def _arahkan_ke(tabel_tujuan: str) -> None:
    is_postgres = op.get_bind().dialect.name == "postgresql"
    for table, guard, fk in _RUJUKAN:
        jumlah = _kosongkan(table, guard if is_postgres else None)
        log.warning("%d %s judge_provider_id set to NULL (now points to %s)", jumlah, table, tabel_tujuan)
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(fk, type_="foreignkey")
            batch.create_foreign_key(fk, tabel_tujuan, ["judge_provider_id"], ["id"], ondelete="RESTRICT")


def _kosongkan(table: str, guard: str | None) -> int:
    """NULL untuk semua judge_provider_id; trigger penjaga dimatikan sebentar.

    ALTER TABLE ... DISABLE TRIGGER ikut transaksi migrasi di PostgreSQL, jadi kalau
    migrasi gagal trigger tetap hidup.
    """
    if guard is not None:
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {guard}")
    jumlah = (
        op.get_bind()
        .execute(
            sa.text(
                f"UPDATE {table} SET judge_provider_id = NULL WHERE judge_provider_id IS NOT NULL"
            )
        )
        .rowcount
    )
    if guard is not None:
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {guard}")
    return jumlah
