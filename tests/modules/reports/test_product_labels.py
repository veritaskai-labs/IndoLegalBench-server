"""Label produk di laporan. PBI-15 SCRUM-79 SCRUM-313, AC9. Murni, tanpa database."""

import pytest

from app.modules.reports.models import ProductLabelMode
from app.modules.reports.product_labels import product_label


class TestRealName:
    def test_keeps_the_name_exactly_as_the_product_writes_it(self):
        # Act
        label = product_label(ProductLabelMode.REAL_NAME, "AiYU", 3)

        # Assert
        assert label == "AiYU"

    def test_does_not_change_case_or_spacing(self):
        # Act
        label = product_label(ProductLabelMode.REAL_NAME, "Legal Hero ", 0)

        # Assert
        assert label == "Legal Hero "


class TestCode:
    @pytest.mark.parametrize(
        ("index", "diharapkan"),
        [
            (0, "Product A"),
            (1, "Product B"),
            (25, "Product Z"),
            (26, "Product AA"),
            (27, "Product AB"),
            (51, "Product AZ"),
            (52, "Product BA"),
            (701, "Product ZZ"),
            (702, "Product AAA"),
        ],
    )
    def test_counts_in_letters_like_a_spreadsheet_column(self, index, diharapkan):
        # Act
        label = product_label(ProductLabelMode.CODE, "AiYU", index)

        # Assert
        assert label == diharapkan

    def test_never_contains_the_real_name(self):
        # Act
        label = product_label(ProductLabelMode.CODE, "Justisio", 4)

        # Assert
        assert "Justisio" not in label

    def test_a_negative_position_is_rejected(self):
        # Act and Assert
        with pytest.raises(ValueError):
            product_label(ProductLabelMode.CODE, "AiYU", -1)
