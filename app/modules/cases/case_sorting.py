"""Urutan daftar kasus. PBI-4 SCRUM-81 SCRUM-379, AC3.

Satu tempat untuk menjawab "diurutkan menurut apa": kolom mana yang boleh (whitelist), arah
bawaannya, dan ekspresi ORDER BY-nya. Router memakai enum untuk validasi, service memutuskan arah
dan peringkat suite, repository memasang hasilnya ke query. Kolom baru cukup satu entri di
_EXPRESSIONS; tidak ada percabangan yang perlu diubah.

Urutan hanya menyusun ulang. Tidak ada join atau filter di sini, jadi tidak ada kasus yang hilang
karena nilai kolomnya kosong.
"""

import uuid
from collections.abc import Callable, Mapping
from enum import StrEnum

from sqlalchemy import case, func, literal
from sqlalchemy.sql.elements import ColumnElement

from app.modules.cases.models import Case, CaseStatus, CaseVersion


class CaseSortKey(StrEnum):
    """Kolom daftar kasus yang bermakna untuk diurutkan. Kolom Aksi dan hitungan tidak termasuk."""

    CASE_CODE = "case_code"
    TITLE = "title"
    STATUS = "status"
    SUITE = "suite"
    SPLIT_TAG = "split_tag"
    UPDATED_AT = "updated_at"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


DEFAULT_SORT = CaseSortKey.UPDATED_AT

# Alur hidup versi, bukan abjad: draft dulu, approved terakhir.
_STATUS_FLOW = (
    CaseStatus.DRAFT,
    CaseStatus.IN_REVIEW,
    CaseStatus.NEEDS_REVISION,
    CaseStatus.APPROVED,
)


def default_order(key: CaseSortKey) -> SortOrder:
    """Waktu mulai dari yang terbaru; teks dan status mulai dari awal."""
    return SortOrder.DESC if key == CaseSortKey.UPDATED_AT else SortOrder.ASC


def suite_rank(names: Mapping[uuid.UUID, str]) -> dict[uuid.UUID, int]:
    """Peringkat tiap suite menurut namanya, tanpa membedakan huruf besar kecil.

    Nama yang sama selain huruf besar kecil diurutkan menurut id, supaya dua suite tidak pernah
    mendapat peringkat yang sama. Repository tidak mengimpor modul suites, jadi peringkat dihitung
    di service dan dikirim sebagai data.
    """
    urut = sorted(names, key=lambda suite_id: (names[suite_id].lower(), str(suite_id)))
    return {suite_id: peringkat for peringkat, suite_id in enumerate(urut)}


def _title(_: Mapping[uuid.UUID, int]) -> ColumnElement:
    # Judul kosong disamakan dengan tanpa judul, keduanya di akhir.
    judul = func.lower(CaseVersion.content["title"].as_string())
    return func.nullif(judul, "")


def _status(_: Mapping[uuid.UUID, int]) -> ColumnElement:
    urutan = [(CaseVersion.status == status, i) for i, status in enumerate(_STATUS_FLOW)]
    return case(*urutan, else_=len(_STATUS_FLOW))


def _suite(rank: Mapping[uuid.UUID, int]) -> ColumnElement:
    if not rank:
        return literal(0)
    urutan = [(Case.suite_id == suite_id, peringkat) for suite_id, peringkat in rank.items()]
    return case(*urutan, else_=len(rank))


_EXPRESSIONS: dict[CaseSortKey, Callable[[Mapping[uuid.UUID, int]], ColumnElement]] = {
    CaseSortKey.CASE_CODE: lambda _: func.lower(Case.case_code),
    CaseSortKey.TITLE: _title,
    CaseSortKey.STATUS: _status,
    CaseSortKey.SUITE: _suite,
    CaseSortKey.SPLIT_TAG: lambda _: CaseVersion.split_tag,
    CaseSortKey.UPDATED_AT: lambda _: CaseVersion.updated_at,
}


def build_order_by(
    key: CaseSortKey = DEFAULT_SORT,
    order: SortOrder | None = None,
    rank: Mapping[uuid.UUID, int] | None = None,
) -> list[ColumnElement]:
    """ORDER BY untuk daftar kasus: kolom pilihan, lalu kode kasus naik sebagai kunci kedua.

    Kode kasus unik di semua suite, jadi kunci kedua membuat urutan pasti dan halaman tidak
    tumpang tindih saat nilai kolom sama. Nilai kosong selalu di akhir, di kedua arah.
    """
    ekspresi = _EXPRESSIONS[key](rank or {})
    arah = order or default_order(key)
    utama = ekspresi.desc() if arah == SortOrder.DESC else ekspresi.asc()
    return [utama.nulls_last(), Case.case_code.asc()]
