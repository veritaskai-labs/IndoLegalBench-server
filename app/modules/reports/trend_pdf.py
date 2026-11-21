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
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Line
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.modules.reports.comparison_rules import (
    JUDGE_MODEL,
    MANUAL_ANSWER_RANGE_OVER_LIMIT,
    MODEL_VERSION_DIFFERS,
    NO_SHARED_CASES,
    REPETITIONS_DIFFER,
    RUBRIC_VERSION,
    SUITE_VERSION,
    TRIAL_SESSION,
)
from app.modules.reports.models import ReportLanguage, ScoreScope
from app.modules.reports.pdf import _LABELS, _WIB, DEFAULT_LOGO_PATH, format_score
from app.modules.reports.schemas import TrendPointRead, TrendRead
from app.modules.reports.trend import TrendMetric, TrendRange

_GARIS_COLOR = colors.HexColor("#3B44C4")
_PANDUAN_COLOR = colors.HexColor("#9AA0A6")
# Di atas jumlah titik ini label bulan diselang satu, supaya tidak saling menimpa.
_LABEL_BULAN_MAKS = 12


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
    # Kode alasan putus, penanda, dan peringatan dari service tren (comparison_rules).
    # Kode yang belum ada di sini tetap dicetak apa adanya, tidak membuat PDF gagal.
    codes: dict[str, str]
    dev_banner: str
    no_target: str


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
        codes={
            RUBRIC_VERSION: "Versi rubrik berubah",
            JUDGE_MODEL: "Model penilai berubah",
            SUITE_VERSION: "Versi suite atau kasus berubah",
            MODEL_VERSION_DIFFERS: "Versi model produk berubah",
            TRIAL_SESSION: "Uji Coba",
            REPETITIONS_DIFFER: "Jumlah percobaan berbeda",
            MANUAL_ANSWER_RANGE_OVER_LIMIT: "Rentang jawaban manual lebih dari 7 hari",
            NO_SHARED_CASES: "Tidak ada kasus yang sama di antara sesi pada satu garis, "
            "jadi garis itu tidak digambar.",
        },
        dev_banner="Tren berisi skor dev, hanya untuk internal dan tidak diterbitkan.",
        no_target="Tanpa garis target.",
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
        codes={
            RUBRIC_VERSION: "Rubric version changed",
            JUDGE_MODEL: "Judge model changed",
            SUITE_VERSION: "Suite or case version changed",
            MODEL_VERSION_DIFFERS: "Product model version changed",
            TRIAL_SESSION: "Trial",
            REPETITIONS_DIFFER: "Different number of attempts",
            MANUAL_ANSWER_RANGE_OVER_LIMIT: "Manual answers span more than 7 days",
            NO_SHARED_CASES: "No case is shared by the sessions of a line, "
            "so that line is not drawn.",
        },
        dev_banner="This trend contains dev scores. Internal only, it is never published.",
        no_target="No target line.",
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


def _teks_kode(kode: list[str], label: _TrendLabels) -> str:
    return "; ".join(label.codes.get(satu, satu) for satu in kode)


def _baris_tren(trend: TrendRead, language: ReportLanguage) -> list[list[str]]:
    """Teks tiap baris tabel, belum di-escape; satu baris per titik, urut bulan (AC4)."""
    bulan = _LABELS[language].months
    label = _TREND_LABELS[language]
    return [
        [
            f"{bulan[titik.month.month - 1]} {titik.month.year}",
            titik.label,
            format_score(titik.value, language),
            str(titik.case_count),
            _teks_kode(titik.breaks, label),
            _teks_kode(titik.markers, label),
        ]
        for titik in trend.points
    ]


def _indeks_bulan(bulan: date) -> int:
    return bulan.year * 12 + bulan.month - 1


def _segmen(points: list[TrendPointRead], awal: int) -> list[list[tuple[int, float]]]:
    """Satu seri per potongan garis (AC5, AC11).

    Potongan baru dimulai di titik yang punya alasan putus dan di sekitar nilai kosong:
    nilai kosong tidak digambar (bukan 0) dan garis tidak boleh melompatinya.
    """
    seri: list[list[tuple[int, float]]] = []
    sambung = False
    for titik in points:
        if titik.value is None:
            sambung = False
            continue
        posisi = (_indeks_bulan(titik.month) - awal, float(titik.value))
        if sambung and not titik.breaks:
            seri[-1].append(posisi)
        else:
            seri.append([posisi])
        sambung = True
    return seri


