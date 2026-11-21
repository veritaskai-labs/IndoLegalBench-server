"""Pembuat PDF tren skor satu produk (PBI-17 SCRUM-84 SCRUM-333, diagram D4 langkah 8).

Masukan hanya TrendRead, objek yang sama dengan response GET /reports/trend, jadi angka di
PDF selalu sama dengan halaman tren dan tidak dihitung ulang. Modul ini murni: tidak ada
database dan HTTP.
"""

from datetime import datetime
from pathlib import Path

from app.modules.reports.models import ReportLanguage
from app.modules.reports.pdf import DEFAULT_LOGO_PATH
from app.modules.reports.schemas import TrendRead


def trend_pdf_filename(
    trend: TrendRead, *, language: ReportLanguage, generated_at: datetime
) -> str:
    raise NotImplementedError


def _baris_tren(trend: TrendRead, language: ReportLanguage) -> list[list[str]]:
    raise NotImplementedError


class TrendPdfRenderer:
    def __init__(self, *, compress: bool = True, logo_path: Path = DEFAULT_LOGO_PATH) -> None:
        self.compress = compress
        self.logo_path = logo_path

    def render(
        self, trend: TrendRead, *, language: ReportLanguage, generated_at: datetime
    ) -> bytes:
        raise NotImplementedError
