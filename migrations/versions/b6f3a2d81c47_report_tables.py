"""Tabel laporan perbandingan, dasar kasus, hasil beku, dan penerbitan. SCRUM-79 SCRUM-300.

D1 "ERD Laporan dan Penerbitan" (PBI-15, PBI-16, PBI-17). Laporan disimpan beku: angka,
label, penanda, dan dasar kasus disalin sekali saat laporan dibuat, lalu hanya dibaca.
Trigger PostgreSQL menjaganya untuk semua peran, sama seperti snapshot sesi (16014b26f0bf):

- report_sessions, report_results, report_case_basis, published_reports: hanya INSERT.
- reports: tidak bisa dihapus; hanya status yang boleh berubah, maju saja
  (saved -> published -> superseded) dan hanya kalau baris published_reports-nya ada,
  jadi status dan rantai versi terbit tidak bisa saling bertentangan (desain A).
- Isi laporan hanya bisa ditambah di transaksi yang membuat laporannya
  (reports.created_at = now(), waktu awal transaksi). created_at wajib diisi database.
- Dasar kasus: versi milik kasusnya dan split_tag sama dengan split_tag versi itu.
- Laporan Test tidak memuat skor atau kasus dev; laporan dengan skor dev tidak bisa terbit
  (lapis kedua di belakang reports.service.ensure_publishable).
- Produk manual tidak punya tingkat kegagalan (AC2).
- Terbit dan ganti versi dicek saat COMMIT (constraint trigger tertunda): laporan yang punya
  baris terbit wajib published, dan versi yang diganti wajib superseded.

Kode galat: perubahan isi beku = insufficient_privilege (42501), pelanggaran aturan =
check_violation (23514).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.shared.text import sql_not_blank

revision = "b6f3a2d81c47"  # pragma: allowlist secret
down_revision = "e5b1c7d93a20"  # pragma: allowlist secret
branch_labels = None
depends_on = None

score_scope_enum = postgresql.ENUM(
    "test", "dev", "dev_test", name="report_score_scope_enum", create_type=False
)
score_split_enum = postgresql.ENUM(
    "test", "dev", "combined", name="report_score_split_enum", create_type=False
)
measurement_filter_enum = postgresql.ENUM(
    "automatic", "manual", "all", name="report_measurement_filter_enum", create_type=False
)
measurement_kind_enum = postgresql.ENUM(
    "automatic", "manual", name="report_measurement_kind_enum", create_type=False
)
label_mode_enum = postgresql.ENUM(
    "real_name", "code", name="report_label_mode_enum", create_type=False
)
language_enum = postgresql.ENUM("en", "id", name="report_language_enum", create_type=False)
status_enum = postgresql.ENUM(
    "saved", "published", "superseded", name="report_status_enum", create_type=False
)
# Sudah ada sejak migrasi kasus; dipakai ulang, tidak dibuat atau dihapus di sini.
split_tag_enum = postgresql.ENUM("dev", "test", name="case_split_tag_enum", create_type=False)

_ENUMS = (
    score_scope_enum,
    score_split_enum,
    measurement_filter_enum,
    measurement_kind_enum,
    label_mode_enum,
    language_enum,
    status_enum,
)

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

_CHILD_TABLES = ("report_sessions", "report_results", "report_case_basis")
_INSERT_ONLY_TABLES = (*_CHILD_TABLES, "published_reports")
_FUNCTIONS = (
    "reports_guard_update",
    "report_rows_forbid_change",
    "report_rows_check_insert",
    "published_reports_check_insert",
    "published_reports_check_commit",
)
_RANKED = sa.text("rank IS NOT NULL")
_SCORES = " AND ".join(
    f"({c} IS NULL OR {c} BETWEEN 0 AND 1)" for c in ("det_score", "llm_score", "total_score")
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum in _ENUMS:
            enum.create(bind, checkfirst=True)

    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("score_scope", score_scope_enum, nullable=False),
        sa.Column("has_dev_scores", sa.Boolean(), nullable=False),
        sa.Column("measurement_filter", measurement_filter_enum, nullable=False),
        sa.Column("product_label_mode", label_mode_enum, nullable=False),
        sa.Column("language", language_enum, nullable=False, server_default="en"),
        sa.Column("status", status_enum, nullable=False, server_default="saved"),
        sa.Column(
            "rubric_version_id", sa.Uuid(), sa.ForeignKey("rubric_versions.id"), nullable=False
        ),
        sa.Column("judge_model_name", sa.String(200), nullable=False),
        sa.Column("case_basis_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(sql_not_blank("title"), name="ck_reports_title_not_blank"),
        sa.CheckConstraint(
            sql_not_blank("judge_model_name"), name="ck_reports_judge_model_name_not_blank"
        ),
        sa.CheckConstraint("has_dev_scores = (score_scope <> 'test')", name="ck_reports_dev_flag"),
        sa.CheckConstraint("case_basis_count > 0", name="ck_reports_case_basis_count"),
    )
    op.create_index("ix_reports_created_by_created_at", "reports", ["created_by", "created_at"])
    op.create_index("ix_reports_status_created_at", "reports", ["status", "created_at"])

    op.create_table(
        "report_sessions",
        sa.Column("report_id", sa.Uuid(), sa.ForeignKey("reports.id"), primary_key=True),
        sa.Column(
            "session_id", sa.Uuid(), sa.ForeignKey("measurement_sessions.id"), primary_key=True
        ),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("ai_products.id"), nullable=False),
        sa.Column("product_label", sa.String(200), nullable=False),
        sa.Column("measurement_kind", measurement_kind_enum, nullable=False),
        sa.Column("is_trial", sa.Boolean(), nullable=False),
        sa.Column("markers", _JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.UniqueConstraint("report_id", "product_id", name="uq_report_sessions_product"),
        sa.CheckConstraint(
            sql_not_blank("product_label"), name="ck_report_sessions_product_label_not_blank"
        ),
    )
    op.create_index("ix_report_sessions_session_id", "report_sessions", ["session_id"])

    op.create_table(
        "report_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("score_split", score_split_enum, nullable=False),
        sa.Column("cases_graded_count", sa.Integer(), nullable=False),
        sa.Column("cases_basis_count", sa.Integer(), nullable=False),
        sa.Column("det_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("llm_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("total_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("failure_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("rank", sa.SmallInteger(), nullable=True),
        sa.Column("repetitions", sa.SmallInteger(), nullable=False),
        sa.Column("product_model_version", sa.String(200), nullable=True),
        sa.Column("judge_model_version", sa.String(200), nullable=True),
        sa.ForeignKeyConstraint(
            ["report_id", "session_id"],
            ["report_sessions.report_id", "report_sessions.session_id"],
            name="fk_report_results_report_session",
        ),
        sa.UniqueConstraint(
            "report_id", "session_id", "score_split", name="uq_report_results_split"
        ),
        sa.CheckConstraint(_SCORES, name="ck_report_results_scores"),
        sa.CheckConstraint(
            "failure_rate IS NULL OR failure_rate BETWEEN 0 AND 100",
            name="ck_report_results_failure_rate",
        ),
        sa.CheckConstraint(
            "cases_basis_count >= 0 AND cases_basis_count <= cases_graded_count",
            name="ck_report_results_counts",
        ),
        sa.CheckConstraint("rank IS NULL OR rank >= 1", name="ck_report_results_rank"),
        sa.CheckConstraint(
            "(total_score IS NULL) = (rank IS NULL)", name="ck_report_results_rank_total"
        ),
        sa.CheckConstraint(
            "(total_score IS NULL) = (det_score IS NULL OR llm_score IS NULL)",
            name="ck_report_results_total_needs_both",
        ),
        sa.CheckConstraint("repetitions >= 1", name="ck_report_results_repetitions"),
    )
    op.create_index(
        "uq_report_results_rank",
        "report_results",
        ["report_id", "score_split", "rank"],
        unique=True,
        postgresql_where=_RANKED,
        sqlite_where=_RANKED,
    )

    op.create_table(
        "report_case_basis",
        sa.Column("report_id", sa.Uuid(), sa.ForeignKey("reports.id"), primary_key=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("cases.id"), primary_key=True),
        sa.Column(
            "case_version_id", sa.Uuid(), sa.ForeignKey("case_versions.id"), nullable=False
        ),
        sa.Column("split_tag", split_tag_enum, nullable=False),
    )

    op.create_table(
        "published_reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("report_id", sa.Uuid(), sa.ForeignKey("reports.id"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("published_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "supersedes_id", sa.Uuid(), sa.ForeignKey("published_reports.id"), nullable=True
        ),
        sa.UniqueConstraint("report_id", name="uq_published_reports_report"),
        sa.UniqueConstraint("supersedes_id", name="uq_published_reports_supersedes"),
        sa.CheckConstraint("version_no >= 1", name="ck_published_reports_version_no"),
        sa.CheckConstraint(
            "(supersedes_id IS NULL) = (version_no = 1)", name="ck_published_reports_chain"
        ),
        sa.CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id", name="ck_published_reports_not_self"
        ),
    )

    if bind.dialect.name == "postgresql":
        _create_triggers()


def _create_triggers() -> None:
    # jsonb_typeof hanya ada di PostgreSQL; bentuk isi tiap penanda dijaga service.
    op.create_check_constraint(
        "ck_report_sessions_markers_array",
        "report_sessions",
        "jsonb_typeof(markers) = 'array'",
    )
    op.execute(
        """
        CREATE FUNCTION report_rows_forbid_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is frozen once written', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in _INSERT_ONLY_TABLES:
        # Termasuk UPDATE yang tidak mengubah apa pun: trigger baris selalu jalan.
        op.execute(
            f"""
            CREATE TRIGGER {table}_block_change
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION report_rows_forbid_change()
            """
        )
    for table in ("reports", *_INSERT_ONLY_TABLES):
        op.execute(
            f"""
            CREATE TRIGGER {table}_block_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION report_rows_forbid_change()
            """
        )
    op.execute(
        """
        CREATE TRIGGER reports_block_delete
        BEFORE DELETE ON reports
        FOR EACH ROW EXECUTE FUNCTION report_rows_forbid_change()
        """
    )

    # Desain A: status di reports, rantai versi di published_reports. Status hanya boleh
    # maju dan hanya kalau baris terbitnya ada, jadi dua sumber itu selalu sepakat.
    # Kolom lain dibandingkan lewat to_jsonb tanpa status: kolom baru di masa depan
    # otomatis ikut beku tanpa mengubah trigger ini.
    op.execute(
        """
        CREATE FUNCTION reports_guard_update() RETURNS trigger AS $$
        BEGIN
            IF (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
                RAISE EXCEPTION 'only the status of a report can change'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NEW.status = OLD.status THEN
                RETURN NEW;
            END IF;
            IF NOT ((OLD.status = 'saved' AND NEW.status = 'published')
                    OR (OLD.status = 'published' AND NEW.status = 'superseded')) THEN
                RAISE EXCEPTION 'report status cannot move from % to %', OLD.status, NEW.status
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.status = 'published' AND NOT EXISTS (
                SELECT 1 FROM published_reports WHERE report_id = NEW.id
            ) THEN
                RAISE EXCEPTION 'report % has no published_reports row', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.status = 'superseded' AND NOT EXISTS (
                SELECT 1 FROM published_reports lama
                JOIN published_reports baru ON baru.supersedes_id = lama.id
                WHERE lama.report_id = NEW.id
            ) THEN
                RAISE EXCEPTION 'report % has no newer published version', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER reports_guard_update
        BEFORE UPDATE ON reports
        FOR EACH ROW EXECUTE FUNCTION reports_guard_update()
        """
    )

    # Isi laporan: hanya di transaksi pembuatnya, dan sesuai jenis nilai laporan.
    op.execute(
        """
        CREATE FUNCTION report_rows_check_insert() RETURNS trigger AS $$
        DECLARE
            induk reports%ROWTYPE;
        BEGIN
            SELECT * INTO induk FROM reports WHERE id = NEW.report_id;
            IF NOT FOUND THEN
                RETURN NEW;  -- FK yang menolak, dengan foreign_key_violation.
            END IF;
            IF induk.created_at <> now() THEN
                RAISE EXCEPTION 'report % is closed; % rows can only be added when it is created',
                    NEW.report_id, TG_TABLE_NAME
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF TG_TABLE_NAME = 'report_results' THEN
                IF induk.score_scope <> 'dev_test'
                        AND NEW.score_split::text <> induk.score_scope::text THEN
                    RAISE EXCEPTION 'a % report cannot hold % scores',
                        induk.score_scope, NEW.score_split
                        USING ERRCODE = 'check_violation';
                END IF;
                IF NEW.failure_rate IS NOT NULL AND EXISTS (
                    SELECT 1 FROM report_sessions
                    WHERE report_id = NEW.report_id AND session_id = NEW.session_id
                        AND measurement_kind = 'manual'
                ) THEN
                    RAISE EXCEPTION 'failure rate does not apply to a manual product'
                        USING ERRCODE = 'check_violation';
                END IF;
            ELSIF TG_TABLE_NAME = 'report_case_basis' THEN
                IF induk.score_scope <> 'dev_test'
                        AND NEW.split_tag::text <> induk.score_scope::text THEN
                    RAISE EXCEPTION 'a % report cannot use % cases',
                        induk.score_scope, NEW.split_tag
                        USING ERRCODE = 'check_violation';
                END IF;
                -- split_tag disalin dari versinya; harus sama, kalau tidak kasus dev bisa
                -- masuk laporan Test dengan menulis 'test' (AC7).
                IF NOT EXISTS (
                    SELECT 1 FROM case_versions
                    WHERE id = NEW.case_version_id AND case_id = NEW.case_id
                        AND split_tag::text = NEW.split_tag::text
                ) THEN
                    RAISE EXCEPTION
                        'case_version % does not belong to case % with split_tag %',
                        NEW.case_version_id, NEW.case_id, NEW.split_tag
                        USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in _CHILD_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_check_insert
            BEFORE INSERT ON {table}
            FOR EACH ROW EXECUTE FUNCTION report_rows_check_insert()
            """
        )

    # Lapis kedua di belakang reports.service.ensure_publishable (AC7, AC14), plus rantai
    # versi: hanya versi yang sedang terbit yang bisa diganti, dengan nomor berikutnya.
    op.execute(
        """
        CREATE FUNCTION published_reports_check_insert() RETURNS trigger AS $$
        DECLARE
            nomor_lama integer;
        BEGIN
            IF EXISTS (SELECT 1 FROM reports WHERE id = NEW.report_id AND has_dev_scores) THEN
                RAISE EXCEPTION 'a report with dev scores cannot be published'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.supersedes_id IS NOT NULL THEN
                SELECT lama.version_no INTO nomor_lama
                FROM published_reports lama JOIN reports r ON r.id = lama.report_id
                WHERE lama.id = NEW.supersedes_id AND r.status = 'published';
                IF nomor_lama IS NULL THEN
                    RAISE EXCEPTION 'only the currently published version can be replaced'
                        USING ERRCODE = 'check_violation';
                END IF;
                IF NEW.version_no <> nomor_lama + 1 THEN
                    RAISE EXCEPTION 'the next version of % is %, not %',
                        NEW.supersedes_id, nomor_lama + 1, NEW.version_no
                        USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER published_reports_check_insert
        BEFORE INSERT ON published_reports
        FOR EACH ROW EXECUTE FUNCTION published_reports_check_insert()
        """
    )

    # Terbit atau ganti versi = INSERT published_reports + UPDATE status laporan baru
    # (+ UPDATE versi lama ke superseded). Diperiksa saat COMMIT, jadi transaksi yang lupa
    # salah satu UPDATE ditolak dan tidak pernah ada dua versi terbit sekaligus.
    op.execute(
        """
        CREATE FUNCTION published_reports_check_commit() RETURNS trigger AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM reports
                WHERE id = NEW.report_id AND status IN ('published', 'superseded')
            ) THEN
                RAISE EXCEPTION 'report % has a publication but its status is not published',
                    NEW.report_id
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.supersedes_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM published_reports lama JOIN reports r ON r.id = lama.report_id
                WHERE lama.id = NEW.supersedes_id AND r.status = 'superseded'
            ) THEN
                RAISE EXCEPTION 'the version replaced by % is not marked superseded', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER published_reports_check_commit
        AFTER INSERT ON published_reports
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION published_reports_check_commit()
        """
    )


def downgrade() -> None:
    # Trigger ikut terhapus bersama tabelnya; fungsi dan enum dihapus sesudahnya.
    op.drop_table("published_reports")
    op.drop_table("report_case_basis")
    op.drop_index("uq_report_results_rank", table_name="report_results")
    op.drop_table("report_results")
    op.drop_index("ix_report_sessions_session_id", table_name="report_sessions")
    op.drop_table("report_sessions")
    op.drop_index("ix_reports_status_created_at", table_name="reports")
    op.drop_index("ix_reports_created_by_created_at", table_name="reports")
    op.drop_table("reports")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for function in _FUNCTIONS:
            op.execute(f"DROP FUNCTION IF EXISTS {function}()")
        for enum in reversed(_ENUMS):
            enum.drop(bind, checkfirst=True)
