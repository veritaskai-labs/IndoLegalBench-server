"""Pembaca data seed jenis peraturan.

Sumber: Database Peraturan JDIH BPK RI (peraturan.bpk.go.id), filter Jenis.
Diambil 16 Oktober 2026. Dibersihkan: 3 entri kategori dan 2 duplikat
dibuang, titik koma dan typo dibenahi.

Datanya ada di migrations/seeds/regulation_types_seed_2026-10-16.csv.
Sumber dan tanggal dicatat di sini, bukan di CSV, karena baris komentar
merusak parsing. Loader memeriksa isi file sebelum ada yang masuk ke
database, jadi file rusak gagal dengan pesan yang menyebut baris dan
kolomnya, bukan dengan error database.

code dirujuk legal_ref_links.regulation_type, jadi tidak boleh diubah
setelah seed masuk. Mengurangi jenis dilakukan lewat is_active.
"""

import csv
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parents[3]
    / "migrations"
    / "seeds"
    / "regulation_types_seed_2026-10-16.csv"
)

COLUMNS = ("urutan", "code", "label", "singkatan", "is_active")
MAX_CODE = 150
MAX_LABEL = 255
MAX_SINGKATAN = 50
_CODE_PATTERN = re.compile(r"[a-z0-9_]+")
_BOOLEANS = {"true": True, "false": False}


class SeedDataError(ValueError):
    """File seed tidak bisa dipakai. Pesannya menyebut baris dan kolom."""


@dataclass(frozen=True)
class RegulationTypeSeed:
    code: str
    label: str
    singkatan: str | None
    urutan: int
    is_active: bool


def load_regulation_types(path: Path = DEFAULT_SEED_PATH) -> list[RegulationTypeSeed]:
    """Baca dan periksa seluruh file. Satu kesalahan menggagalkan semuanya."""
    # utf-8-sig membuang BOM, newline="" membiarkan modul csv menangani CRLF
    with path.open(encoding="utf-8-sig", newline="") as berkas:
        pembaca = csv.DictReader(berkas)
        _periksa_header(pembaca.fieldnames)
        hasil: list[RegulationTypeSeed] = []
        sudah_ada_code: set[str] = set()
        sudah_ada_urutan: set[int] = set()
        for nomor_baris, baris in enumerate(pembaca, start=2):
            jenis = _baca_baris(nomor_baris, baris)
            if jenis.code in sudah_ada_code:
                raise _salah(nomor_baris, "code", f"'{jenis.code}' sudah dipakai baris lain")
            if jenis.urutan in sudah_ada_urutan:
                raise _salah(nomor_baris, "urutan", f"{jenis.urutan} sudah dipakai baris lain")
            sudah_ada_code.add(jenis.code)
            sudah_ada_urutan.add(jenis.urutan)
            hasil.append(jenis)
    if not hasil:
        raise SeedDataError("File seed kosong: tidak ada baris data")
    return hasil


def _periksa_header(nama_kolom: list[str] | None) -> None:
    tampak = tuple((nama or "").strip() for nama in (nama_kolom or ()))
    kurang = [kolom for kolom in COLUMNS if kolom not in tampak]
    if kurang:
        raise SeedDataError(f"Kolom seed kurang: {', '.join(kurang)}")


def _baca_baris(nomor_baris: int, baris: dict[str, str | None]) -> RegulationTypeSeed:
    nilai = {kunci.strip(): (isi or "").strip() for kunci, isi in baris.items() if kunci}
    return RegulationTypeSeed(
        code=_baca_code(nomor_baris, nilai["code"]),
        label=_baca_teks(nomor_baris, "label", nilai["label"], MAX_LABEL, wajib=True),
        singkatan=_baca_teks(
            nomor_baris, "singkatan", nilai["singkatan"], MAX_SINGKATAN, wajib=False
        ),
        urutan=_baca_urutan(nomor_baris, nilai["urutan"]),
        is_active=_baca_boolean(nomor_baris, nilai["is_active"]),
    )


def _baca_code(nomor_baris: int, isi: str) -> str:
    if not _CODE_PATTERN.fullmatch(isi) or len(isi) > MAX_CODE:
        raise _salah(
            nomor_baris,
            "code",
            f"harus huruf kecil, angka, atau garis bawah, paling panjang {MAX_CODE}",
        )
    return isi


def _baca_teks(nomor_baris: int, kolom: str, isi: str, batas: int, *, wajib: bool) -> str | None:
    if wajib and not isi:
        raise _salah(nomor_baris, kolom, "tidak boleh kosong")
    if len(isi) > batas:
        raise _salah(nomor_baris, kolom, f"paling panjang {batas} karakter")
    return isi or None


def _baca_urutan(nomor_baris: int, isi: str) -> int:
    if not isi.isdecimal() or int(isi) < 1:
        raise _salah(nomor_baris, "urutan", "harus bilangan bulat positif")
    return int(isi)


def _baca_boolean(nomor_baris: int, isi: str) -> bool:
    if isi.lower() not in _BOOLEANS:
        raise _salah(nomor_baris, "is_active", "harus true atau false")
    return _BOOLEANS[isi.lower()]


def _salah(nomor_baris: int, kolom: str, alasan: str) -> SeedDataError:
    return SeedDataError(f"Seed baris {nomor_baris}, kolom {kolom}: {alasan}")