def _grafik_garis(trend: TrendRead, language: ReportLanguage) -> Drawing | None:
    """Grafik garis satu metrik; None bila tidak ada nilai yang bisa digambar (AC11).

    Sumbu x adalah bulan sebenarnya, jadi bulan tanpa sesi menjadi celah (D4). Tidak ada
    garis target (AC7); satu-satunya garis lain adalah garis bantu putus-putus di titik
    perubahan versi.
    """
    if not trend.points:
        return None
    awal = _indeks_bulan(trend.points[0].month)
    seri = _segmen(trend.points, awal)
    if not seri:
        return None
    nama_bulan = _LABELS[language].months
    langkah = [_indeks_bulan(titik.month) - awal for titik in trend.points]
    selang = 2 if len(langkah) > _LABEL_BULAN_MAKS else 1
    label_bulan = {
        x: f"{nama_bulan[titik.month.month - 1][:3]} {titik.month.year % 100:02d}"
        if urutan % selang == 0
        else ""
        for urutan, (x, titik) in enumerate(zip(langkah, trend.points, strict=True))
    }

    gambar = Drawing(277 * mm, 72 * mm)
    plot = LinePlot()
    plot.x, plot.y, plot.width, plot.height = 15 * mm, 12 * mm, 240 * mm, 55 * mm
    plot.data = seri
    plot.xValueAxis.valueMin = -0.5
    plot.xValueAxis.valueMax = langkah[-1] + 0.5
    plot.xValueAxis.valueSteps = langkah
    plot.xValueAxis.labelTextFormat = lambda x: label_bulan.get(round(x), "")
    plot.yValueAxis.valueMin = 0
    plot.yValueAxis.valueMax = 1
    plot.yValueAxis.valueSteps = [0, 0.25, 0.5, 0.75, 1]
    plot.yValueAxis.labelTextFormat = lambda y: format_score(Decimal(str(y)), language)
    for axis in (plot.xValueAxis, plot.yValueAxis):
        axis.labels.fontName = "Helvetica"
        axis.labels.fontSize = 7
    for urutan in range(len(seri)):
        plot.lines[urutan].strokeColor = _GARIS_COLOR
        plot.lines[urutan].strokeWidth = 1.5
        plot.lines[urutan].symbol = makeMarker("FilledCircle", size=4)
    gambar.add(plot)

    lebar_bulan = plot.width / (plot.xValueAxis.valueMax - plot.xValueAxis.valueMin)
    for x, titik in zip(langkah, trend.points, strict=True):
        if titik.breaks:
            px = plot.x + (x - plot.xValueAxis.valueMin) * lebar_bulan
            gambar.add(
                Line(
                    px,
                    plot.y,
                    px,
                    plot.y + plot.height,
                    strokeColor=_PANDUAN_COLOR,
                    strokeDashArray=[3, 2],
                )
            )
    return gambar


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
        grafik = _grafik_garis(trend, language)
        if grafik is not None:
            isi += [grafik, Spacer(1, 3 * mm)]
        if trend.points:
            isi.append(self._tabel(trend, label, language, styles))
        else:
            isi.append(Paragraph(label.no_points, styles["Normal"]))
        for peringatan in trend.warnings:
            teks = escape(label.codes.get(peringatan, peringatan))
            isi.append(Paragraph(f"<b>{teks}</b>", styles["Normal"]))
        isi.append(Paragraph(label.no_target, styles["Normal"]))
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
        kepala = [
            logo,
            Paragraph(escape(judul), styles["Title"]),
            Paragraph(" · ".join(info), styles["Normal"]),
        ]
        # AC12: tren yang memuat skor dev hanya untuk internal (tren tidak pernah diterbitkan).
        if trend.score_scope is not ScoreScope.TEST:
            kepala.append(Paragraph(f"<b>{label.dev_banner}</b>", styles["Normal"]))
        return kepala

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
