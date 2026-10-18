"""Pratinjau rujukan hukum saat Author mengetik (PBI-21 AC6, AC8, AC10; SCRUM-162).

Fungsi di sini murni dan tidak punya aturan sendiri. Tiap rujukan melewati
langkah yang sama seperti simpan, dengan urutan yang sama: schema
LegalRefWrite, check_citation, lalu check_citation_types. Rujukan yang lolos
ditulis dengan citation_text, sama seperti display_text setelah simpan.
Keputusan PO 18 Okt: satu hasil per rujukan, teks hanya kalau rujukannya
sah, dan hanya masalah pertamanya.
"""

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from pydantic import ValidationError as SchemaError

from app.modules.auth.schemas import ErrorBody
from app.modules.cases.display_text import citation_text
from app.modules.cases.schemas import LegalRefPreviewItem, LegalRefStored, LegalRefWrite
from app.modules.cases.validation import (
    check_citation,
    check_citation_types,
    response_from_pydantic,
)
from app.modules.legal_refs.service import TypeName
from app.shared.exceptions import ValidationError


def preview_citations(
    legal_refs: Sequence[Any],
    *,
    aktif: Mapping[str, bool],
    nama: Mapping[str, TypeName],
    tersimpan: Collection[str],
    tahun_ini: int,
) -> list[LegalRefPreviewItem]:
    """Satu hasil per rujukan, urut sesuai kiriman.

    aktif dan nama berasal dari daftar jenis peraturan; tersimpan adalah kode
    jenis yang sudah disimpan versi terbuka kasus itu (aturan jenis nonaktif).
    """
    return [
        _preview(indeks, isi, aktif=aktif, nama=nama, tersimpan=tersimpan, tahun_ini=tahun_ini)
        for indeks, isi in enumerate(legal_refs)
    ]


def _preview(
    indeks: int,
    isi: Any,
    *,
    aktif: Mapping[str, bool],
    nama: Mapping[str, TypeName],
    tersimpan: Collection[str],
    tahun_ini: int,
) -> LegalRefPreviewItem:
    try:
        rujukan = LegalRefWrite.model_validate(isi)
    except SchemaError as galat:
        # Path dibuat sama seperti body simpan: legal_refs[i]...
        errors = [
            {**error, "loc": ("legal_refs", indeks, *error["loc"])} for error in galat.errors()
        ]
        return _gagal(ErrorBody(**response_from_pydantic(errors)))
    data = rujukan.model_dump(mode="json")
    try:
        check_citation(indeks, data, tahun_ini)
        check_citation_types(indeks, data, aktif, tersimpan)
    except ValidationError as galat:
        return _gagal(ErrorBody(code=galat.code, message=galat.message, field=galat.field))
    teks = citation_text(LegalRefStored.model_validate(data), nama)
    return LegalRefPreviewItem(display_text=teks, error=None)


def _gagal(error: ErrorBody) -> LegalRefPreviewItem:
    return LegalRefPreviewItem(display_text=None, error=error)
