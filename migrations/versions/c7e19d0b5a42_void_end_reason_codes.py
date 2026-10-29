"""SCRUM-197: store the void end_reason of a review assignment as a code.

Before SCRUM-197 deactivating a reviewer wrote the text "Reviewer
deactivated" into end_reason. A void now holds an AssignmentVoidReason code
(deactivated, role_changed) and GET /reviews/needs-reassignment validates it
as void_reason, so the old rows are converted here. Only status = 'void' is
touched: a replaced assignment keeps the admin's free-text reason.

Data only, no schema change. audit_logs is append-only, so the reason of
past review.assignment_voided rows stays as it was written.

The downgrade turns "deactivated" back into the old text. "role_changed"
has no old form and the old app reads end_reason as plain text, so it stays.

Revision ID: c7e19d0b5a42
Revises: ad76e1ba6c12
"""
import sqlalchemy as sa
from alembic import op

revision = "c7e19d0b5a42"  # pragma: allowlist secret
down_revision = "ad76e1ba6c12"  # pragma: allowlist secret
branch_labels = None
depends_on = None

TEKS_LAMA = "Reviewer deactivated"
KODE_NONAKTIF = "deactivated"

_GANTI = sa.text(
    "UPDATE review_assignments SET end_reason = :baru "
    "WHERE status = 'void' AND end_reason = :lama"
)


def void_reason_to_codes(bind: sa.Connection) -> int:
    """Old void text to the deactivated code. Returns the number of rows changed."""
    return bind.execute(_GANTI, {"lama": TEKS_LAMA, "baru": KODE_NONAKTIF}).rowcount


def void_reason_to_text(bind: sa.Connection) -> int:
    """The deactivated code back to the old text. Returns the number of rows changed."""
    return bind.execute(_GANTI, {"lama": KODE_NONAKTIF, "baru": TEKS_LAMA}).rowcount


def upgrade():
    void_reason_to_codes(op.get_bind())


def downgrade():
    void_reason_to_text(op.get_bind())
