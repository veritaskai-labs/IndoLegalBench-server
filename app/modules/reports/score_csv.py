"""Isi berkas CSV skor satu sesi pengukuran. PBI-16 SCRUM-83 SCRUM-327.

Satu sesi menghasilkan satu berkas: tiap baris satu kasus dengan produk, skor jalur
deterministik dan skor penilai LLM (terpisah, dua desimal), jenis pengukuran, dan catatan
versi kasus, rubrik, serta model (AC5). Jawaban asli dan alasan penilaian tidak pernah
masuk berkas ini. Catatan sesi diulang di setiap baris supaya tetap ikut saat berkasnya
disaring atau diurutkan di spreadsheet. Sesi Uji Coba bertanda di setiap baris (D2).
Kasus yang tidak punya skor (misalnya gagal teknis) tetap tertulis dengan status
"tidak dinilai" dan skor kosong; versi yang tidak diketahui tertulis "tidak tercatat",
supaya sel kosong tidak disalahartikan di laporan luar (review PR #152).

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
# teknis menyebut = + - @. Spreadsheet juga mengabaikan spasi dan baris baru di depan,
# jadi formula di baliknya ikut dinetralkan; sel yang diawali tab, CR, atau LF selalu
# dinetralkan (sama dengan audit/export.py untuk tab dan CR).
_AWAL_FORMULA = ("=", "+", "-", "@")
_AWAL_KONTROL = ("\t", "\r", "\n")
_SPASI_DEPAN = " \t\r\n"

TIDAK_TERCATAT = "tidak tercatat"
_DINILAI = "dinilai"
_TIDAK_DINILAI = "tidak dinilai"

HEADER = [
    "Kasus",
    "Versi Kasus",
    "Produk",
    "Jenis Pengukuran",
    "Status Penilaian",
    "Skor Deterministik",
    "Skor LLM",
    "Versi Rubrik",
    "Versi Model Produk",
    "Model Penilai",
    "Uji Coba",
]


@dataclass(frozen=True)
class ScoreCsvSession:
    """Catatan satu sesi yang sama untuk semua barisnya. String kosong = tidak tercatat."""

    product_name: str
    measurement_kind: MeasurementKind
    rubric_version: str
    product_model_version: str
    judge_model_version: str
    trial: bool


@dataclass(frozen=True)
class ScoreCsvRow:
    """Satu kasus sesi. Skor None berarti jalur itu belum punya skor, bukan nol.

    graded False berarti kasus ini tidak punya skor sama sekali (misalnya gagal teknis).
    """

    case_code: str
    case_version: str
    det_score: Decimal | None
    llm_score: Decimal | None
    graded: bool


def neutralize_cell(value: str) -> str:
    """Awali dengan tanda kutip sel yang akan dibaca spreadsheet sebagai formula."""
    berbahaya = value.startswith(_AWAL_KONTROL) or value.lstrip(_SPASI_DEPAN).startswith(
        _AWAL_FORMULA
    )
    return "'" + value if berbahaya else value


def _atau_tidak_tercatat(value: str) -> str:
    return value or TIDAK_TERCATAT


def _skor(value: Decimal | None) -> str:
    """Dua desimal, dibulatkan setengah ke atas seperti tampilan skor lainnya."""
    if value is None:
        return ""
    return str(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def _sel(row: ScoreCsvRow, session: ScoreCsvSession) -> list[str]:
    """Satu baris berkas, urutannya sama dengan HEADER, setiap sel sudah dinetralkan."""
    nilai = [
        row.case_code,
        _atau_tidak_tercatat(row.case_version),
        session.product_name,
        MeasurementKind(session.measurement_kind).value,
        _DINILAI if row.graded else _TIDAK_DINILAI,
        _skor(row.det_score),
        _skor(row.llm_score),
        _atau_tidak_tercatat(session.rubric_version),
        _atau_tidak_tercatat(session.product_model_version),
        _atau_tidak_tercatat(session.judge_model_version),
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
