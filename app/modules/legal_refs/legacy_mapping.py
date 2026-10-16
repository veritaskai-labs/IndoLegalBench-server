"""Pemeta rujukan hukum Sprint 1 ke bentuk tabel baru.

Rujukan lama ada di case_versions.content["legal_refs"] sebagai daftar dict
berisi regulation_type, regulation_number, year (opsional), pasal, ayat,
dan huruf. Fungsi di sini murni: tidak membaca database dan tidak tahu
cara menyimpan, jadi pembacaan dan penulisan tetap tugas migrasi.

Aturannya: tidak ada data yang boleh hilang. Entri yang bisa menjadi link
terstruktur (jenis dikenal, nomor ada, tahun kosong atau 1 sampai 9999,
nilai muat di kolom baru) dipetakan apa adanya. Entri lain disimpan sebagai
teks bebas yang memuat semua nilai aslinya, dan dihitung supaya migrasi
bisa melaporkannya.
"""

from dataclasses import dataclass
from typing import Any

from app.modules.legal_refs.models import LegalRefConnector, LegalRefForm, LegalRefLink
from app.modules.legal_refs.type_lookup import RegulationTypeLookup

MIN_YEAR = 1
MAX_YEAR = 9999

# Batas panjang dibaca dari kolom tabel baru, jadi tidak ada angka salinan.
_COLUMNS = LegalRefLink.__table__.c
_BATAS_PANJANG = {nama: _COLUMNS[nama].type.length for nama in ("number", "pasal", "ayat", "huruf")}


@dataclass(frozen=True)
class LegalRefLinkDraft:
    position: int
    connector: LegalRefConnector
    regulation_type: str
    number: str
    year: int | None
    pasal: str | None
    ayat: str | None
    huruf: str | None


@dataclass(frozen=True)
class LegalRefDraft:
    order: int
    form: LegalRefForm
    free_text: str | None
    links: list[LegalRefLinkDraft]


@dataclass(frozen=True)
class MappingResult:
    refs: list[LegalRefDraft]
    free_text_fallbacks: int
    skipped: int


def map_legacy_refs(raw: Any, lookup: RegulationTypeLookup) -> MappingResult:
    """Petakan isi key legal_refs lama. Nilai yang bukan list dianggap tanpa rujukan."""
    if not isinstance(raw, list):
        return MappingResult(refs=[], free_text_fallbacks=0, skipped=0)

    refs: list[LegalRefDraft] = []
    fallbacks = 0
    skipped = 0
    for entri in raw:
        # Urutan dihitung dari rujukan yang lolos, jadi tetap rapat 1..n.
        draft = _petakan_entri(entri, len(refs) + 1, lookup)
        if draft is None:
            skipped += 1
            continue
        if draft.form == LegalRefForm.FREE_TEXT:
            fallbacks += 1
        refs.append(draft)
    return MappingResult(refs=refs, free_text_fallbacks=fallbacks, skipped=skipped)


def _petakan_entri(entri: Any, order: int, lookup: RegulationTypeLookup) -> LegalRefDraft | None:
    if isinstance(entri, str):
        return _teks_bebas(order, entri) if entri.strip() else None
    if not isinstance(entri, dict):
        return None

    jenis = _teks(entri.get("regulation_type"))
    nomor = _teks(entri.get("regulation_number"))
    tahun = entri.get("year")
    pasal = _teks(entri.get("pasal"))
    ayat = _teks(entri.get("ayat"))
    huruf = _teks(entri.get("huruf"))

    if not (jenis or nomor or pasal or ayat or huruf) and tahun is None:
        return None

    code = lookup.resolve(jenis) if jenis else None
    if code and nomor and _tahun_sah(tahun) and _muat(nomor, pasal, ayat, huruf):
        link = LegalRefLinkDraft(
            position=1,
            connector=LegalRefConnector.NONE,
            regulation_type=code,
            number=nomor,
            year=tahun,
            pasal=pasal or None,
            ayat=ayat or None,
            huruf=huruf or None,
        )
        form = LegalRefForm.PASAL if pasal else LegalRefForm.NAME_ONLY
        return LegalRefDraft(order=order, form=form, free_text=None, links=[link])

    return _teks_bebas(order, _susun_teks(jenis, nomor, tahun, pasal, ayat, huruf))


def _teks(nilai: Any) -> str:
    """Nilai lama sebagai teks rapi. Kosong untuk None."""
    if nilai is None:
        return ""
    return str(nilai).strip()


def _tahun_sah(tahun: Any) -> bool:
    if tahun is None:
        return True
    return isinstance(tahun, int) and not isinstance(tahun, bool) and MIN_YEAR <= tahun <= MAX_YEAR


def _muat(nomor: str, pasal: str, ayat: str, huruf: str) -> bool:
    nilai = {"number": nomor, "pasal": pasal, "ayat": ayat, "huruf": huruf}
    return all(len(isi) <= _BATAS_PANJANG[nama] for nama, isi in nilai.items())


def _susun_teks(jenis: str, nomor: str, tahun: Any, pasal: str, ayat: str, huruf: str) -> str:
    """Tulis ulang semua nilai lama jadi satu kalimat, tanpa membuang apa pun."""
    kepala = [jenis]
    if nomor:
        kepala.append(f"Nomor {nomor}")
    if tahun is not None:
        kepala.append(f"Tahun {tahun}")
    bagian = [" ".join(isi for isi in kepala if isi)]
    for label, isi in (("Pasal", pasal), ("Ayat", ayat), ("Huruf", huruf)):
        if isi:
            bagian.append(f"{label} {isi}")
    return ", ".join(isi for isi in bagian if isi)


def _teks_bebas(order: int, isi: str) -> LegalRefDraft:
    return LegalRefDraft(order=order, form=LegalRefForm.FREE_TEXT, free_text=isi, links=[])
