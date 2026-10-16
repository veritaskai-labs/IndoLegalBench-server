"""create regulation_types, legal_refs, legal_ref_links and copy Sprint 1 references

Revision ID: d4f7a2c18b36
Revises: a6d2f9c4e185
Create Date: 2026-10-17 09:00:00.000000

PBI-21, SCRUM-159. Seeds the 266 regulation types from
migrations/seeds/regulation_types_seed_2026-10-16.csv, then copies the
references that Sprint 1 cases keep under the legal_refs key of
case_versions.content into the new tables.

The copy only reads case_versions. The legal_refs key stays in content, so
approved versions (locked by PBI-8) are not touched and the current editor
keeps working until the new form replaces it. Because content never
changes, downgrade just drops the new tables and loses nothing.

The mapping rules live in app/modules/legal_refs/legacy_mapping.py. This
file only reads the rows and writes the result.
"""

import json
import logging
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.modules.legal_refs import legacy_mapping, seed, type_lookup

revision: str = "d4f7a2c18b36"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "a6d2f9c4e185"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

kind_enum = postgresql.ENUM(
    "structured", "free_text", name="legal_ref_kind_enum", create_type=False
)
link_form_enum = postgresql.ENUM(
    "name_only", "pasal", "pasal_angka", name="legal_ref_link_form_enum", create_type=False
)
connector_enum = postgresql.ENUM(
    "none", "jo", "jis", name="legal_ref_connector_enum", create_type=False
)
_ENUMS = (kind_enum, link_form_enum, connector_enum)

# Lightweight table definitions for the inserts, so this file does not depend
# on the ORM models changing later.
_regulation_types = sa.table(
    "regulation_types",
    sa.column("code", sa.String),
    sa.column("label", sa.String),
    sa.column("singkatan", sa.String),
    sa.column("urutan", sa.Integer),
    sa.column("is_active", sa.Boolean),
)
_legal_refs = sa.table(
    "legal_refs",
    sa.column("id", sa.Uuid),
    sa.column("case_version_id", sa.Uuid),
    sa.column("order", sa.Integer),
    sa.column("kind", kind_enum),
    sa.column("free_text", sa.Text),
)
_legal_ref_links = sa.table(
    "legal_ref_links",
    sa.column("id", sa.Uuid),
    sa.column("legal_ref_id", sa.Uuid),
    sa.column("position", sa.Integer),
    sa.column("connector", connector_enum),
    sa.column("form", link_form_enum),
    sa.column("regulation_type", sa.String),
    sa.column("number", sa.String),
    sa.column("year", sa.Integer),
    sa.column("pasal", sa.String),
    sa.column("ayat", sa.String),
    sa.column("huruf", sa.String),
)

_CHUNK = 500


