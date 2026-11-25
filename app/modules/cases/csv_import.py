"""Impor kasus lewat CSV: template, pemeriksaan berkas, dan pemeriksaan baris.

Modul ini murni, tanpa basis data:
- Template: delapan kolom yang diturunkan dari CaseWrite, tanpa case_code karena kode dibuat
  sistem. UTF-8 dengan BOM dan pemisah koma.
- Tingkat berkas (gagal = tolak semua, tidak ada yang disimpan): ekstensi .csv, UTF-8 dengan
  atau tanpa BOM, header sama dengan template, maksimal 500 baris kasus.
- Tingkat baris (gagal = baris itu saja): semua field boleh kosong karena hasilnya draft.
- Baris yang seluruhnya kosong dilewati tanpa dicatat. Nomor baris mengikuti spreadsheet
  (header = baris 1), supaya alasan gagal mudah dicari di Google Sheets.
"""

import csv
import hashlib
import io
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.modules.cases import validation
from app.modules.cases.fingerprint import content_fingerprint
from app.modules.cases.models import SplitTag
from app.modules.cases.schemas import LegalRefWrite
from app.shared.exceptions import DomainError, ValidationError

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
# Impor dipakai sekali untuk migrasi awal, sekitar 500 kasus per berkas.
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
    """Seluruh berkas ditolak, 422. Tidak ada batch, suite, atau kasus yang dibuat."""


@dataclass(frozen=True)
class RawRow:
    """Satu baris kasus apa adanya. row_no mengikuti spreadsheet (header = baris 1)."""

    row_no: int
    cells: tuple[str, ...]


def template_csv() -> bytes:
    """Template unduhan: hanya header, supaya mengunggahnya kembali tidak membuat kasus."""
    keluaran = io.StringIO()
    csv.writer(keluaran, lineterminator="\r\n").writerow(TEMPLATE_COLUMNS)
    return (_BOM + keluaran.getvalue()).encode("utf-8")


