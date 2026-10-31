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
- case_snapshots dan case_snapshot_items: salinan kasus per sesi yang hanya bisa
  di-INSERT. UPDATE, DELETE, dan TRUNCATE ditolak trigger untuk semua peran (AC6).
- attempts: satu per item snapshot per nomor pengulangan, nomor tidak melebihi
  pengulangan sesi (D2), jawaban mentah ditulis sekali dan tidak bisa dihapus
  (AC3, AC8).
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

attempt_source_enum = postgresql.ENUM(
    "automatic", "manual", name="attempt_source_enum", create_type=False
)
attempt_status_enum = postgresql.ENUM(
    "pending",
    "running",
    "succeeded",
    "technical_failure",
    name="attempt_status_enum",
    create_type=False,
)

_ENUMS = (
    purpose_enum,
    session_type_enum,
    split_filter_enum,
    status_enum,
    attempt_source_enum,
    attempt_status_enum,
)

# Milik ai_products (PBI-10, PBI-23). Dipakai ulang, tidak dibuat atau dihapus di sini.
product_measurement_type_enum = postgresql.ENUM(
    name="ai_product_measurement_type_enum", create_type=False
)
product_provider_type_enum = postgresql.ENUM(
    name="ai_product_provider_type_enum", create_type=False
)
# Milik case_versions (PBI-9). Dipakai ulang.
case_split_tag_enum = postgresql.ENUM(name="case_split_tag_enum", create_type=False)

_SNAPSHOT_TABLES = ("case_snapshots", "case_snapshot_items")

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
        # Urutan antrean FIFO (D2). Diisi saat sesi masuk antrean (SCRUM-207).
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
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

    # AC6: snapshot kasus per sesi, tabel terpisah dari kasus live, hanya INSERT.
    op.create_table(
        "case_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("measurement_sessions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "case_snapshot_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("case_snapshots.id"), nullable=False),
        # Referensi ke kasus live dan versi approved yang disalin, bukan isi yang dipakai.
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("case_version_id", sa.Uuid(), sa.ForeignKey("case_versions.id"), nullable=False),
        sa.Column("case_code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("split_tag", _enum(case_split_tag_enum), nullable=False),
        sa.Column("category", sa.String(length=200), nullable=True),
        sa.Column("reference_answer", sa.Text(), nullable=True),
        sa.Column("legal_refs", _JSONB, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("answer_criteria", _JSONB, nullable=True),
        sa.Column("traps", _JSONB, nullable=True),
        sa.Column("content", _JSONB, nullable=False),
    )

    if is_postgres:
        op.execute(
            """
            CREATE FUNCTION case_snapshots_forbid_change() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is immutable once written', TG_TABLE_NAME
                    USING ERRCODE = 'insufficient_privilege';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table in _SNAPSHOT_TABLES:
            # Termasuk UPDATE yang tidak mengubah apa pun: trigger baris selalu jalan.
            op.execute(
                f"""
                CREATE TRIGGER {table}_block_change
                BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION case_snapshots_forbid_change()
                """
            )
            op.execute(
                f"""
                CREATE TRIGGER {table}_block_truncate
                BEFORE TRUNCATE ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION case_snapshots_forbid_change()
                """
            )

    # AC3, AC8: satu percobaan per item snapshot per nomor pengulangan. Coba ulang
    # menambah call log (SCRUM-209), bukan baris baru. failure_kind ikut SCRUM-209.
    op.create_table(
        "attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "session_id", sa.Uuid(), sa.ForeignKey("measurement_sessions.id"), nullable=False
        ),
        sa.Column(
            "snapshot_item_id",
            sa.Uuid(),
            sa.ForeignKey("case_snapshot_items.id"),
            nullable=False,
        ),
        sa.Column("repetition_no", sa.SmallInteger(), nullable=False),
        sa.Column("source", _enum(attempt_source_enum), nullable=False),
        sa.Column("status", _enum(attempt_status_enum), server_default="pending", nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("model_version", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("repetition_no BETWEEN 1 AND 5", name="ck_attempts_repetition_no"),
        sa.UniqueConstraint(
            "session_id", "snapshot_item_id", "repetition_no", name="uq_attempts_repetition"
        ),
    )

    if is_postgres:
        # Aturan lintas tabel yang tidak bisa ditulis sebagai CHECK, dan jawaban
        # mentah yang permanen. IS DISTINCT FROM supaya mengosongkan ke NULL juga
        # terhitung perubahan; menulis jawaban yang sama tetap boleh.
        op.execute(
            """
            CREATE FUNCTION attempts_protect_answer() RETURNS trigger AS $$
            DECLARE
                max_repetitions smallint;
            BEGIN
                IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                    RAISE EXCEPTION 'attempts cannot be deleted'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;

                IF TG_OP = 'UPDATE' AND OLD.answer_text IS NOT NULL
                   AND NEW.answer_text IS DISTINCT FROM OLD.answer_text THEN
                    RAISE EXCEPTION 'attempt answer is permanent once written'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;

                SELECT repetitions INTO max_repetitions
                FROM measurement_sessions WHERE id = NEW.session_id;
                IF NEW.repetition_no > max_repetitions THEN
                    RAISE EXCEPTION 'repetition_no % exceeds the session repetitions %',
                        NEW.repetition_no, max_repetitions
                        USING ERRCODE = 'check_violation';
                END IF;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER attempts_protect_answer
            BEFORE INSERT OR UPDATE OR DELETE ON attempts
            FOR EACH ROW EXECUTE FUNCTION attempts_protect_answer()
            """
        )
        op.execute(
            """
            CREATE TRIGGER attempts_block_truncate
            BEFORE TRUNCATE ON attempts
            FOR EACH STATEMENT EXECUTE FUNCTION attempts_protect_answer()
            """
        )


def downgrade() -> None:
    op.drop_table("attempts")
    op.drop_table("case_snapshot_items")
    op.drop_table("case_snapshots")
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
        op.execute("DROP FUNCTION IF EXISTS attempts_protect_answer()")
        op.execute("DROP FUNCTION IF EXISTS case_snapshots_forbid_change()")
        op.execute("DROP FUNCTION IF EXISTS measurement_sessions_forbid_delete()")
        for enum in reversed(_ENUMS):
            enum.drop(bind, checkfirst=True)
