"""Pembuat PDF tren skor satu produk (PBI-17 SCRUM-84 SCRUM-333, diagram D4 langkah 8).

Masukan hanya TrendRead, objek yang sama dengan response GET /reports/trend, jadi angka di
PDF selalu sama dengan halaman tren dan tidak dihitung ulang. Modul ini murni: tidak ada
database dan HTTP.

Bagian atas, format skor, dan bagian bawah sama dengan PDF laporan (SCRUM-320), supaya
kedua berkas terlihat dari pembuat yang sama. Teks dua bahasa disimpan sebagai data di
_TREND_LABELS, jadi SCRUM-355 (PBI-26) cukup mengoreksi isinya.
"""

import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.modules.reports.models import ReportLanguage, ScoreScope
from app.modules.reports.pdf import _LABELS, _WIB, DEFAULT_LOGO_PATH, format_score
from app.modules.reports.schemas import TrendRead
from app.modules.reports.trend import TrendMetric, TrendRange


@dataclass(frozen=True)
class _TrendLabels:
    title: str
    metric: str
    case_type: str
    range: str
    metrics: dict[TrendMetric, str]
    scopes: dict[ScoreScope, str]
    ranges: dict[TrendRange, str]
    columns: tuple[str, ...]
    no_points: str


_TREND_LABELS: dict[ReportLanguage, _TrendLabels] = {
    ReportLanguage.ID: _TrendLabels(
        title="Tren skor {product}",
        metric="Metrik",
        case_type="Jenis kasus",
        range="Rentang",
        metrics={
            TrendMetric.TOTAL: "Skor total",
            TrendMetric.DETERMINISTIC: "Skor deterministik",
            TrendMetric.LLM: "Skor penilai LLM",
        },
        scopes={
            ScoreScope.TEST: "Kasus test",
            ScoreScope.DEV: "Kasus dev",
            ScoreScope.DEV_TEST: "Gabungan dev + test",
        },
        ranges={
            TrendRange.ALL: "Semua sesi",
            TrendRange.THREE_MONTHS: "3 bulan terakhir",
            TrendRange.SIX_MONTHS: "6 bulan terakhir",
            TrendRange.TWELVE_MONTHS: "12 bulan terakhir",
        },
        columns=("Bulan", "Sesi", "Nilai", "Jumlah kasus", "Alasan garis putus", "Penanda"),
        no_points="Belum ada sesi layak untuk produk ini pada rentang ini.",
    ),
    ReportLanguage.EN: _TrendLabels(
        title="Score trend: {product}",
        metric="Metric",
        case_type="Case type",
        range="Range",
        metrics={
            TrendMetric.TOTAL: "Total score",
            TrendMetric.DETERMINISTIC: "Deterministic score",
            TrendMetric.LLM: "LLM judge score",
        },
        scopes={
            ScoreScope.TEST: "Test cases",
            ScoreScope.DEV: "Dev cases",
            ScoreScope.DEV_TEST: "Dev + test combined",
        },
        ranges={
            TrendRange.ALL: "All sessions",
            TrendRange.THREE_MONTHS: "Last 3 months",
            TrendRange.SIX_MONTHS: "Last 6 months",
            TrendRange.TWELVE_MONTHS: "Last 12 months",
        },
        columns=("Month", "Session", "Value", "Cases", "Line break reason", "Markers"),
        no_points="No eligible session for this product in this range.",
    ),
}


def trend_pdf_filename(
    trend: TrendRead, *, language: ReportLanguage, generated_at: datetime
) -> str:
    # Hanya id dan nilai enum: nama produk dan nama sesi diketik pengguna, jadi tidak boleh
    # masuk header Content-Disposition.
    bulan = generated_at.astimezone(_WIB).strftime("%Y-%m")
    pilihan = "-".join(
        (trend.score_scope.value, trend.metric.value, trend.range.value, language.value)
    )
    return f"tren-{trend.product_id}-{pilihan}-{bulan}.pdf"


def _baris_tren(trend: TrendRead, language: ReportLanguage) -> list[list[str]]:
    """Teks tiap baris tabel, belum di-escape; satu baris per titik, urut bulan (AC4)."""
    bulan = _LABELS[language].months
    return [
        [
            f"{bulan[titik.month.month - 1]} {titik.month.year}",
            titik.label,
            format_score(titik.value, language),
            str(titik.case_count),
            "; ".join(titik.breaks),
            "; ".join(titik.markers),
        ]
        for titik in trend.points
    ]


class TrendPdfRenderer:
    # Lebar kolom tabel dalam mm, total 277 = A4 lanskap dikurangi margin.
    _LEBAR = (30, 70, 20, 25, 66, 66)

    def __init__(self, *, compress: bool = True, logo_path: Path = DEFAULT_LOGO_PATH) -> None:
        self.compress = compress
        self.logo_path = logo_path

    def render(
        self, trend: TrendRead, *, language: ReportLanguage, generated_at: datetime
    ) -> bytes:
        label = _TREND_LABELS[language]
        styles = getSampleStyleSheet()
        judul = label.title.format(product=trend.product_name)
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=10 * mm,
            rightMargin=10 * mm,
            topMargin=10 * mm,
            bottomMargin=10 * mm,
            title=judul,
            pageCompression=1 if self.compress else 0,
        )
        isi = [*self._kepala(trend, judul, label, language, generated_at, styles)]
        isi.append(Spacer(1, 4 * mm))
        if trend.points:
            isi.append(self._tabel(trend, label, language, styles))
        else:
            isi.append(Paragraph(label.no_points, styles["Normal"]))
        isi += [Spacer(1, 4 * mm), Paragraph(_LABELS[language].contact, styles["Italic"])]
        doc.build(isi)
        return buffer.getvalue()

    def _kepala(
        self,
        trend: TrendRead,
        judul: str,
        label: _TrendLabels,
        language: ReportLanguage,
        generated_at: datetime,
        styles,
    ) -> list:
        if self.logo_path.is_file():
            logo = Image(
                str(self.logo_path),
                width=48 * mm,
                height=12 * mm,
                kind="proportional",
                mask="auto",
                hAlign="LEFT",
            )
        else:
            logo = Paragraph("<b>VERITASK</b>", styles["Heading2"])
        umum = _LABELS[language]
        dibuat = generated_at.astimezone(_WIB)
        info = [
            f"{label.metric}: {label.metrics[trend.metric]}",
            f"{label.case_type}: {label.scopes[trend.score_scope]}",
            f"{label.range}: {label.ranges[trend.range]}",
            f"{umum.created} {dibuat.day} {umum.months[dibuat.month - 1]} {dibuat.year}",
        ]
        return [
            logo,
            Paragraph(escape(judul), styles["Title"]),
            Paragraph(" · ".join(info), styles["Normal"]),
        ]

    def _tabel(self, trend: TrendRead, label: _TrendLabels, language: ReportLanguage, styles):
        sel = styles["BodyText"].clone("sel", fontSize=8, leading=10)
        data = [[Paragraph(f"<b>{judul}</b>", sel) for judul in label.columns]]
        data += [
            [Paragraph(escape(nilai), sel) for nilai in baris]
            for baris in _baris_tren(trend, language)
        ]
        tabel = Table(data, colWidths=[lebar * mm for lebar in self._LEBAR], repeatRows=1)
        tabel.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EAED")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        return tabel
