"""Berkas CSV skor per sesi yang disimpan selamanya. PBI-16 SCRUM-83 SCRUM-327.

Satu baris satu berkas yang pernah diserahkan lewat GET /reports/sessions/{id}/csv (AC5).
Isinya BYTEA, pola yang sama dengan evidence_files (keputusan SA 7 November 2026: kontainer
API tanpa volume, cadangan hanya PostgreSQL).

AC7: berkas disimpan selamanya. Trigger menolak UPDATE, DELETE, dan TRUNCATE untuk semua
peran, sama seperti tabel laporan beku (b6f3a2d81c47). Isi yang sama untuk sesi yang sama
hanya disimpan sekali (unik session_id + sha256). Jawaban asli sudah dijaga trigger
attempts_protect_answer, jadi tidak diubah di sini.

Kode galat: perubahan berkas = insufficient_privilege (42501).

downgrade() menolak jalan selama tabel sudah berisi berkas (review PR #152 B3), pola yang
sama dengan e216a5b7c901 dan b3c91d7a5e24: rollback migrasi tidak boleh menghapus berkas
yang wajib disimpan selamanya. Basis data baru (siklus CI) tetap bisa di-downgrade.
"""

import sqlalchemy as sa
from alembic import op

from app.shared.text import sql_not_blank

revision = "d3e8a1f5c927"  # pragma: allowlist secret
down_revision = "b6f3a2d81c47"  # pragma: allowlist secret
branch_labels = None
depends_on = None

_TABEL = "session_score_files"


def upgrade() -> None:
    op.create_table(
        _TABEL,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "session_id", sa.Uuid(), sa.ForeignKey("measurement_sessions.id"), nullable=False
        ),
        sa.Column("grading_run_id", sa.Uuid(), sa.ForeignKey("grading_runs.id"), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("session_id", "sha256", name="uq_session_score_files_session_sha256"),
        sa.CheckConstraint("size_bytes > 0", name="ck_session_score_files_size_bytes"),
        sa.CheckConstraint(
            sql_not_blank("file_name"), name="ck_session_score_files_file_name_not_blank"
        ),
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE FUNCTION session_score_files_forbid_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is kept forever once written', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    # Termasuk UPDATE yang tidak mengubah apa pun: trigger baris selalu jalan.
    op.execute(
        f"""
        CREATE TRIGGER {_TABEL}_block_change
        BEFORE UPDATE OR DELETE ON {_TABEL}
        FOR EACH ROW EXECUTE FUNCTION session_score_files_forbid_change()
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_TABEL}_block_truncate
        BEFORE TRUNCATE ON {_TABEL}
        FOR EACH STATEMENT EXECUTE FUNCTION session_score_files_forbid_change()
        """
    )


def downgrade() -> None:
    ada_berkas = op.get_bind().scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {_TABEL})"))
    if ada_berkas:
        raise RuntimeError("Cannot downgrade while score files exist")
    # Trigger ikut terhapus bersama tabelnya; fungsinya dihapus sesudahnya.
    op.drop_table(_TABEL)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS session_score_files_forbid_change()")
