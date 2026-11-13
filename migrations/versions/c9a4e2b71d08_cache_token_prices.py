"""Tarif cache pada produk dan rincian token pada call log. SCRUM-176 SCRUM-298.

Baris lama tidak dihitung ulang: kolom baru boleh kosong.
"""

import sqlalchemy as sa
from alembic import op

revision = "c9a4e2b71d08"  # pragma: allowlist secret
down_revision = "1d73e6afcfe1"  # pragma: allowlist secret
branch_labels = None
depends_on = None

_PRICE = sa.Numeric(precision=12, scale=4)
_CHECK = "ck_ai_products_type_fields"

_CHECK_SQL = """(
    measurement_type = 'automatic'
    AND provider_type IS NOT NULL
    AND base_url IS NOT NULL
    AND model_name IS NOT NULL
    AND credential_encrypted IS NOT NULL
    AND credential_hint IS NOT NULL
    AND rate_limit_per_minute IS NOT NULL
    AND monthly_budget_idr IS NOT NULL
    AND vendor_name IS NULL
    AND site_url IS NULL
    AND access_note IS NULL
    AND manual_params IS NULL
) OR (
    measurement_type = 'manual'
    AND vendor_name IS NOT NULL
    AND site_url IS NOT NULL
    AND provider_type IS NULL
    AND base_url IS NULL
    AND model_name IS NULL
    AND credential_encrypted IS NULL
    AND credential_hint IS NULL
    AND rate_limit_per_minute IS NULL
    AND monthly_budget_idr IS NULL
    AND last_test_at IS NULL
    AND last_test_status IS NULL
    AND last_test_message IS NULL
    AND last_test_error_category IS NULL
    AND model_params IS NULL
    AND price_input_usd_per_mtok IS NULL
    AND price_output_usd_per_mtok IS NULL
    AND price_cache_read_usd_per_mtok IS NULL
    AND price_cache_write_usd_per_mtok IS NULL
    AND price_cache_write_long_usd_per_mtok IS NULL
)"""

_PREVIOUS_CHECK_SQL = """(
    measurement_type = 'automatic'
    AND provider_type IS NOT NULL
    AND base_url IS NOT NULL
    AND model_name IS NOT NULL
    AND credential_encrypted IS NOT NULL
    AND credential_hint IS NOT NULL
    AND rate_limit_per_minute IS NOT NULL
    AND monthly_budget_idr IS NOT NULL
    AND vendor_name IS NULL
    AND site_url IS NULL
    AND access_note IS NULL
    AND manual_params IS NULL
) OR (
    measurement_type = 'manual'
    AND vendor_name IS NOT NULL
    AND site_url IS NOT NULL
    AND provider_type IS NULL
    AND base_url IS NULL
    AND model_name IS NULL
    AND credential_encrypted IS NULL
    AND credential_hint IS NULL
    AND rate_limit_per_minute IS NULL
    AND monthly_budget_idr IS NULL
    AND last_test_at IS NULL
    AND last_test_status IS NULL
    AND last_test_message IS NULL
    AND last_test_error_category IS NULL
    AND model_params IS NULL
    AND price_input_usd_per_mtok IS NULL
    AND price_output_usd_per_mtok IS NULL
)"""


def _price(table: str, name: str) -> None:
    op.add_column(table, sa.Column(name, _PRICE, nullable=True))


def upgrade() -> None:
    for name in (
        "price_cache_read_usd_per_mtok",
        "price_cache_write_usd_per_mtok",
        "price_cache_write_long_usd_per_mtok",
    ):
        _price("ai_products", name)
        _price("session_product_copies", name)
    for name in (
        "uncached_input_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "cache_write_long_tokens",
    ):
        op.add_column("call_logs", sa.Column(name, sa.Integer(), nullable=True))
    op.drop_constraint(_CHECK, "ai_products", type_="check")
    op.create_check_constraint(_CHECK, "ai_products", _CHECK_SQL)


def downgrade() -> None:
    op.drop_constraint(_CHECK, "ai_products", type_="check")
    op.create_check_constraint(_CHECK, "ai_products", _PREVIOUS_CHECK_SQL)
    for name in (
        "cache_write_long_tokens",
        "cache_write_tokens",
        "cache_read_tokens",
        "uncached_input_tokens",
    ):
        op.drop_column("call_logs", name)
    for name in (
        "price_cache_write_long_usd_per_mtok",
        "price_cache_write_usd_per_mtok",
        "price_cache_read_usd_per_mtok",
    ):
        op.drop_column("session_product_copies", name)
        op.drop_column("ai_products", name)
