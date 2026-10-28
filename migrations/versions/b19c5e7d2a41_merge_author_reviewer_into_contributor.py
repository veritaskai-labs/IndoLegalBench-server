"""PBI-24 (SCRUM-195): move author and reviewer accounts to contributor.

Second of two steps (4a10dea5a620 added the value). Cases, versions,
review_assignments, review_verdicts, and audit history point at users.id,
not at the role, so they stay with their owners untouched (AC7).

Every account that changes gets one user.role_changed row in audit_logs
(AC9), with no actor (the system) and the SCRUM-195 reason. audit_logs is
append-only, so the downgrade does not delete those rows: it reads the old
role back from them and adds a reverse row for each account it restores.
A contributor with no such row (created after this migration) goes back to
author, the safe side, since it cannot review. If audit retention has
already removed a row, the same applies.

PostgreSQL cannot drop an enum value, and a value added in the same
transaction cannot be used, so the column goes through text while the
type is rebuilt.

Revision ID: b19c5e7d2a41
Revises: 4a10dea5a620
"""
import sqlalchemy as sa
from alembic import op

revision = "b19c5e7d2a41"  # pragma: allowlist secret
down_revision = "4a10dea5a620"  # pragma: allowlist secret
branch_labels = None
depends_on = None

ALASAN = "SCRUM-195: Author dan Reviewer digabung menjadi Kontributor"
ALASAN_BALIK = "SCRUM-195: rollback migrasi akun Kontributor"
PERAN_BARU = ("admin", "viewer", "developer_ai", "contributor")
PERAN_LAMA = ("author", "reviewer", "admin", "viewer", "developer_ai", "contributor")

# Peran lama tiap akun, dari baris audit migrasi ini yang paling baru.
_PERAN_ASAL = """
    SELECT DISTINCT ON (entity_id) entity_id, before ->> 'role' AS role
    FROM audit_logs
    WHERE action = 'user.role_changed' AND reason = :alasan AND actor_user_id IS NULL
    ORDER BY entity_id, id DESC
"""


def upgrade():
    op.execute(
        sa.text(
            "INSERT INTO audit_logs (action, entity_type, entity_id, before, after, reason) "
            "SELECT 'user.role_changed', 'user', id, jsonb_build_object('role', role::text), "
            "jsonb_build_object('role', 'contributor'), :alasan "
            "FROM users WHERE role::text IN ('author', 'reviewer')"
        ).bindparams(alasan=ALASAN)
    )
    _ganti_tipe(
        PERAN_BARU,
        "UPDATE users SET role = 'contributor', updated_at = now() "
        "WHERE role IN ('author', 'reviewer')",
    )


def downgrade():
    op.execute(
        sa.text(
            f"WITH asal AS ({_PERAN_ASAL}) "
            "INSERT INTO audit_logs (action, entity_type, entity_id, before, after, reason) "
            "SELECT 'user.role_changed', 'user', u.id, jsonb_build_object('role', 'contributor'), "
            "jsonb_build_object('role', COALESCE(asal.role, 'author')), :balik "
            "FROM users u LEFT JOIN asal ON asal.entity_id = u.id "
            "WHERE u.role::text = 'contributor'"
        ).bindparams(alasan=ALASAN, balik=ALASAN_BALIK)
    )
    _ganti_tipe(
        PERAN_LAMA,
        sa.text(
            f"WITH asal AS ({_PERAN_ASAL}) "
            "UPDATE users u SET role = COALESCE(asal.role, 'author'), updated_at = now() "
            "FROM users x LEFT JOIN asal ON asal.entity_id = x.id "
            "WHERE x.id = u.id AND u.role = 'contributor'"
        ).bindparams(alasan=ALASAN),
    )


def _ganti_tipe(nilai: tuple[str, ...], ubah_data) -> None:
    """Bangun ulang user_role_enum dengan `nilai`, mengubah data selagi kolomnya teks."""
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE text USING role::text")
    op.execute(ubah_data)
    op.execute("DROP TYPE user_role_enum")
    daftar = ", ".join(f"'{v}'" for v in nilai)
    op.execute(f"CREATE TYPE user_role_enum AS ENUM ({daftar})")
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE user_role_enum USING role::user_role_enum"
    )