def upgrade() -> None:
    bind = op.get_bind()
    for enum in _ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "regulation_types",
        sa.Column("code", sa.String(length=150), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("singkatan", sa.String(length=50), nullable=True),
        sa.Column("urutan", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.PrimaryKeyConstraint("code"),
        sa.UniqueConstraint("urutan"),
    )
    op.create_table(
        "legal_refs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_version_id", sa.Uuid(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("kind", kind_enum, nullable=False),
        sa.Column("free_text", sa.Text(), nullable=True),
        sa.CheckConstraint('"order" >= 1', name="ck_legal_refs_order_positive"),
        sa.CheckConstraint(
            "(kind = 'free_text' AND free_text IS NOT NULL AND length(trim(free_text)) > 0)"
            " OR (kind <> 'free_text' AND free_text IS NULL)",
            name="ck_legal_refs_free_text_only_for_free_text_kind",
        ),
        sa.ForeignKeyConstraint(
            ["case_version_id"], ["case_versions.id"], name="fk_legal_refs_case_version_id"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_version_id", "order", name="uq_legal_refs_case_version_id_order"),
    )
    op.create_table(
        "legal_ref_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legal_ref_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("connector", connector_enum, nullable=False),
        sa.Column("form", link_form_enum, nullable=False),
        sa.Column("regulation_type", sa.String(length=150), nullable=False),
        sa.Column("number", sa.String(length=100), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("about", sa.Text(), nullable=True),
        sa.Column("pasal", sa.String(length=40), nullable=True),
        sa.Column("angka", sa.String(length=40), nullable=True),
        sa.Column("ayat", sa.String(length=20), nullable=True),
        sa.Column("huruf", sa.String(length=8), nullable=True),
        sa.CheckConstraint("position >= 1", name="ck_legal_ref_links_position_positive"),
        sa.CheckConstraint(
            "(position = 1 AND connector = 'none') OR (position > 1 AND connector <> 'none')",
            name="ck_legal_ref_links_connector_matches_position",
        ),
        sa.CheckConstraint("year >= 1 AND year <= 9999", name="ck_legal_ref_links_year_range"),
        sa.ForeignKeyConstraint(
            ["legal_ref_id"],
            ["legal_refs.id"],
            name="fk_legal_ref_links_legal_ref_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["regulation_type"],
            ["regulation_types.code"],
            name="fk_legal_ref_links_regulation_type",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "legal_ref_id", "position", name="uq_legal_ref_links_legal_ref_id_position"
        ),
    )

    jenis = seed.load_regulation_types()
    op.bulk_insert(
        _regulation_types,
        [
            {
                "code": item.code,
                "label": item.label,
                "singkatan": item.singkatan,
                "urutan": item.urutan,
                "is_active": item.is_active,
            }
            for item in jenis
        ],
    )
    copy_legacy_refs(bind, type_lookup.RegulationTypeIndex(jenis))


def copy_legacy_refs(
    bind: sa.Connection, lookup: type_lookup.RegulationTypeLookup
) -> dict[str, int]:
    """Copy the legal_refs key of every case version into the new tables.

    Only reads case_versions. Returns the counts that were also logged.
    """
    jumlah = {"versions": 0, "refs": 0, "links": 0, "free_text_fallbacks": 0, "skipped": 0}
    baris_ref: list[dict[str, Any]] = []
    baris_link: list[dict[str, Any]] = []

    versi = bind.execute(sa.text("SELECT id, content FROM case_versions ORDER BY created_at, id"))
    for version_id, content in versi:
        jumlah["versions"] += 1
        hasil = legacy_mapping.map_legacy_refs(_as_dict(content).get("legal_refs"), lookup)
        jumlah["free_text_fallbacks"] += hasil.free_text_fallbacks
        jumlah["skipped"] += hasil.skipped
        for rujukan in hasil.refs:
            ref_id = uuid.uuid4()
            baris_ref.append(
                {
                    "id": ref_id,
                    "case_version_id": version_id,
                    "order": rujukan.order,
                    "kind": rujukan.kind.value,
                    "free_text": rujukan.free_text,
                }
            )
            for link in rujukan.links:
                baris_link.append(
                    {
                        "id": uuid.uuid4(),
                        "legal_ref_id": ref_id,
                        "position": link.position,
                        "connector": link.connector.value,
                        "form": link.form.value,
                        "regulation_type": link.regulation_type,
                        "number": link.number,
                        "year": link.year,
                        "pasal": link.pasal,
                        "ayat": link.ayat,
                        "huruf": link.huruf,
                    }
                )

    jumlah["refs"] = len(baris_ref)
    jumlah["links"] = len(baris_link)
    _insert(bind, _legal_refs, baris_ref)
    _insert(bind, _legal_ref_links, baris_link)
    log.info(
        "legal_refs copy: %(versions)d versions read, %(refs)d references and %(links)d links "
        "written, %(free_text_fallbacks)d kept as free text, %(skipped)d blank entries skipped",
        jumlah,
    )
    return jumlah


def _as_dict(content: Any) -> dict[str, Any]:
    """content is JSONB (already a dict) on PostgreSQL, text on other databases."""
    if isinstance(content, str):
        content = json.loads(content)
    return content if isinstance(content, dict) else {}


def _insert(bind: sa.Connection, tabel: sa.TableClause, baris: list[dict[str, Any]]) -> None:
    for awal in range(0, len(baris), _CHUNK):
        bind.execute(sa.insert(tabel), baris[awal : awal + _CHUNK])


def downgrade() -> None:
    op.drop_table("legal_ref_links")
    op.drop_table("legal_refs")
    op.drop_table("regulation_types")
    bind = op.get_bind()
    for enum in reversed(_ENUMS):
        enum.drop(bind, checkfirst=True)
