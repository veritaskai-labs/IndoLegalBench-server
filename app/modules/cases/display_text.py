"""Penulisan rujukan hukum sesuai praktik penulisan peraturan (PBI-21 AC6, AC10).

Contoh: "Pasal 156 UU 13/2003 jo. Pasal II Angka 5 UU 6/2023".

Aturan nama peraturan (Jira SCRUM-160, keputusan PO 16 Okt):
1. Jenis bersingkatan: "{singkatan} {nomor}/{tahun}", misalnya "UU 13/2003".
2. Tanpa singkatan: "{label} Nomor {nomor} Tahun {tahun}".
3. Nomor yang sudah berakhiran "/{tahun}" tidak ditulis ulang tahunnya.
4. Akhiran nomor yang beda dengan tahun tetap ditambah tahun (menunggu Steven).

Unit (keputusan PO 17 Okt): "Pasal {pasal} Angka {angka} ayat ({ayat}) huruf
{huruf}" sebelum nama peraturan. Tentang tidak ditampilkan.

Fungsi di sini murni. Label dan singkatan diberikan pemanggil, jadi modul ini
tidak membaca tabel regulation_types.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from app.modules.cases.schemas import LegalRefLinkRead, LegalRefStored

_PENGHUBUNG = {"jo": "jo.", "jis": "jis."}


@dataclass(frozen=True)
class TypeName:
    """Nama satu jenis peraturan seperti di regulation_types."""

    label: str
    singkatan: str | None


def citation_text(rujukan: LegalRefStored, jenis: Mapping[str, TypeName]) -> str:
    """Tulis satu rujukan. Teks bebas dikembalikan apa adanya (AC7)."""
    if rujukan.kind == "free_text":
        return rujukan.free_text or ""
    bagian: list[str] = []
    for link in rujukan.links:
        if link.connector in _PENGHUBUNG:
            bagian.append(_PENGHUBUNG[link.connector])
        bagian.append(_link_text(link, jenis))
    return " ".join(bagian)


def _link_text(link: LegalRefLinkRead, jenis: Mapping[str, TypeName]) -> str:
    """Unit yang dirujuk (kalau ada), lalu nama peraturannya."""
    return " ".join([*_unit_parts(link), _regulation_name(link, jenis)])


def _unit_parts(link: LegalRefLinkRead) -> list[str]:
    """Pasal, Angka, ayat, huruf. Link name_only tidak punya unit."""
    if link.form == "name_only":
        return []
    bagian = [f"Pasal {link.pasal}"]
    if link.form == "pasal_angka":
        bagian.append(f"Angka {link.angka}")
    if link.ayat:
        bagian.append(f"ayat ({link.ayat.strip('()')})")
    if link.huruf:
        bagian.append(f"huruf {link.huruf}")
    return bagian


def _regulation_name(link: LegalRefLinkRead, jenis: Mapping[str, TypeName]) -> str:
    """Nama peraturan menurut aturan 1-4. Link Sprint 1 tanpa tahun tidak menulis tahun."""
    nama = jenis.get(link.regulation_type)
    tahun = _year_suffix(link.number, link.year)
    if nama is not None and nama.singkatan:
        return f"{nama.singkatan} {link.number}" + (f"/{tahun}" if tahun else "")
    label = nama.label if nama is not None else link.regulation_type
    return f"{label} Nomor {link.number}" + (f" Tahun {tahun}" if tahun else "")


def _year_suffix(nomor: str, tahun: int | None) -> str:
    """Tahun yang perlu ditulis, atau "" kalau tidak ada atau sudah ada di akhir nomor."""
    if tahun is None or nomor.endswith(f"/{tahun}"):
        return ""
    return str(tahun)
