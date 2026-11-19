"""Label produk di laporan: nama asli atau kode. PBI-15 SCRUM-79 SCRUM-313, AC9.

Murni, tanpa database dan tanpa HTTP.
"""

import string

from app.modules.reports.models import ProductLabelMode

_LETTERS = string.ascii_uppercase


def product_label(mode: ProductLabelMode, name: str, index: int) -> str:
    """Nama asli apa adanya, atau kode menurut urutan produk di laporan (0 jadi Product A)."""
    if mode is ProductLabelMode.REAL_NAME:
        return name
    return f"Product {_column_letters(index)}"


def _column_letters(index: int) -> str:
    """0 jadi A, 25 jadi Z, 26 jadi AA: tanpa batas jumlah produk (AC1)."""
    if index < 0:
        raise ValueError(f"urutan produk tidak boleh negatif: {index}")
    letters = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, len(_LETTERS))
        letters = _LETTERS[remainder] + letters
    return letters
