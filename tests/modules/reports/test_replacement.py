"""Aturan himpunan produk untuk ganti versi laporan terbit. PBI-17 AC9, SCRUM-371.

Murni, tanpa database: positif, negatif, dan edge untuk "himpunan produk sama persis".
"""

import uuid

import pytest

from app.modules.reports.replacement import (
    REPORT_PRODUCTS_MISMATCH,
    ensure_same_products,
    same_product_set,
)
from app.shared.exceptions import ConflictError

A = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
B = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
C = uuid.UUID("cccccccc-0000-0000-0000-000000000003")


class TestSameProductSet:
    def test_the_same_products_in_the_same_order_match(self):
        assert same_product_set([A, B], [A, B]) is True

    def test_the_order_of_the_products_does_not_matter(self):
        assert same_product_set([A, B], [B, A]) is True

    def test_three_products_in_any_order_match(self):
        assert same_product_set([A, B, C], [C, A, B]) is True

    def test_a_superset_does_not_match(self):
        # A vs B vs C bukan pengganti A vs B
        assert same_product_set([A, B, C], [A, B]) is False

    def test_a_subset_does_not_match(self):
        assert same_product_set([A, B], [A, B, C]) is False

    def test_one_different_product_does_not_match(self):
        assert same_product_set([A, B], [A, C]) is False

    def test_disjoint_products_do_not_match(self):
        assert same_product_set([A], [B, C]) is False

    def test_an_empty_side_only_matches_another_empty_side(self):
        # Laporan selalu punya produk; fungsinya tetap harus total.
        assert same_product_set([], []) is True
        assert same_product_set([], [A]) is False

    def test_a_repeated_id_is_read_as_a_set(self):
        assert same_product_set([A, A, B], [B, A]) is True

    def test_any_iterable_is_accepted_and_read_once(self):
        assert same_product_set(iter([A, B]), (product for product in (B, A))) is True


class TestEnsureSameProducts:
    def test_the_same_products_pass_without_error(self):
        assert ensure_same_products([B, A], [A, B]) is None

    @pytest.mark.parametrize(
        ("replacement", "replaced"),
        [([A, B, C], [A, B]), ([A], [A, B]), ([A, C], [A, B])],
        ids=["superset", "subset", "other-product"],
    )
    def test_other_products_are_refused_with_409_and_a_clear_reason(self, replacement, replaced):
        with pytest.raises(ConflictError) as caught:
            ensure_same_products(replacement, replaced)

        assert caught.value.status_code == 409
        assert caught.value.code == REPORT_PRODUCTS_MISMATCH
        assert "produk yang sama" in caught.value.message
