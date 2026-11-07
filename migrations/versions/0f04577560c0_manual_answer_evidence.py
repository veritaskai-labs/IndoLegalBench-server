"""PBI-22 (SCRUM-233): evidence for manual answers (D9 attempt_evidence) and file storage.

D9 (manual answer ERD) adds attempt_evidence: optional proof for one manual answer, a
screenshot file or a link, at most 3 per attempt (AC7).

- A CHECK keeps the file columns (file_key, mime_type, size_bytes) and the link column
  (url) on the right kind. Only image/png, image/jpeg and application/pdf are stored; the
  service reads the type from the file's bytes, not the extension.
- attempt_evidence_guard (BEFORE INSERT) locks the attempt row, so two uploads at the same
  time cannot both pass the count, and refuses a 4th row or an automatic attempt.
- Rows are never updated (BEFORE UPDATE refuses). A wrong proof is deleted and uploaded
  again, and the service audits both.

evidence_files holds the file bytes (BYTEA) under file_key. The API container has no
volume and backup.sh only dumps PostgreSQL, so the bytes stay in the database (SA
decision, 7 November 2026). file_key has no FK so the storage can be swapped later.

The downgrade refuses while any evidence exists: dropping the tables would lose proof
the legal team uploaded.

Revision ID: 0f04577560c0
Revises: d8a531f0c231
"""

import sqlalchemy as sa
from alembic import op

revision = "0f04577560c0"  # pragma: allowlist secret
down_revision = "d8a531f0c231"  # pragma: allowlist secret
branch_labels = None
depends_on = None

_KIND = sa.Enum("file", "link", name="attempt_evidence_kind_enum")


def upgrade() -> None:
    op.create_table(
        "evidence_files",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "attempt_evidence",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_id", sa.Uuid(as_uuid=True), sa.ForeignKey("attempts.id"), nullable=False
        ),
        sa.Column("kind", _KIND, nullable=False),
        sa.Column("file_key", sa.String(64), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("mime_type", sa.String(32), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("uploaded_by", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(kind = 'file' AND file_key IS NOT NULL AND mime_type IS NOT NULL "
            "AND size_bytes IS NOT NULL AND url IS NULL) OR "
            "(kind = 'link' AND url IS NOT NULL AND file_key IS NULL "
            "AND mime_type IS NULL AND size_bytes IS NULL)",
            name="ck_attempt_evidence_kind_fields",
        ),
        sa.CheckConstraint(
            "mime_type IS NULL OR mime_type IN ('image/png', 'image/jpeg', 'application/pdf')",
            name="ck_attempt_evidence_mime_type",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes > 0", name="ck_attempt_evidence_size_bytes"
        ),
    )
    op.create_index("ix_attempt_evidence_attempt_id", "attempt_evidence", ["attempt_id"])

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE FUNCTION attempt_evidence_check_insert() RETURNS trigger AS $$
        DECLARE
            attempt_source attempt_source_enum;
            existing integer;
        BEGIN
            -- Locking the attempt serialises concurrent uploads for the same answer.
            SELECT source INTO attempt_source FROM attempts WHERE id = NEW.attempt_id FOR UPDATE;
            IF attempt_source IS DISTINCT FROM 'manual' THEN
                RAISE EXCEPTION 'evidence is only for manual attempts'
                    USING ERRCODE = 'check_violation';
            END IF;
            SELECT count(*) INTO existing FROM attempt_evidence WHERE attempt_id = NEW.attempt_id;
            IF existing >= 3 THEN
                RAISE EXCEPTION 'attempt evidence limit reached (3)'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE FUNCTION attempt_evidence_forbid_update() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'attempt evidence cannot be changed; delete and upload again'
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER attempt_evidence_guard BEFORE INSERT ON attempt_evidence "
        "FOR EACH ROW EXECUTE FUNCTION attempt_evidence_check_insert()"
    )
    op.execute(
        "CREATE TRIGGER attempt_evidence_no_update BEFORE UPDATE ON attempt_evidence "
        "FOR EACH ROW EXECUTE FUNCTION attempt_evidence_forbid_update()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    has_evidence = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM attempt_evidence) "
            "OR EXISTS (SELECT 1 FROM evidence_files)"
        )
    )
    if has_evidence:
        raise RuntimeError("Cannot downgrade: manual answer evidence would be lost.")

    op.drop_index("ix_attempt_evidence_attempt_id", table_name="attempt_evidence")
    op.drop_table("attempt_evidence")
    op.drop_table("evidence_files")
    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION attempt_evidence_check_insert()")
        op.execute("DROP FUNCTION attempt_evidence_forbid_update()")
        _KIND.drop(bind)
