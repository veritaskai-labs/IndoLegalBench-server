"""Asal kasus, tabel impor, dan tag dev/test yang boleh kosong pada draft. PBI-4 SCRUM-81 SCRUM-337.

Diagram D8 (Sprint 4, ERD impor dan duplikasi kasus):
- cases.origin (manual, import, duplicate), import_batch_id, source_case_id, dan
  content_fingerprint. Kasus lama diisi manual; sidik jarinya dihitung dari versi yang
  sedang dikerjakan (backfill), supaya impor pertama sudah bisa mengenali kasus kembar.
- import_batches dan import_row_results: satu berkas dan hasil tiap barisnya (D7).
- case_versions.split_tag boleh NULL, tetapi hanya selama draft (CHECK). Pengajuan review
  sudah menolak tag kosong (completeness.review_blockers), dan trigger
  case_versions_reject_approved_mutation tetap mengunci versi approved.

downgrade(): draft tanpa tag diisi 'test' sebelum kolom dikembalikan NOT NULL. Itu sama
dengan aturan D9 nomor 4 (tag kosong diperlakukan tertutup seperti test), jadi tidak ada
kasus yang tiba-tiba terbuka untuk Developer AI. Asal kasus, batch, dan hasil per baris
ikut terhapus; kasus hasil impor dan duplikat tetap ada sebagai kasus biasa.
"""

import hashlib
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f2a7c4e91b38"  # pragma: allowlist secret
down_revision = "d3e8a1f5c927"  # pragma: allowlist secret
branch_labels = None
depends_on = None

case_origin_enum = postgresql.ENUM(
    "manual", "import", "duplicate", name="case_origin_enum", create_type=False
)
import_row_status_enum = postgresql.ENUM(
    "created", "failed", "skipped", name="import_row_status_enum", create_type=False
)

_SPASI = re.compile(r"\s+")


def _sidik_jari(title: str | None, question: str | None) -> str | None:
    """Salinan beku app.modules.cases.fingerprint.content_fingerprint per revisi ini.

    Migrasi tidak mengimpor kode aplikasi, supaya mengulang migrasi lama tetap memberi hasil
    yang sama walaupun aturan di aplikasi kelak berubah. Tes migrasi memastikan keduanya sama.
    """
    judul = _SPASI.sub(" ", title or "").strip().casefold()
    pertanyaan = _SPASI.sub(" ", question or "").strip().casefold()
    if not judul and not pertanyaan:
        return None
    return hashlib.sha256(f"{judul}\n{pertanyaan}".encode()).hexdigest()


def isi_sidik_jari(conn: sa.Connection) -> int:
    """Isi cases.content_fingerprint dari versi yang sedang dikerjakan. Hasil: baris terisi."""
    baris = conn.execute(
        sa.text(
            "SELECT c.id, v.content->>'title' AS title, v.content->>'question' AS question "
            "FROM cases c JOIN case_versions v ON v.id = c.current_version_id"
        )
    ).all()
    terisi = 0
    for case_id, title, question in baris:
        sidik = _sidik_jari(title, question)
        if sidik is None:
            continue
        conn.execute(
            sa.text("UPDATE cases SET content_fingerprint = :sidik WHERE id = :id"),
            {"sidik": sidik, "id": case_id},
        )
        terisi += 1
    return terisi


def isi_tag_kosong(conn: sa.Connection) -> int:
    """Draft tanpa tag menjadi test, tertutup untuk Developer AI (D9 aturan 4)."""
    hasil = conn.execute(
        sa.text("UPDATE case_versions SET split_tag = 'test' WHERE split_tag IS NULL")
    )
    return hasil.rowcount


