"""PBI-12 (SCRUM-214): a label or accept-as-is reason must hold visible text.

Adra's #89 note: the CHECKs used length(trim(x)) > 0, and PostgreSQL's trim() only
strips ordinary spaces, so a label or reason made of a tab, a newline, a non-breaking
space or a zero-width space was stored. Both CHECKs now trim the same blank characters
as app.shared.text: every str.isspace() character plus the invisible format characters
(U+180E, U+200B to U+200D, U+2060, U+FEFF).

The character list is copied here, not imported, so a later change to the app cannot
rewrite what this revision did. tests/integration/test_measurement_sessions_pg.py checks
the database against app.shared.text character by character.

The upgrade refuses while a session already has such a label or reason: inventing a
name or a reason would rewrite measured history, so an Admin renames the session or
clears and re-accepts it first. The downgrade restores the plain trim() CHECKs, which
are looser, so it never fails.

Revision ID: 9b526a16b704
Revises: c1fd2ffcc735
"""

import sqlalchemy as sa
from alembic import op

revision = "9b526a16b704"  # pragma: allowlist secret
down_revision = "c1fd2ffcc735"  # pragma: allowlist secret
branch_labels = None
depends_on = None

_TABLE = "measurement_sessions"
_LABEL = "ck_measurement_sessions_label_not_blank"
_ACCEPT = "ck_measurement_sessions_accepted_as_is_complete"

_BLANK = (
    "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f "
    "\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
    "\u180e\u200b\u200c\u200d\u2060\ufeff"
)

_ACCEPT_COMPLETE = (
    "(accepted_as_is_at IS NULL AND accepted_as_is_by IS NULL "
    "AND accepted_as_is_reason IS NULL) OR "
    "(accepted_as_is_at IS NOT NULL AND accepted_as_is_by IS NOT NULL "
    "AND {reason_not_blank})"
)


def _not_blank(column: str, chars: str | None) -> str:
    if chars is None:
        return f"length(trim({column})) > 0"
    return f"length(trim({column}, '{chars}')) > 0"


def _replace_checks(chars: str | None) -> None:
    op.drop_constraint(_LABEL, _TABLE, type_="check")
    op.create_check_constraint(_LABEL, _TABLE, _not_blank("label", chars))
    op.drop_constraint(_ACCEPT, _TABLE, type_="check")
    reason = _not_blank("coalesce(accepted_as_is_reason, '')", chars)
    op.create_check_constraint(_ACCEPT, _TABLE, _ACCEPT_COMPLETE.format(reason_not_blank=reason))


def _count(bind, where: str) -> int:
    return bind.execute(
        sa.text(f"SELECT count(*) FROM {_TABLE} WHERE {where}"), {"blank": _BLANK}
    ).scalar_one()


def upgrade():
    bind = op.get_bind()
    labels = _count(bind, "length(trim(label, :blank)) = 0")
    if labels:
        raise RuntimeError(
            f"{labels} session(s) have a blank label (only invisible characters). "
            "Rename them before upgrading."
        )
    reasons = _count(
        bind,
        "accepted_as_is_reason IS NOT NULL AND length(trim(accepted_as_is_reason, :blank)) = 0",
    )
    if reasons:
        raise RuntimeError(
            f"{reasons} session(s) have a blank accept-as-is reason. "
            "Clear and accept them again with a written reason before upgrading."
        )
    _replace_checks(_BLANK)


def downgrade():
    _replace_checks(None)
