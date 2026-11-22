"""Impor kasus lewat CSV: template, pemeriksaan berkas, dan pemeriksaan baris. PBI-4 SCRUM-338.

Diagram D7 (alur) dan D8 (tabel). Modul ini murni, tanpa basis data:
- Template (AC6): delapan kolom yang diturunkan dari CaseWrite (keputusan SA), tanpa case_code
  karena kode dibuat sistem. UTF-8 dengan BOM dan pemisah koma.
- Tingkat berkas (gagal = tolak semua, tidak ada yang disimpan): ekstensi .csv, UTF-8 dengan
  atau tanpa BOM, header sama dengan template, maksimal 500 baris kasus (AC4).
- Baris yang seluruhnya kosong dilewati tanpa dicatat. Nomor baris mengikuti spreadsheet
  (header = baris 1), supaya alasan gagal mudah dicari di Google Sheets.
"""

import csv
import io
from dataclasses import dataclass

from app.shared.exceptions import ValidationError

TEMPLATE_COLUMNS = (
    "title",
    "question",
    "category",
    "split_tag",
    "legal_refs",
    "must_contain",
    "must_not_contain",
    "expected_conclusion",
)
# AC4: impor sekali untuk migrasi awal, sekitar 500 kasus per berkas.
MAX_ROWS = 500
# 500 baris dengan pertanyaan 20.000 karakter sudah sekitar 10 MB; 20 MB memberi ruang untuk
# rujukan dan kriteria tanpa membiarkan berkas raksasa dibaca ke memori.
MAX_FILE_BYTES = 20 * 1024 * 1024

IMPORT_FILE_NOT_CSV = "IMPORT_FILE_NOT_CSV"
IMPORT_FILE_NOT_UTF8 = "IMPORT_FILE_NOT_UTF8"
IMPORT_FILE_TOO_LARGE = "IMPORT_FILE_TOO_LARGE"
IMPORT_FILE_UNREADABLE = "IMPORT_FILE_UNREADABLE"
IMPORT_FILE_EMPTY = "IMPORT_FILE_EMPTY"
IMPORT_HEADER_MISMATCH = "IMPORT_HEADER_MISMATCH"
IMPORT_TOO_MANY_ROWS = "IMPORT_TOO_MANY_ROWS"

_BOM = "﻿"


class ImportFileError(ValidationError):
    """Seluruh berkas ditolak (D7), 422. Tidak ada batch, suite, atau kasus yang dibuat."""


@dataclass(frozen=True)
class RawRow:
    """Satu baris kasus apa adanya. row_no mengikuti spreadsheet (header = baris 1)."""

    row_no: int
    cells: tuple[str, ...]


def template_csv() -> bytes:
    """Template unduhan (AC6): hanya header, supaya mengunggahnya kembali tidak membuat kasus."""
    keluaran = io.StringIO()
    csv.writer(keluaran, lineterminator="\r\n").writerow(TEMPLATE_COLUMNS)
    return (_BOM + keluaran.getvalue()).encode("utf-8")


def read_file(file_name: str, data: bytes) -> list[RawRow]:
    """Periksa berkas (D7 tingkat berkas) lalu kembalikan baris kasusnya.

    Raises ImportFileError bila berkas harus ditolak utuh. Jumlah kolom per baris tidak
    diperiksa di sini: kolom yang kurang atau lebih adalah kegagalan baris itu saja.
    """
    if not file_name.lower().endswith(".csv"):
        raise ImportFileError(
            "Berkas harus berupa .csv. Dari Google Sheets: File > Download > "
            "Comma Separated Values (.csv).",
            code=IMPORT_FILE_NOT_CSV,
        )
    if len(data) > MAX_FILE_BYTES:
        raise ImportFileError(
            f"Berkas melebihi {MAX_FILE_BYTES // (1024 * 1024)} MB.", code=IMPORT_FILE_TOO_LARGE
        )
    teks = _decode(data)
    rekaman = _records(teks)
    if not rekaman or [_nama_kolom(sel) for sel in rekaman[0]] != list(TEMPLATE_COLUMNS):
        raise ImportFileError(
            "Header tidak sama dengan template. Pakai template dari sistem, pemisah koma, "
            "dan jangan mengubah nama atau urutan kolom.",
            code=IMPORT_HEADER_MISMATCH,
            details={"expected": list(TEMPLATE_COLUMNS)},
        )
    baris = [
        RawRow(row_no=nomor, cells=tuple(sel))
        for nomor, sel in enumerate(rekaman[1:], start=2)
        if any(isi.strip() for isi in sel)
    ]
    if not baris:
        raise ImportFileError("Berkas tidak berisi baris kasus.", code=IMPORT_FILE_EMPTY)
    if len(baris) > MAX_ROWS:
        raise ImportFileError(
            f"Berkas berisi {len(baris)} baris kasus; maksimal {MAX_ROWS} per berkas. "
            "Pecah menjadi beberapa berkas.",
            code=IMPORT_TOO_MANY_ROWS,
        )
    return baris


def _decode(data: bytes) -> str:
    try:
        teks = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportFileError(
            "Berkas harus berpengkodean UTF-8. Ekspor ulang dari Google Sheets, atau di Excel "
            "pilih CSV UTF-8.",
            code=IMPORT_FILE_NOT_UTF8,
        ) from None
    if "\x00" in teks:
        raise ImportFileError(_PESAN_RUSAK, code=IMPORT_FILE_UNREADABLE)
    return teks


_PESAN_RUSAK = "Berkas CSV rusak dan tidak bisa dibaca. Ekspor ulang dari Google Sheets."


def _records(teks: str) -> list[list[str]]:
    try:
        return list(csv.reader(io.StringIO(teks, newline=""), strict=True))
    except csv.Error:
        raise ImportFileError(_PESAN_RUSAK, code=IMPORT_FILE_UNREADABLE) from None


def _nama_kolom(sel: str) -> str:
    return sel.strip().lower()