def upgrade() -> None:
    bind = op.get_bind()
    case_origin_enum.create(bind, checkfirst=True)
    import_row_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "import_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column(
            "imported_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", name="fk_import_batches_imported_by_users"),
            nullable=False,
        ),
        sa.Column(
            "imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "suite_id",
            sa.Uuid(),
            sa.ForeignKey("suites.id", name="fk_import_batches_suite_id_suites"),
            nullable=False,
        ),
        sa.Column("total_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("success_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_count", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "total_rows >= 0 AND success_count >= 0 AND failed_count >= 0 AND skipped_count >= 0",
            name="ck_import_batches_counts_not_negative",
        ),
        sa.CheckConstraint(
            "success_count + failed_count + skipped_count <= total_rows",
            name="ck_import_batches_counts_within_total",
        ),
        sa.CheckConstraint("length(trim(file_name)) > 0", name="ck_import_batches_file_name"),
    )

    op.add_column(
        "cases",
        sa.Column("origin", case_origin_enum, server_default="manual", nullable=False),
    )
    op.add_column(
        "cases",
        sa.Column(
            "import_batch_id",
            sa.Uuid(),
            sa.ForeignKey("import_batches.id", name="fk_cases_import_batch_id_import_batches"),
            nullable=True,
        ),
    )
    op.add_column(
        "cases",
        sa.Column(
            "source_case_id",
            sa.Uuid(),
            sa.ForeignKey("cases.id", name="fk_cases_source_case_id_cases"),
            nullable=True,
        ),
    )
    op.add_column("cases", sa.Column("content_fingerprint", sa.String(64), nullable=True))
    op.create_index("ix_cases_content_fingerprint", "cases", ["content_fingerprint"])
    op.create_check_constraint(
        "ck_cases_import_batch_matches_origin",
        "cases",
        "(origin = 'import') = (import_batch_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_cases_source_case_matches_origin",
        "cases",
        "(origin = 'duplicate') = (source_case_id IS NOT NULL)",
    )

    op.create_table(
        "import_row_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Uuid(),
            sa.ForeignKey(
                "import_batches.id", name="fk_import_row_results_batch_id_import_batches"
            ),
            nullable=False,
        ),
        sa.Column("row_no", sa.Integer(), nullable=False),
        sa.Column("status", import_row_status_enum, nullable=False),
        sa.Column(
            "case_id",
            sa.Uuid(),
            sa.ForeignKey("cases.id", name="fk_import_row_results_case_id_cases"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("row_fingerprint", sa.String(64), nullable=True),
        sa.UniqueConstraint("batch_id", "row_no", name="uq_import_row_results_batch_id_row_no"),
        sa.CheckConstraint("row_no >= 1", name="ck_import_row_results_row_no"),
        sa.CheckConstraint(
            "(status = 'created') = (case_id IS NOT NULL)",
            name="ck_import_row_results_case_only_when_created",
        ),
        sa.CheckConstraint(
            "status = 'created' OR length(trim(coalesce(reason, ''))) > 0",
            name="ck_import_row_results_reason_when_not_created",
        ),
    )

    op.alter_column("case_versions", "split_tag", nullable=True)
    op.create_check_constraint(
        "ck_case_versions_split_tag_required_after_draft",
        "case_versions",
        "split_tag IS NOT NULL OR status = 'draft'",
    )

    isi_sidik_jari(bind)


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_constraint(
        "ck_case_versions_split_tag_required_after_draft", "case_versions", type_="check"
    )
    isi_tag_kosong(bind)
    op.alter_column("case_versions", "split_tag", nullable=False)

    op.drop_table("import_row_results")
    op.drop_constraint("ck_cases_source_case_matches_origin", "cases", type_="check")
    op.drop_constraint("ck_cases_import_batch_matches_origin", "cases", type_="check")
    op.drop_index("ix_cases_content_fingerprint", table_name="cases")
    op.drop_column("cases", "content_fingerprint")
    op.drop_column("cases", "source_case_id")
    op.drop_column("cases", "import_batch_id")
    op.drop_column("cases", "origin")
    op.drop_table("import_batches")
    import_row_status_enum.drop(bind, checkfirst=True)
    case_origin_enum.drop(bind, checkfirst=True)
