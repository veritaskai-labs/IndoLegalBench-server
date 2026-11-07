
"""SCRUM-231 manual answer metadata and history.

Revision ID: d8a531f0c231
Revises: b3c91d7a5e24
"""

import sqlalchemy as sa
from alembic import op

revision = "d8a531f0c231"  # pragma: allowlist secret
down_revision = "b3c91d7a5e24"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def _attempt_guard(*, manual_enabled: bool) -> None:
    if manual_enabled:
        protection = """
        IF TG_OP = 'UPDATE' THEN
            -- A source cannot switch between automatic and manual.
            IF NEW.source IS DISTINCT FROM OLD.source THEN
                RAISE EXCEPTION 'attempt source is immutable'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;

            IF OLD.source = 'automatic'
               AND OLD.answer_text ~ '[^[:space:]]' THEN
                IF NEW.answer_text IS DISTINCT FROM OLD.answer_text THEN
                    RAISE EXCEPTION 'automatic answer is permanent'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;

                IF (
                    NEW.session_id,
                    NEW.snapshot_item_id,
                    NEW.model_version
                ) IS DISTINCT FROM (
                    OLD.session_id,
                    OLD.snapshot_item_id,
                    OLD.model_version
                ) THEN
                    RAISE EXCEPTION 'answered attempt cannot move'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;
            END IF;

            IF OLD.source = 'manual'
               AND OLD.filled_at IS NOT NULL THEN
                -- Previously filled manual attempts cannot move.
                IF (
                    NEW.session_id,
                    NEW.snapshot_item_id,
                    NEW.repetition_no,
                    NEW.model_version
                ) IS DISTINCT FROM (
                    OLD.session_id,
                    OLD.snapshot_item_id,
                    OLD.repetition_no,
                    OLD.model_version
                ) THEN
                    RAISE EXCEPTION 'filled manual attempt cannot move'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;

                IF NEW.filled_at IS NULL OR NEW.filled_by IS NULL THEN
                    RAISE EXCEPTION 'filled manual attempt cannot be cleared'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;

                IF (
                    NEW.answer_text,
                    NEW.platform_refused,
                    NEW.manual_note,
                    NEW.answered_at_platform,
                    NEW.filled_by,
                    NEW.filled_at
                ) IS DISTINCT FROM (
                    OLD.answer_text,
                    OLD.platform_refused,
                    OLD.manual_note,
                    OLD.answered_at_platform,
                    OLD.filled_by,
                    OLD.filled_at
                ) THEN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM answer_history h
                        WHERE h.attempt_id = OLD.id
                          AND h.answer_md IS NOT DISTINCT FROM OLD.answer_text
                          AND h.filled_by IS NOT DISTINCT FROM OLD.filled_by
                          AND h.filled_at = OLD.filled_at
                          AND h.obtained_at
                              IS NOT DISTINCT FROM OLD.answered_at_platform
                          AND h.platform_refused = OLD.platform_refused
                          AND h.note IS NOT DISTINCT FROM OLD.manual_note
                          AND h.overwritten_by = NEW.filled_by
                          AND h.overwritten_at = NEW.filled_at
                    ) THEN
                        RAISE EXCEPTION 'manual overwrite requires history'
                            USING ERRCODE = 'insufficient_privilege';
                    END IF;
                END IF;
            END IF;
        END IF;
        """
    else:
        # Restore the pre-SCRUM-231 protection on downgrade.
        protection = """
        IF TG_OP = 'UPDATE'
           AND OLD.answer_text ~ '[^[:space:]]' THEN
            IF NEW.answer_text IS DISTINCT FROM OLD.answer_text THEN
                RAISE EXCEPTION 'attempt answer is permanent once written'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;

            IF (
                NEW.session_id,
                NEW.snapshot_item_id,
                NEW.model_version
            ) IS DISTINCT FROM (
                OLD.session_id,
                OLD.snapshot_item_id,
                OLD.model_version
            ) THEN
                RAISE EXCEPTION 'an answered attempt cannot move'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
        END IF;
        """

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION attempts_protect_answer()
        RETURNS trigger AS $$
        DECLARE
            max_repetitions smallint;
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION 'attempts cannot be deleted'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;

            {protection}

            IF NOT EXISTS (
                SELECT 1
                FROM case_snapshot_items i
                JOIN case_snapshots s ON s.id = i.snapshot_id
                WHERE i.id = NEW.snapshot_item_id
                  AND s.session_id = NEW.session_id
            ) THEN
                RAISE EXCEPTION 'snapshot item does not belong to session'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;

            SELECT repetitions INTO max_repetitions
            FROM measurement_sessions
            WHERE id = NEW.session_id;

            IF NEW.repetition_no > max_repetitions THEN
                RAISE EXCEPTION 'repetition exceeds session limit'
                    USING ERRCODE = 'check_violation';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def upgrade() -> None:
    op.add_column(
        "attempts",
        sa.Column(
            "filled_by",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "attempts",
        sa.Column(
            "platform_refused",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "attempts",
        sa.Column("manual_note", sa.Text(), nullable=True),
    )

    op.create_table(
        "answer_history",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("attempts.id"),
            nullable=False,
        ),
        sa.Column("answer_md", sa.Text(), nullable=True),
        sa.Column(
            "filled_by",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("obtained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("platform_refused", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "overwritten_by",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("overwritten_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_index(
        "ix_answer_history_attempt_id",
        "answer_history",
        ["attempt_id"],
    )

    if op.get_bind().dialect.name == "postgresql":
        _attempt_guard(manual_enabled=True)

        op.execute(
            """
            CREATE FUNCTION answer_history_forbid_change()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'answer history is append-only'
                    USING ERRCODE = 'insufficient_privilege';
            END;
            $$ LANGUAGE plpgsql;
            """
        )

        op.execute(
            """
            CREATE TRIGGER answer_history_guard
            BEFORE UPDATE OR DELETE ON answer_history
            FOR EACH ROW
            EXECUTE FUNCTION answer_history_forbid_change();
            """
        )

        op.execute(
            """
            CREATE TRIGGER answer_history_block_truncate
            BEFORE TRUNCATE ON answer_history
            FOR EACH STATEMENT
            EXECUTE FUNCTION answer_history_forbid_change();
            """
        )


def downgrade() -> None:
    bind = op.get_bind()

    has_history = bind.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM answer_history)")
    )
    has_metadata = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM attempts "
            "WHERE filled_by IS NOT NULL "
            "OR platform_refused = true "
            "OR manual_note IS NOT NULL)"
        )
    )

    if has_history or has_metadata:
        raise RuntimeError(
            "Cannot downgrade: manual answer data would be lost."
        )

    if bind.dialect.name == "postgresql":
        _attempt_guard(manual_enabled=False)

    op.drop_index(
        "ix_answer_history_attempt_id",
        table_name="answer_history",
    )
    op.drop_table("answer_history")

    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION answer_history_forbid_change()")

    op.drop_column("attempts", "manual_note")
    op.drop_column("attempts", "platform_refused")
    op.drop_column("attempts", "filled_by")
