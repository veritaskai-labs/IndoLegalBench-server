"""Allow the worker to initialize the observed model version once.

SCRUM-249: D2 reserves model_version_at_start for the first response, but #89
made the entire product copy immutable. Keep every other field immutable and
require an actual attempt with that model version in this running session.
"""

from alembic import op

revision = "d49a1f2b3c60"  # pragma: allowlist secret
down_revision = "c1fd2ffcc735"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
        CREATE FUNCTION session_product_copy_initialize_version() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF OLD.model_version_at_start IS NULL
                   AND NEW.model_version_at_start ~ '[^[:space:]]'
                   AND (to_jsonb(OLD) - 'model_version_at_start') =
                       (to_jsonb(NEW) - 'model_version_at_start')
                   AND EXISTS (SELECT 1 FROM measurement_sessions
                               WHERE id = NEW.session_id AND status IN ('running', 'paused', 'cancelled'))
                   AND EXISTS (SELECT 1 FROM attempts
                               WHERE session_id = NEW.session_id
                                 AND model_version = NEW.model_version_at_start) THEN
                    RETURN NEW;
                END IF;
            END IF;
            RAISE EXCEPTION 'session_product_copies is immutable once written'
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER session_product_copies_block_change ON session_product_copies")
    op.execute("""
        CREATE TRIGGER session_product_copies_block_change
        BEFORE UPDATE OR DELETE ON session_product_copies
        FOR EACH ROW EXECUTE FUNCTION session_product_copy_initialize_version()
    """)


def downgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER session_product_copies_block_change ON session_product_copies")
    op.execute("""
        CREATE TRIGGER session_product_copies_block_change
        BEFORE UPDATE OR DELETE ON session_product_copies
        FOR EACH ROW EXECUTE FUNCTION case_snapshots_forbid_change()
    """)
    op.execute("DROP FUNCTION session_product_copy_initialize_version()")
