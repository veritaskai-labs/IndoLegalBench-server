"""Pembuat PDF laporan perbandingan (PBI-15 SCRUM-320, diagram D3 langkah 15).

Masukan hanya ReportDetail, yaitu hasil beku dari tabel D1, jadi PDF
selalu sama dengan laporan yang dibuka di web dan tidak menghitung skor
ulang. Modul ini murni: tidak ada database dan HTTP.

Isi PDF mengikuti tampilan web (SCRUM-322): logo Veritask dan judul,
tabel peringkat, grafik batang, label otomatis/manual, penanda D2, versi
kasus, rubrik, dan model. Skor deterministik dan skor penilai LLM tetap
dua kolom dan dua batang terpisah; total_score hanya dipakai untuk urutan
(keputusan SA di D1), jadi tidak dicetak.

Teks mengikuti reports.language. Label dua bahasa disimpan sebagai data
di _LABELS supaya SCRUM-355 (PBI-26) cukup menambah atau mengoreksi isi,
bukan mengubah alur render.
"""

import io
from dataclasses import dataclass
from datetime import timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.textlabels import NA_Label
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.modules.reports.models import (
    MeasurementKind,
    ProductLabelMode,
    ReportLanguage,
    ReportMarkerCode,
    ScoreSplit,
)
from app.modules.reports.schemas import ReportDetail, ReportMarker, ReportSessionRead

# Wordmark resmi Veritask (PNG transparan, rasio 4:1) untuk bagian atas laporan (AC tampilan).
# Bila berkas hilang, bagian atas memakai tulisan VERITASK supaya PDF tetap bisa diunduh.
DEFAULT_LOGO_PATH = Path(__file__).parent / "assets" / "veritask-logo.png"
CONTACT_EMAIL = "indolegalbench@veritask.ai"
_WIB = timezone(timedelta(hours=7))
_CENT = Decimal("0.01")
_KOSONG = "–"
_DET_COLOR = colors.HexColor("#3B44C4")
_LLM_COLOR = colors.HexColor("#147D7A")
# Nama produk di bawah grafik dipotong dan dimiringkan supaya tidak saling tumpang tindih
# kalau produknya banyak (review #145). Nama lengkap tetap ada di tabel peringkat.
_GRAFIK_LABEL_MAKS = 18
_GRAFIK_MIRING_MULAI = 6


@dataclass(frozen=True)
class _Labels:
    automatic: str
    manual: str
    not_applicable: str
    hidden: str
    rubric: str
    judge_model: str
    case_basis: str
    case_versions: str
    created: str
    dev_banner: str
    no_results: str
    contact: str
    columns: tuple[str, ...]
    deterministic: str
    llm: str
    splits: dict[ScoreSplit, str]
    trial: str
    # Kunci dari ReportMarkerCode (SCRUM-300); test memastikan tiap kode punya label.
    markers: dict[ReportMarkerCode, str]
    months: tuple[str, ...]


