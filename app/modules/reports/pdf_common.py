"""Bagian yang sama di semua PDF modul reports (PBI-15 SCRUM-320, PBI-17 SCRUM-333).

Logo Veritask, kertas A4 lanskap, tanggal dibuat dalam WIB, format skor, nama bulan, dan
kalimat permintaan isi kasus. PDF laporan (pdf.py) dan PDF tren (trend_pdf.py) memakai
modul ini, jadi kedua berkas tampak dari pembuat yang sama dan bahasa (SCRUM-355, PBI-26)
cukup dikoreksi di satu tempat. PDF baru menambah renderer sendiri tanpa mengubah yang lama.
Modul ini murni: tidak ada database dan HTTP.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import BinaryIO

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Image, Paragraph, SimpleDocTemplate

from app.modules.reports.models import ReportLanguage
from app.modules.reports.wib import WIB

# Wordmark resmi Veritask (PNG transparan, rasio 4:1) untuk bagian atas PDF.
# Bila berkas hilang, bagian atas memakai tulisan VERITASK supaya PDF tetap bisa diunduh.
DEFAULT_LOGO_PATH = Path(__file__).parent / "assets" / "veritask-logo.png"
CONTACT_EMAIL = "indolegalbench@veritask.ai"
KOSONG = "–"
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class CommonLabels:
    created: str
    contact: str
    months: tuple[str, ...]


COMMON_LABELS: dict[ReportLanguage, CommonLabels] = {
    ReportLanguage.ID: CommonLabels(
        created="Dibuat",
        contact=f"Untuk mendapatkan isi kasus, kirim email ke {CONTACT_EMAIL}.",
        months=(
            "Januari",
            "Februari",
            "Maret",
            "April",
            "Mei",
            "Juni",
            "Juli",
            "Agustus",
            "September",
            "Oktober",
            "November",
            "Desember",
        ),
    ),
    ReportLanguage.EN: CommonLabels(
        created="Created",
        contact=f"To get the case contents, email {CONTACT_EMAIL}.",
        months=(
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
    ),
}


def format_score(value: Decimal | None, language: ReportLanguage) -> str:
    """Dua desimal tanpa persen (AC skor). Pembulatan sama dengan score_aggregation."""
    if value is None:
        return KOSONG
    teks = f"{value.quantize(_CENT, rounding=ROUND_HALF_UP):.2f}"
    return teks.replace(".", ",") if language is ReportLanguage.ID else teks


def created_text(moment: datetime, language: ReportLanguage) -> str:
    """'Dibuat 21 November 2026', tanggalnya dalam WIB."""
    label = COMMON_LABELS[language]
    wib = moment.astimezone(WIB)
    return f"{label.created} {wib.day} {label.months[wib.month - 1]} {wib.year}"


def month_year(month: date, language: ReportLanguage) -> str:
    """'Oktober 2026'."""
    return f"{COMMON_LABELS[language].months[month.month - 1]} {month.year}"


def short_month(month: date, language: ReportLanguage) -> str:
    """'Okt 26', untuk label sumbu grafik."""
    return f"{COMMON_LABELS[language].months[month.month - 1][:3]} {month.year % 100:02d}"


def logo(logo_path: Path, styles) -> Flowable:
    if logo_path.is_file():
        # proportional: logo dimuat di kotak 48x12 mm tanpa melar; mask auto menjaga transparansi.
        return Image(
            str(logo_path),
            width=48 * mm,
            height=12 * mm,
            kind="proportional",
            mask="auto",
            hAlign="LEFT",
        )
    return Paragraph("<b>VERITASK</b>", styles["Heading2"])


def contact(language: ReportLanguage, styles) -> Paragraph:
    return Paragraph(COMMON_LABELS[language].contact, styles["Italic"])


def landscape_document(buffer: BinaryIO, *, title: str, compress: bool) -> SimpleDocTemplate:
    """A4 lanskap dengan margin 10 mm; lebar isi 277 mm."""
    return SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=title,
        pageCompression=1 if compress else 0,
    )