def read_file(file_name: str, data: bytes) -> list[RawRow]:
    """Periksa berkas lalu kembalikan baris kasusnya.

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


_BATAS_TEKS = {"title": 300, "question": 20000, "category": 120}
_TAG = {tag.value: tag for tag in SplitTag}


@dataclass(frozen=True)
class ImportedCase:
    """Isi satu baris yang lolos pemeriksaan, siap menjadi kasus draft."""

    row_no: int
    title: str
    question: str
    category: str | None
    split_tag: SplitTag | None
    legal_refs: list[dict[str, Any]]
    must_contain: list[str]
    must_not_contain: list[str]
    expected_conclusion: str | None
    fingerprint: str | None
    row_identity: str

    def as_payload(self, case_code: str) -> dict[str, Any]:
        """Bentuk body CaseWrite, untuk kelengkapan yang sama dengan kasus manual."""
        return {
            "case_code": case_code,
            "identity": {"title": self.title, "question": self.question, "category": self.category},
            "split_tag": self.split_tag.value if self.split_tag else None,
            "legal_refs": self.legal_refs,
            "answer_criteria": {
                "must_contain": self.must_contain,
                "must_not_contain": self.must_not_contain,
                "expected_conclusion": self.expected_conclusion,
            },
        }


@dataclass(frozen=True)
class RowFailure:
    """Baris yang gagal; alasannya ditampilkan di ringkasan dan baris ini tidak menjadi kasus."""

    row_no: int
    reason: str


class _BarisGagal(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def parse_row(
    raw: RawRow, *, regulation_types: Mapping[str, bool], current_year: int
) -> ImportedCase | RowFailure:
    """Periksa satu baris. Hanya masalah pertama, urut kolom template, yang dilaporkan.

    regulation_types: kode jenis peraturan -> aktif (legal_refs.TypeCatalog.active). Jenis
    nonaktif ditolak karena rujukan hasil impor selalu baru.
    """
    try:
        return _baca_baris(raw, regulation_types, current_year)
    except _BarisGagal as galat:
        return RowFailure(row_no=raw.row_no, reason=galat.reason)


def _baca_baris(
    raw: RawRow, regulation_types: Mapping[str, bool], current_year: int
) -> ImportedCase:
    if len(raw.cells) != len(TEMPLATE_COLUMNS):
        raise _BarisGagal(
            f"Jumlah kolom {len(raw.cells)}, seharusnya {len(TEMPLATE_COLUMNS)} sesuai template."
        )
    sel = dict(zip(TEMPLATE_COLUMNS, raw.cells, strict=True))
    title, question = _teks(sel, "title"), _teks(sel, "question")
    category = _teks(sel, "category") or None
    return ImportedCase(
        row_no=raw.row_no,
        title=title,
        question=question,
        category=category,
        split_tag=_tag(sel["split_tag"]),
        legal_refs=_rujukan(sel["legal_refs"], regulation_types, current_year),
        must_contain=_frasa(sel, "must_contain"),
        must_not_contain=_frasa(sel, "must_not_contain"),
        expected_conclusion=sel["expected_conclusion"].strip() or None,
        fingerprint=content_fingerprint(title, question),
        row_identity=_identitas_baris(raw.cells),
    )


_SPASI = re.compile(r"\s+")


def _identitas_baris(sel: tuple[str, ...]) -> str:
    """Sidik jari impor satu baris: sidik jari isi bila judul atau pertanyaan ada, kalau tidak
    SHA-256 seluruh kolom yang dinormalkan, supaya baris tanpa judul dan pertanyaan pun dikenali
    saat berkas yang sama diimpor ulang. Awalan "baris" menjaga nilainya tidak pernah sama dengan
    sidik jari isi."""
    isi = content_fingerprint(sel[0], sel[1])
    if isi is not None:
        return isi
    normal = "\n".join(_SPASI.sub(" ", nilai).strip().casefold() for nilai in sel)
    return hashlib.sha256(f"baris\n{normal}".encode()).hexdigest()


def _teks(sel: dict[str, str], kolom: str) -> str:
    nilai = sel[kolom].strip()
    batas = _BATAS_TEKS[kolom]
    if len(nilai) > batas:
        raise _BarisGagal(f"{kolom}: maksimal {batas} karakter (sekarang {len(nilai)}).")
    return nilai


def _tag(nilai: str) -> SplitTag | None:
    bersih = nilai.strip().lower()
    if not bersih:
        return None
    if bersih not in _TAG:
        raise _BarisGagal("split_tag: isi dev, test, atau kosongkan.")
    return _TAG[bersih]


def _list_json(kolom: str, nilai: str) -> list[Any]:
    if not nilai.strip():
        return []
    try:
        isi = json.loads(nilai)
    except ValueError:
        raise _BarisGagal(
            f'{kolom}: bukan list JSON yang valid. Contoh: ["teks pertama", "teks kedua"].'
        ) from None
    if not isinstance(isi, list):
        raise _BarisGagal(f"{kolom}: harus list JSON, diawali [ dan diakhiri ].")
    return isi


def _frasa(sel: dict[str, str], kolom: str) -> list[str]:
    hasil: list[str] = []
    for indeks, item in enumerate(_list_json(kolom, sel[kolom])):
        if not isinstance(item, str):
            raise _BarisGagal(f"{kolom}[{indeks}]: harus teks.")
        if item.strip():
            hasil.append(item.strip())
    return hasil


def _rujukan(
    nilai: str, regulation_types: Mapping[str, bool], current_year: int
) -> list[dict[str, Any]]:
    """Setiap isi boleh teks bebas atau objek rujukan terstruktur (bentuk LegalRefWrite)."""
    hasil = []
    for indeks, item in enumerate(_list_json("legal_refs", nilai)):
        if isinstance(item, str):
            item = {"kind": "free_text", "free_text": item, "links": []}
        if not isinstance(item, dict):
            raise _BarisGagal(f"legal_refs[{indeks}]: harus teks atau objek rujukan.")
        if item.get("kind") == "free_text":
            item = {"links": [], **item}
        hasil.append(_satu_rujukan(indeks, item, regulation_types, current_year))
    return hasil


def _satu_rujukan(
    indeks: int, item: dict[str, Any], regulation_types: Mapping[str, bool], current_year: int
) -> dict[str, Any]:
    """Aturan simpan rujukan yang sama dengan editor: skema, aturan link, lalu jenis."""
    try:
        rujukan = LegalRefWrite.model_validate(item).model_dump(mode="json")
    except PydanticValidationError as galat:
        kesalahan = [{**e, "loc": ("legal_refs", indeks, *e["loc"])} for e in galat.errors()]
        petunjuk = validation.response_from_pydantic(kesalahan)
        raise _BarisGagal(_alasan(petunjuk.get("field", ""), petunjuk["message"])) from None
    try:
        validation.check_citation(indeks, rujukan, current_year)
        validation.check_citation_types(indeks, rujukan, regulation_types, ())
    except DomainError as galat:
        raise _BarisGagal(_alasan(galat.field or "", galat.message)) from None
    return rujukan


def _alasan(field: str, pesan: str) -> str:
    """field: pesan, kecuali pesannya sudah menyebut field itu."""
    if not field or field in pesan:
        return pesan
    return f"{field}: {pesan}"