_LABELS: dict[ReportLanguage, _Labels] = {
    ReportLanguage.ID: _Labels(
        automatic="Otomatis",
        manual="Manual",
        not_applicable="Tidak berlaku",
        hidden="Disamarkan",
        rubric="Rubrik",
        judge_model="Model penilai",
        case_basis="Jumlah kasus dasar perbandingan",
        case_versions="Versi kasus",
        created="Dibuat",
        dev_banner="Laporan berisi skor dev, hanya untuk internal dan tidak dapat diterbitkan.",
        no_results="Tidak ada hasil untuk laporan ini.",
        contact=f"Untuk mendapatkan isi kasus, kirim email ke {CONTACT_EMAIL}.",
        columns=(
            "Peringkat",
            "Produk",
            "Jenis",
            "Skor deterministik",
            "Skor penilai LLM",
            "Kasus dinilai",
            "Kasus dasar",
            "Tingkat kegagalan",
            "Percobaan",
            "Versi model",
            "Penanda",
        ),
        deterministic="Skor deterministik",
        llm="Skor penilai LLM",
        splits={
            ScoreSplit.TEST: "Kasus test",
            ScoreSplit.DEV: "Kasus dev",
            ScoreSplit.COMBINED: "Gabungan dev + test",
        },
        trial="Uji Coba",
        markers={
            ReportMarkerCode.MODEL_VERSION_DIFFERS: "Versi model berbeda",
            ReportMarkerCode.REPETITIONS_DIFFER: "Jumlah percobaan berbeda",
            ReportMarkerCode.MANUAL_DATE_SPAN_OVER_7_DAYS: "Rentang jawaban manual lebih dari 7 hari",
            ReportMarkerCode.REFUSALS: "Menolak menjawab",
        },
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
    ReportLanguage.EN: _Labels(
        automatic="Automatic",
        manual="Manual",
        not_applicable="Not applicable",
        hidden="Hidden",
        rubric="Rubric",
        judge_model="Judge model",
        case_basis="Cases used for comparison",
        case_versions="Case versions",
        created="Created",
        dev_banner="This report contains dev scores. Internal only, it cannot be published.",
        no_results="This report has no results.",
        contact=f"To get the case contents, email {CONTACT_EMAIL}.",
        columns=(
            "Rank",
            "Product",
            "Type",
            "Deterministic score",
            "LLM judge score",
            "Cases graded",
            "Basis cases",
            "Failure rate",
            "Attempts",
            "Model version",
            "Markers",
        ),
        deterministic="Deterministic score",
        llm="LLM judge score",
        splits={
            ScoreSplit.TEST: "Test cases",
            ScoreSplit.DEV: "Dev cases",
            ScoreSplit.COMBINED: "Dev + test combined",
        },
        trial="Trial",
        markers={
            ReportMarkerCode.MODEL_VERSION_DIFFERS: "Different model version",
            ReportMarkerCode.REPETITIONS_DIFFER: "Different number of attempts",
            ReportMarkerCode.MANUAL_DATE_SPAN_OVER_7_DAYS: "Manual answers span more than 7 days",
            ReportMarkerCode.REFUSALS: "Refused to answer",
        },
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
        return _KOSONG
    teks = f"{value.quantize(_CENT, rounding=ROUND_HALF_UP):.2f}"
    return teks.replace(".", ",") if language is ReportLanguage.ID else teks


def report_pdf_filename(report: ReportDetail) -> str:
    # Judul diketik pengguna, jadi tidak dipakai di header Content-Disposition.
    return f"laporan-{report.id}.pdf"


class ReportPdfRenderer:
    # Lebar kolom tabel peringkat dalam mm, total 277 = A4 lanskap dikurangi margin.
    _LEBAR = (18, 34, 18, 20, 20, 16, 16, 20, 18, 43, 54)

    def __init__(self, *, compress: bool = True, logo_path: Path = DEFAULT_LOGO_PATH) -> None:
        self.compress = compress
        self.logo_path = logo_path

    def render(self, report: ReportDetail) -> bytes:
        label = _LABELS[report.language]
        styles = getSampleStyleSheet()
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=10 * mm,
            rightMargin=10 * mm,
            topMargin=10 * mm,
            bottomMargin=10 * mm,
            title=report.title,
            pageCompression=1 if self.compress else 0,
        )
        isi = [*self._kepala(report, label, styles), Spacer(1, 4 * mm)]
        splits = [s for s in ScoreSplit if any(_hasil(sesi, s) for sesi in report.sessions)]
        if not splits:
            isi.append(Paragraph(label.no_results, styles["Normal"]))
        for split in splits:
            isi += self._bagian(report, split, label, styles)
        if report.case_basis:
            versi = ", ".join(
                f"{escape(kasus.case_code)} v{kasus.case_version_no}" for kasus in report.case_basis
            )
            isi.append(Paragraph(f"<b>{label.case_versions}:</b> {versi}", styles["BodyText"]))
        isi += [Spacer(1, 4 * mm), Paragraph(label.contact, styles["Italic"])]
        doc.build(isi)
        return buffer.getvalue()

    def _kepala(self, report: ReportDetail, label: _Labels, styles) -> list:
        if self.logo_path.is_file():
            # proportional: logo dimuat di kotak 48x12 mm tanpa melar; mask auto menjaga transparansi.
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
        dibuat = report.created_at.astimezone(_WIB)
        tanggal = f"{dibuat.day} {label.months[dibuat.month - 1]} {dibuat.year}"
        info = [
            f"{label.created} {tanggal}",
            f"{label.rubric} v{report.rubric_version_no}" if report.rubric_version_no else None,
            f"{label.judge_model}: {escape(report.judge_model_name)}"
            if report.judge_model_name
            else None,
            f"{label.case_basis}: {report.case_basis_count}",
        ]
        kepala = [logo, Paragraph(escape(report.title), styles["Title"])]
        kepala.append(Paragraph(" · ".join(i for i in info if i), styles["Normal"]))
        if report.has_dev_scores:
            kepala.append(Paragraph(f"<b>{label.dev_banner}</b>", styles["Normal"]))
        return kepala

    def _bagian(self, report: ReportDetail, split: ScoreSplit, label: _Labels, styles) -> list:
        sel = styles["BodyText"].clone("sel", fontSize=7, leading=8.5)
        baris = _baris_peringkat(report, split)
        data = [[Paragraph(f"<b>{judul}</b>", sel) for judul in label.columns]]
        for sesi, hasil in baris:
            manual = sesi.measurement_kind is MeasurementKind.MANUAL
            if manual:
                gagal = label.not_applicable
            elif hasil.failure_rate is None:
                gagal = _KOSONG
            else:
                gagal = f"{format_score(hasil.failure_rate, report.language)}%"
            kolom = [
                str(hasil.rank) if hasil.rank is not None else _KOSONG,
                escape(sesi.product_label),
                label.manual if manual else label.automatic,
                format_score(hasil.det_score, report.language),
                format_score(hasil.llm_score, report.language),
                str(hasil.cases_graded_count),
                str(hasil.cases_basis_count),
                gagal,
                str(hasil.repetitions),
                _versi_model(report, hasil, label),
                "; ".join(_penanda_sesi(sesi, label)),
            ]
            data.append([Paragraph(nilai, sel) for nilai in kolom])
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
        judul = Paragraph(label.splits[split], styles["Heading2"])
        grafik = _grafik_batang(
            [sesi for sesi, _ in baris], [h for _, h in baris], label, report.language
        )
        return [judul, tabel, Spacer(1, 3 * mm), grafik, Spacer(1, 4 * mm)]


def _hasil(sesi: ReportSessionRead, split: ScoreSplit):
    return next((hasil for hasil in sesi.results if hasil.score_split is split), None)


def _baris_peringkat(report: ReportDetail, split: ScoreSplit) -> list:
    """Pasangan (sesi, hasil) untuk satu jenis nilai, urut peringkat; tanpa peringkat di akhir."""
    pasangan = [(sesi, _hasil(sesi, split)) for sesi in report.sessions]
    return sorted(
        ((sesi, hasil) for sesi, hasil in pasangan if hasil is not None),
        key=lambda baris: (baris[1].rank is None, baris[1].rank or 0),
    )


def _penanda_sesi(sesi: ReportSessionRead, label: _Labels) -> list[str]:
    # Uji Coba dari kolom is_trial (SCRUM-300), lalu penanda D2 dari markers.
    uji_coba = [label.trial] if sesi.is_trial else []
    return uji_coba + [_penanda(marker, label) for marker in sesi.markers]


def _penanda(marker: ReportMarker, label: _Labels) -> str:
    # code bertipe ReportMarkerCode dan test memastikan semua kode punya label.
    teks = escape(label.markers[marker.code])
    return f"{teks} ({escape(marker.detail)})" if marker.detail else teks


def _versi_model(report: ReportDetail, hasil, label: _Labels) -> str:
    # Mode kode (AC9): service sudah mengosongkan versinya; "Disamarkan" membedakannya dari
    # versi yang memang tidak tercatat.
    if report.product_label_mode is ProductLabelMode.CODE:
        return label.hidden
    return escape(hasil.product_model_version or _KOSONG)


def _nilai_grafik(nilai: Decimal | None) -> float | None:
    # Skor kosong tetap None: tidak digambar sebagai batang 0 (review #145), tetapi diberi
    # tanda "–" di posisinya, sama dengan tabel.
    return None if nilai is None else float(nilai)


def _nama_grafik(nama: str) -> str:
    return nama if len(nama) <= _GRAFIK_LABEL_MAKS else f"{nama[: _GRAFIK_LABEL_MAKS - 1]}…"


def _grafik_batang(
    sesi: list[ReportSessionRead], hasil: list, label: _Labels, language: ReportLanguage
) -> Drawing:
    """Dua batang per produk: deterministik dan LLM, tidak digabung (AC skor)."""
    det = [_nilai_grafik(h.det_score) for h in hasil]
    llm = [_nilai_grafik(h.llm_score) for h in hasil]
    miring = len(sesi) > _GRAFIK_MIRING_MULAI
    gambar = Drawing(277 * mm, 66 * mm if miring else 60 * mm)
    grafik = VerticalBarChart()
    grafik.x, grafik.y, grafik.width, grafik.height = (
        15 * mm,
        18 * mm if miring else 12 * mm,
        210 * mm,
        44 * mm,
    )
    grafik.data = [det or [0], llm or [0]]
    grafik.valueAxis.valueMin = 0
    grafik.valueAxis.valueMax = max([1.0, *(n for n in det + llm if n is not None)])
    grafik.naLabel = NA_Label()
    grafik.naLabel.text = _KOSONG
    grafik.naLabel.fontName = "Helvetica"
    grafik.categoryAxis.categoryNames = [_nama_grafik(s.product_label) for s in sesi] or [""]
    if miring:
        grafik.categoryAxis.labels.angle = 30
        grafik.categoryAxis.labels.boxAnchor = "ne"
    # Angka sumbu ikut pemisah desimal bahasa laporan, sama dengan tabel.
    grafik.valueAxis.labelTextFormat = lambda nilai: format_score(Decimal(str(nilai)), language)
    grafik.valueAxis.labels.fontName = grafik.categoryAxis.labels.fontName = "Helvetica"
    grafik.categoryAxis.labels.fontSize = 6
    grafik.bars[0].fillColor = _DET_COLOR
    grafik.bars[1].fillColor = _LLM_COLOR
    legenda = Legend()
    legenda.fontName = "Helvetica"
    legenda.x, legenda.y = 232 * mm, grafik.y + 38 * mm
    legenda.colorNamePairs = [(_DET_COLOR, label.deterministic), (_LLM_COLOR, label.llm)]
    gambar.add(grafik)
    gambar.add(legenda)
    return gambar
