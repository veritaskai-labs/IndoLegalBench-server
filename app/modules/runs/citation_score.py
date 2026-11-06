"""Jalur deterministik penilaian: ketepatan rujukan peraturan dan pasal. PBI-14 AC1, AC2.

D7 langkah "Ekstrak rujukan peraturan dari jawaban" lalu "Cocokkan": rujukan
acuan adalah legal_refs kasus di snapshot (bentuk PBI-21), dan sebuah peraturan
cocok bila jenis, nomor, dan tahunnya sama. Tidak ada daftar sinonim; jenis
dikenali dari label dan singkatan regulation_types saja, jadi 'UU 40/2007' dan
'Undang-Undang Nomor 40 Tahun 2007' sama, sedangkan 'UU Perseroan Terbatas'
tanpa nomor dan tahun tidak terbaca sebagai rujukan.

Pasal: acuan "Pasal 156" terpenuhi bila jawaban menyebut Pasal 156 untuk peraturan
itu, termasuk "Pasal 156 ayat (1)"; pasal tambahan tidak dihukum. Ayat, angka, dan
huruf acuan tidak diperiksa. Rujukan berangkai (jo./jis.) dinilai satu kesatuan:
benar hanya bila semua link-nya benar.

Hasilnya mengikuti kolom D6 grading_results: det_regulation_matches
[{jenis, nomor, tahun, cocok}], det_article_matches [{pasal acuan, disebut}],
det_all_chained_cited, dan det_score. Tabelnya milik SCRUM-222, jadi modul ini
murni dan tidak membaca database.

Rumus klien "total extracted sitasi / total real sitasi benar" masih TBA (AC1).
Default-nya recall (rujukan acuan yang benar / rujukan acuan yang bisa dinilai)
karena AC2 tidak menghukum rujukan tambahan; presisi (peraturan terekstrak yang
ada di acuan / peraturan terekstrak) tersedia bila klien memilihnya. Rujukan teks
bebas dan link lama tanpa tahun tidak punya tiga unsur itu, jadi dicatat sebagai
tidak bisa dinilai dan tidak masuk penyebut.

Batas yang diketahui: "PASAL 156" dengan huruf kapital semua tidak terbaca (huruf
besar dipakai untuk membedakan nomor romawi); "UU No. 13 Th. 2003" terbaca tanpa
tahun, jadi tidak cocok; nama populer di antara pasal dan peraturannya ("Pasal 156
UU Ketenagakerjaan (UU 13/2003)") membuat pasal itu tidak dikaitkan ke UU 13/2003.
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
_PENGHUBUNG = {"jo": "jo.", "jis": "jis."}

# Nomor pasal: angka dengan huruf sisipan (27A) atau romawi kapital (Pasal II UU 6/2023).
# Romawi tidak dibaca tanpa membedakan huruf, supaya "pasal di atas" bukan Pasal DI.
_NOMOR_PASAL = r"(?:\d+[A-Za-z]?|[IVXLCDM]+)(?!\w)"
_POLA_PASAL = re.compile(
    rf"(?<!\w)[Pp]asal\s+(?P<daftar>{_NOMOR_PASAL}"
    # Koma boleh diikuti kata sambung: "Pasal 156, 157, dan 158" (koma sebelum 'dan', PUEBI).
    rf"(?:\s*(?:,(?:\s*(?:dan|serta|atau)\b)?|\bdan\b|\bserta\b|\batau\b)\s*{_NOMOR_PASAL})*)"
)
# Teks yang boleh ada di antara pasal dan peraturannya dalam satu frasa rujukan:
# "Pasal 54 ayat 1 huruf d UU ...", "Pasal 155 dan Pasal 156 ...", "Pasal 156 dari UU ...".
# Tiap spasi hanya bisa dibaca satu cara (satu \s per ulangan, ayat tanpa \s* di ujung):
# \s+ atau \s* yang tumpang tindih di dalam (...)* membuat sela yang gagal cocok, mis.
# baris tabel markdown, dicoba 2^n cara dan penilai macet.
_SELA_FRASA = re.compile(
    r"(?:\s|,|\(|\)|\b(?:dan|serta|atau|dari|dalam|pada)\b|\bayat\s*(?:\(\s*\w+\s*\)|\w+)"
    r"|\bhuruf\s+\w+\b|\bangka\s+\w+\b|\bpasal\s+\w+\b)*",
    re.IGNORECASE,
)
_SELA_MAKS = 80


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
class ArticleMatch:
    """Satu pasal acuan dan apakah jawaban menyebutnya untuk peraturannya (D6 det_article_matches)."""

    regulation_type: str
    number: str
    year: int
    pasal: str
    cited: bool


@dataclass(frozen=True)
class CitationScore:
    formula: ScoreFormula
    # None bila kasus tidak punya rujukan acuan yang bisa dinilai.
    score: Decimal | None
    regulation_matches: tuple[RegulationMatch, ...]
    article_matches: tuple[ArticleMatch, ...]
    # None bila tidak ada rujukan berangkai, supaya tidak tampil sebagai "semua disebut".
    all_chained_cited: bool | None
    reference_correct: int
    reference_total: int
    extracted_correct: int
    extracted_total: int
    ungradable_refs: tuple[str, ...]


# (jenis, nomor ternormalisasi, tahun)
_Kunci = tuple[str, str, int | None]


@dataclass(frozen=True)
class _Sebutan:
    """Satu peraturan yang disebut jawaban, dengan letaknya untuk mengaitkan pasal."""

    kunci: _Kunci
    mulai: int
    akhir: int


@dataclass(frozen=True)
class _Link:
    kunci: _Kunci
    number: str
    pasal: str | None


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
            rf"(?<!\w)(?P<jenis>{pilihan})(?:{_PEMISAH}Republik{_PEMISAH}Indonesia)?"
            # Singkatan dalam kurung: "Undang-Undang (UU) Nomor 8 Tahun 1999".
            r"(?:\s*\([^()\n]{1,20}\))?\s+"
            r"(?:(?:Nomor|No\.?)\s*(?P<nomor_kata>[0-9A-Za-z][\w./\-]*)|(?P<nomor>\d[\w./\-]*))"
            r"(?:\s+Tahun\s+(?P<tahun>\d{4}))?",
            re.IGNORECASE,
        )

    def score(self, jawaban: str, acuan: Sequence[Mapping[str, Any]]) -> CitationScore:
        sebutan = self._sebutan(jawaban)
        # Sitasi unik sesuai urutan kemunculan; menyebut ulang tidak menambah hitungan.
        terekstrak = list(dict.fromkeys(item.kunci for item in sebutan))
        disebut = {kunci for kunci in terekstrak if kunci[2] is not None}
        pasal_disebut = _pasal_disebut(jawaban, sebutan)
        unit, tidak_dinilai = self._unit_acuan(acuan)

        def benar(link: _Link) -> bool:
            if link.kunci not in disebut:
                return False
            return link.pasal is None or (link.kunci, _kunci_pasal(link.pasal)) in pasal_disebut

        regulasi: dict[_Kunci, RegulationMatch] = {}
        pasal: dict[tuple[_Kunci, str], ArticleMatch] = {}
        for links in unit:
            for link in links:
                kunci, tahun = link.kunci, link.kunci[2]
                regulasi.setdefault(
                    kunci, RegulationMatch(kunci[0], link.number, tahun, kunci in disebut)
                )
                if link.pasal is not None:
                    pasal.setdefault(
                        (kunci, _kunci_pasal(link.pasal)),
                        ArticleMatch(kunci[0], link.number, tahun, link.pasal, benar(link)),
                    )
        hasil_unit = [all(benar(link) for link in links) for links in unit]
        rangkaian = [ok for links, ok in zip(unit, hasil_unit, strict=True) if len(links) > 1]
        acuan_benar = sum(hasil_unit)
        ekstrak_benar = sum(kunci in regulasi for kunci in terekstrak)

        return CitationScore(
            formula=self._formula,
            score=self._skor(acuan_benar, len(unit), ekstrak_benar, len(terekstrak)),
            regulation_matches=tuple(regulasi.values()),
            article_matches=tuple(pasal.values()),
            all_chained_cited=all(rangkaian) if rangkaian else None,
            reference_correct=acuan_benar,
            reference_total=len(unit),
            extracted_correct=ekstrak_benar,
            extracted_total=len(terekstrak),
            ungradable_refs=tuple(tidak_dinilai),
        )

    def _sebutan(self, jawaban: str) -> list[_Sebutan]:
        hasil: list[_Sebutan] = []
        for temuan in self._pola.finditer(jawaban):
            code = self._code_per_nama[_kunci_nama(temuan["jenis"])]
            nomor = (temuan["nomor_kata"] or temuan["nomor"]).rstrip(_EKOR_NOMOR)
            tahun = int(temuan["tahun"]) if temuan["tahun"] else _tahun_di_nomor(nomor)
            kunci = (code, _kunci_nomor(nomor, tahun), tahun)
            hasil.append(_Sebutan(kunci, temuan.start(), temuan.end()))
        return hasil

    def _unit_acuan(
        self, acuan: Sequence[Mapping[str, Any]]
    ) -> tuple[list[tuple[_Link, ...]], list[str]]:
        """Satu unit per rujukan acuan (rangkaian = satu unit), dan teks yang tidak bisa dinilai."""
        unit: dict[tuple[_Link, ...], None] = {}
        tidak_dinilai: list[str] = []
        for rujukan in acuan:
            links = rujukan.get("links") or []
            if rujukan.get("kind") != "structured":
                tidak_dinilai.append(rujukan.get("free_text") or "")
            elif any(link.get("year") is None for link in links):
                # Link Sprint 1 boleh tanpa tahun (PBI-21 AC9): tiga aturan dasar tidak berlaku.
                tidak_dinilai.append(self._tulis(links))
            else:
                unit.setdefault(tuple(_link(link) for link in links), None)
        return list(unit), tidak_dinilai

    def _tulis(self, links: Sequence[Mapping[str, Any]]) -> str:
        """Teks singkat rujukan yang tidak bisa dinilai, untuk ditampilkan apa adanya."""
        bagian: list[str] = []
        for link in links:
            if link.get("connector") in _PENGHUBUNG:
                bagian.append(_PENGHUBUNG[link["connector"]])
            if link.get("pasal"):
                bagian.append(f"Pasal {link['pasal']}")
            nama = f"{self._nama_jenis(link['regulation_type'])} {link['number']}"
            bagian.append(f"{nama}/{link['year']}" if link.get("year") else nama)
        return " ".join(bagian)

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


def _pasal_disebut(jawaban: str, sebutan: Sequence[_Sebutan]) -> set[tuple[_Kunci, str]]:
    """Pasal yang disebut beserta peraturannya.

    Pasal dikaitkan ke peraturan yang langsung menyusul dalam satu frasa rujukan
    ("Pasal 156 ayat (1) UU 13/2003"). Bila tidak ada, ke peraturan terakhir yang
    disebut sebelumnya ("Pasal 486 undang-undang yang sama"). Pasal tanpa peraturan
    sama sekali, mis. "Pasal 1320 KUHPerdata" di awal jawaban, tidak dikaitkan.
    """
    hasil: set[tuple[_Kunci, str]] = set()
    for temuan in _POLA_PASAL.finditer(jawaban):
        target = _peraturan_pasal(jawaban, sebutan, temuan.start(), temuan.end())
        if target is None:
            continue
        for nomor in re.findall(_NOMOR_PASAL, temuan["daftar"]):
            hasil.add((target, _kunci_pasal(nomor)))
    return hasil


def _peraturan_pasal(
    jawaban: str, sebutan: Sequence[_Sebutan], mulai: int, akhir: int
) -> _Kunci | None:
    berikut = next((item for item in sebutan if item.mulai >= akhir), None)
    if berikut is not None:
        sela = jawaban[akhir : berikut.mulai]
        if len(sela) <= _SELA_MAKS and _SELA_FRASA.fullmatch(sela):
            return berikut.kunci
    sebelum = [item for item in sebutan if item.akhir <= mulai]
    return sebelum[-1].kunci if sebelum else None


def _link(link: Mapping[str, Any]) -> _Link:
    tahun = link["year"]
    kunci = (link["regulation_type"], _kunci_nomor(link["number"], tahun), tahun)
    return _Link(kunci, link["number"], link.get("pasal"))


def _kunci_nama(teks: str) -> str:
    return " ".join(re.split(_PEMISAH, teks.strip())).casefold()


def _kunci_pasal(pasal: str) -> str:
    return "".join(pasal.split()).casefold()


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
