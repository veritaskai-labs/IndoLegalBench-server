"""Jalur deterministik penilaian: ketepatan rujukan peraturan. PBI-14 AC1, AC2.

D7 langkah "Ekstrak rujukan peraturan dari jawaban" lalu "Cocokkan": rujukan
acuan adalah legal_refs kasus di snapshot (bentuk PBI-21), dan sebuah rujukan
benar bila jenis, nomor, dan tahunnya sama. Tidak ada daftar sinonim; jenis
dikenali dari label dan singkatan regulation_types saja, jadi 'UU 40/2007' dan
'Undang-Undang Nomor 40 Tahun 2007' sama, sedangkan 'UU Perseroan Terbatas'
tanpa nomor dan tahun tidak terbaca sebagai rujukan.

Hasilnya mengikuti kolom D6 grading_results: det_regulation_matches
[{jenis, nomor, tahun, cocok}] dan det_score. Tabelnya milik SCRUM-222, jadi
modul ini murni dan tidak membaca database.

Rumus klien "total extracted sitasi / total real sitasi benar" masih TBA (AC1).
Default-nya recall (acuan yang disebut benar / acuan yang bisa dinilai) karena
AC2 tidak menghukum rujukan tambahan; presisi tersedia bila klien memilihnya.
Rujukan teks bebas dan link lama tanpa tahun tidak punya tiga unsur itu, jadi
dicatat sebagai tidak bisa dinilai dan tidak masuk penyebut.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from app.modules.legal_refs.service import TypeName

_DUA_DIGIT = Decimal("0.01")
# Spasi dan tanda hubung dianggap sama: "Undang-Undang", "Undang Undang", "undang-undang".
_PEMISAH = r"[\s\-]+"
_EKOR_NOMOR = ".,;:)/-"


class ScoreFormula(StrEnum):
    """Rumus skor selama pembilang dan penyebut klien belum dikonfirmasi (AC1)."""

    RECALL = "recall"
    PRECISION = "precision"


@dataclass(frozen=True)
class RegulationMatch:
    """Satu peraturan acuan dan apakah jawaban menyebutnya (D6 det_regulation_matches)."""

    regulation_type: str
    number: str
    year: int
    matched: bool


@dataclass(frozen=True)
class CitationScore:
    formula: ScoreFormula
    # None bila kasus tidak punya rujukan acuan yang bisa dinilai.
    score: Decimal | None
    regulation_matches: tuple[RegulationMatch, ...]
    reference_correct: int
    reference_total: int
    extracted_correct: int
    extracted_total: int
    ungradable_refs: tuple[str, ...]


# (jenis, nomor ternormalisasi, tahun)
_Kunci = tuple[str, str, int | None]


class CitationScorer:
    """Dibangun sekali per penilaian dari daftar jenis peraturan, lalu dipakai per jawaban."""

    def __init__(
        self, jenis: Mapping[str, TypeName], *, formula: ScoreFormula = ScoreFormula.RECALL
    ) -> None:
        self._jenis = jenis
        self._formula = formula
        self._code_per_nama = _code_per_nama(jenis)
        # Nama terpanjang dicoba dulu supaya "Peraturan Pemerintah Pengganti Undang-Undang"
        # tidak terbaca sebagai "Peraturan Pemerintah".
        nama = sorted(self._code_per_nama, key=lambda kunci: (-len(kunci), kunci))
        pilihan = "|".join(_PEMISAH.join(map(re.escape, kunci.split(" "))) for kunci in nama)
        self._pola = re.compile(
            rf"(?<!\w)(?P<jenis>{pilihan})(?:{_PEMISAH}Republik{_PEMISAH}Indonesia)?\s+"
            r"(?:(?:Nomor|No\.?)\s*(?P<nomor_kata>[0-9A-Za-z][\w./\-]*)|(?P<nomor>\d[\w./\-]*))"
            r"(?:\s+Tahun\s+(?P<tahun>\d{4}))?",
            re.IGNORECASE,
        )

    def score(self, jawaban: str, acuan: Sequence[Mapping[str, Any]]) -> CitationScore:
        terekstrak = self._ekstrak(jawaban)
        unit, tidak_dinilai = self._unit_acuan(acuan)
        disebut = {kunci for kunci in terekstrak if kunci[2] is not None}

        matches: dict[_Kunci, RegulationMatch] = {}
        for kunci, nomor in unit.values():
            matches.setdefault(kunci, RegulationMatch(kunci[0], nomor, kunci[2], kunci in disebut))
        acuan_benar = sum(kunci in disebut for kunci, _ in unit.values())
        regulasi_acuan = {kunci for kunci, _ in unit.values()}
        ekstrak_benar = sum(kunci in regulasi_acuan for kunci in terekstrak)

        return CitationScore(
            formula=self._formula,
            score=self._skor(acuan_benar, len(unit), ekstrak_benar, len(terekstrak)),
            regulation_matches=tuple(matches.values()),
            reference_correct=acuan_benar,
            reference_total=len(unit),
            extracted_correct=ekstrak_benar,
            extracted_total=len(terekstrak),
            ungradable_refs=tuple(tidak_dinilai),
        )

    def _ekstrak(self, jawaban: str) -> list[_Kunci]:
        """Sitasi unik sesuai urutan kemunculan; menyebut ulang tidak menambah hitungan."""
        hasil: dict[_Kunci, None] = {}
        for temuan in self._pola.finditer(jawaban):
            code = self._code_per_nama[_kunci_nama(temuan["jenis"])]
            nomor = (temuan["nomor_kata"] or temuan["nomor"]).rstrip(_EKOR_NOMOR)
            tahun = int(temuan["tahun"]) if temuan["tahun"] else _tahun_di_nomor(nomor)
            hasil.setdefault((code, _kunci_nomor(nomor, tahun), tahun), None)
        return list(hasil)

    def _unit_acuan(
        self, acuan: Sequence[Mapping[str, Any]]
    ) -> tuple[dict[tuple[_Kunci, str | None], tuple[_Kunci, str]], list[str]]:
        """Satu unit per link acuan (peraturan + pasal), dan teks rujukan yang tidak bisa dinilai."""
        unit: dict[tuple[_Kunci, str | None], tuple[_Kunci, str]] = {}
        tidak_dinilai: list[str] = []
        for rujukan in acuan:
            if rujukan.get("kind") != "structured":
                tidak_dinilai.append(rujukan.get("free_text") or "")
                continue
            for link in rujukan.get("links") or []:
                tahun = link.get("year")
                if tahun is None:
                    # Link Sprint 1 boleh tanpa tahun (PBI-21 AC9): tiga aturan dasar tidak berlaku.
                    tidak_dinilai.append(
                        f"{self._nama_jenis(link['regulation_type'])} {link['number']}"
                    )
                    continue
                kunci = (link["regulation_type"], _kunci_nomor(link["number"], tahun), tahun)
                unit.setdefault((kunci, link.get("pasal")), (kunci, link["number"]))
        return unit, tidak_dinilai

    def _nama_jenis(self, code: str) -> str:
        nama = self._jenis.get(code)
        if nama is None:
            return code
        return nama.singkatan or nama.label

    def _skor(
        self, acuan_benar: int, acuan_total: int, ekstrak_benar: int, ekstrak_total: int
    ) -> Decimal | None:
        if acuan_total == 0:
            return None
        if self._formula is ScoreFormula.PRECISION:
            pembilang, penyebut = ekstrak_benar, ekstrak_total
        else:
            pembilang, penyebut = acuan_benar, acuan_total
        if penyebut == 0:
            return Decimal("0.00")
        return (Decimal(pembilang) / Decimal(penyebut)).quantize(_DUA_DIGIT, ROUND_HALF_UP)


def _kunci_nama(teks: str) -> str:
    return " ".join(re.split(_PEMISAH, teks.strip())).casefold()


def _code_per_nama(jenis: Mapping[str, TypeName]) -> dict[str, str]:
    """Nama tertulis -> code. Singkatan dulu, lalu label, seperti type_lookup (SCRUM-159).

    Code diurutkan supaya nama yang dipakai dua jenis selalu jatuh ke jenis yang sama,
    apa pun urutan daftar dari database (AC1 reproducible).
    """
    hasil: dict[str, str] = {}
    urut = sorted(jenis.items())
    for code, nama in urut:
        if nama.singkatan:
            hasil.setdefault(_kunci_nama(nama.singkatan), code)
    for code, nama in urut:
        hasil.setdefault(_kunci_nama(nama.label), code)
    return hasil


def _tahun_di_nomor(nomor: str) -> int | None:
    """'40/2007' dan '12/POJK.03/2018' memuat tahun di akhir nomor (aturan 3 display_text)."""
    temuan = re.search(r"/(\d{4})$", nomor)
    return int(temuan.group(1)) if temuan else None


def _kunci_nomor(nomor: str, tahun: int | None) -> str:
    """Nomor tanpa spasi dan huruf besar, tanpa akhiran '/{tahun}' yang sudah dicatat terpisah."""
    kunci = "".join(nomor.split()).casefold()
    if tahun is not None and kunci.endswith(f"/{tahun}"):
        kunci = kunci[: -len(f"/{tahun}")]
    return kunci
