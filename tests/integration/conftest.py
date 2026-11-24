"""Fixture bersama tes integrasi PostgreSQL.

dunia (QA PBI-16 SCRUM-330: sesi otomatis dan manual yang sudah dinilai, di database salinan
per tes) beserta db_qa dan templat_qa ditulis di tests/drilldown_qa_pg.py.
dunia_tren (QA PBI-17 SCRUM-336: pengguna, rubrik, penilai, suite, dan produk, tanpa sesi,
di database salinan per tes) beserta db_tren dan templat_tren ditulis di tests/trend_qa_pg.py.
"""

from tests.drilldown_qa_pg import db_qa, dunia, templat_qa  # noqa: F401
from tests.trend_qa_pg import db_tren, dunia_tren, templat_tren  # noqa: F401
