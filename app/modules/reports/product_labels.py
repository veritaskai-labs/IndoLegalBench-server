"""Label produk di laporan: nama asli atau kode. PBI-15 SCRUM-79 SCRUM-313, AC9.

Murni, tanpa database dan tanpa HTTP.
"""

from app.modules.reports.models import ProductLabelMode


def product_label(mode: ProductLabelMode, name: str, index: int) -> str:
    raise NotImplementedError
