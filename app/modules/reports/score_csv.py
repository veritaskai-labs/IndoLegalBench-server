"""Isi berkas CSV skor satu sesi pengukuran. PBI-16 SCRUM-83 SCRUM-327.

Satu sesi menghasilkan satu berkas: tiap baris satu kasus dengan produk, skor jalur
deterministik dan skor penilai LLM (terpisah, dua desimal), jenis pengukuran, dan catatan
versi kasus, rubrik, serta model (AC5). Jawaban asli dan alasan penilaian tidak pernah
masuk berkas ini. Catatan sesi diulang di setiap baris supaya tetap ikut saat berkasnya
disaring atau diurutkan di spreadsheet. Sesi Uji Coba bertanda di setiap baris (D2).

Modul ini murni: hanya menyusun byte CSV dari data yang sudah disaring pemanggil.
Kelayakan sesi, isi per peran, dan penyimpanan berkas ada di score_files.
"""

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.modules.reports.models import MeasurementKind

_CENT = Decimal("0.01")

# Karakter yang membuat spreadsheet membaca sel sebagai formula (CSV injection). Usulan
# teknis menyebut = + - @; tab dan carriage return ikut, sama dengan audit/export.py.
_AWAL_FORMULA = ("=", "+", "-", "@", "\t", "\r")

HEADER = [
    "Kasus",
    "Versi Kasus",
    "Produk",
    "Jenis Pengukuran",
    "Skor Deterministik",
    "Skor LLM",
    "Versi Rubrik",
    "Versi Model Produk",
    "Model Penilai",
    "Uji Coba",
]


@dataclass(frozen=True)
class ScoreCsvSession:
    """Catatan satu sesi yang sama untuk semua barisnya. String kosong = tidak diketahui."""

    product_name: str
    measurement_kind: MeasurementKind
    rubric_version: str
    product_model_version: str
    judge_model_version: str
    trial: bool


@dataclass(frozen=True)
class ScoreCsvRow:
    """Satu kasus yang dinilai. Skor None berarti jalur itu belum punya skor, bukan nol."""

    case_code: str
    case_version: str
    det_score: Decimal | None
    llm_score: Decimal | None


def neutralize_cell(value: str) -> str:
    """Awali dengan tanda kutip sel yang akan dibaca spreadsheet sebagai formula."""
    return "'" + value if value.startswith(_AWAL_FORMULA) else value


def _skor(value: Decimal | None) -> str:
    """Dua desimal, dibulatkan setengah ke atas seperti tampilan skor lainnya."""
    if value is None:
        return ""
    return str(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def _sel(row: ScoreCsvRow, session: ScoreCsvSession) -> list[str]:
    """Satu baris berkas, urutannya sama dengan HEADER, setiap sel sudah dinetralkan."""
    nilai = [
        row.case_code,
        row.case_version,
        session.product_name,
        MeasurementKind(session.measurement_kind).value,
        _skor(row.det_score),
        _skor(row.llm_score),
        session.rubric_version,
        session.product_model_version,
        session.judge_model_version,
        "ya" if session.trial else "tidak",
    ]
    return [neutralize_cell(sel) for sel in nilai]


def render_score_csv(session: ScoreCsvSession, rows: Sequence[ScoreCsvRow]) -> bytes:
    """Byte berkas CSV: header lalu satu baris per kasus, UTF-8 dengan BOM untuk Excel."""
    buffer = io.StringIO()
    penulis = csv.writer(buffer)
    penulis.writerow(HEADER)
    for row in rows:
        penulis.writerow(_sel(row, session))
    return buffer.getvalue().encode("utf-8-sig")
