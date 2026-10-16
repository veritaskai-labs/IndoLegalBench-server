"""Aturan bisnis modul legal_refs (PBI-21, SCRUM-160).

Modul lain memakai fungsi di sini, bukan tabel legal_refs secara langsung.
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.modules.legal_refs import repository
from app.modules.legal_refs.citations import to_citations
from app.modules.legal_refs.models import (
    LegalRef,
    LegalRefConnector,
    LegalRefKind,
    LegalRefLink,
    LegalRefLinkForm,
    RegulationType,
)
from app.modules.legal_refs.schemas import RegulationTypeRead
from app.modules.legal_refs.seed import RegulationTypeSeed
from app.modules.legal_refs.type_lookup import RegulationTypeIndex


class TypeCatalog:
    """Semua jenis peraturan, aktif dan nonaktif, dimuat sekali per request."""

    def __init__(self, jenis: Sequence[RegulationType]) -> None:
        # code -> aktif, dipakai cek jenis saat menyimpan (AC2).
        self.active: dict[str, bool] = {item.code: item.is_active for item in jenis}
        # code -> (label, singkatan), dipakai penulisan display_text.
        self.names: dict[str, tuple[str, str | None]] = {
            item.code: (item.label, item.singkatan) for item in jenis
        }
        self._lookup = RegulationTypeIndex(
            RegulationTypeSeed(
                code=item.code,
                label=item.label,
                singkatan=item.singkatan,
                urutan=item.urutan,
                is_active=item.is_active,
            )
            for item in jenis
        )

    def citations(self, raw: Any) -> list[dict[str, Any]]:
        """content["legal_refs"] dalam bentuk PBI-21, termasuk rujukan Sprint 1 (AC9)."""
        return to_citations(raw, self._lookup)


def replace_for_version(
    db: Session, version_id: uuid.UUID, citations: list[dict[str, Any]]
) -> None:
    """Tulis ulang baris rujukan satu versi terbuka dari daftar bentuk PBI-21. Tidak commit.

    Dipanggil di transaksi yang sama dengan penulisan content, jadi keduanya
    tersimpan atau batal bersama. Pemanggil yang menjamin versinya masih
    terbuka; versi approved tidak pernah ditulis ulang.
    """
    repository.delete_for_version(db, version_id)
    repository.add(
        db,
        [_ref_row(version_id, urutan, isi) for urutan, isi in enumerate(citations, start=1)],
    )


def _ref_row(version_id: uuid.UUID, urutan: int, isi: dict[str, Any]) -> LegalRef:
    return LegalRef(
        case_version_id=version_id,
        order=urutan,
        kind=LegalRefKind(isi["kind"]),
        free_text=isi.get("free_text"),
        links=[
            LegalRefLink(
                position=posisi,
                connector=LegalRefConnector(link["connector"]),
                form=LegalRefLinkForm(link["form"]),
                regulation_type=link["regulation_type"],
                number=link["number"],
                year=link.get("year"),
                about=link.get("about"),
                pasal=link.get("pasal"),
                angka=link.get("angka"),
                ayat=link.get("ayat"),
                huruf=link.get("huruf"),
            )
            for posisi, link in enumerate(isi["links"], start=1)
        ],
    )


def load_catalog(db: Session) -> TypeCatalog:
    return TypeCatalog(repository.regulation_types(db, include_inactive=True))


def list_regulation_types(
    db: Session, *, include_inactive: bool = False
) -> list[RegulationTypeRead]:
    """Daftar untuk dropdown editor (AC2)."""
    return [
        RegulationTypeRead.model_validate(baris)
        for baris in repository.regulation_types(db, include_inactive=include_inactive)
    ]
