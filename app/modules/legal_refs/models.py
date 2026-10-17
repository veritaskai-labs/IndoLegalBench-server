"""Tabel database milik modul legal_refs.

ATURAN: file ini hanya boleh diimpor dari dalam app/modules/legal_refs/.

PBI-21, SCRUM-159. Model datar mengikuti tiket: satu rujukan (legal_refs)
punya satu atau lebih link peraturan (legal_ref_links), masing-masing
dengan satu nomor. Daftar jenis peraturan ada di regulation_types.

regulation_types.code dirujuk legal_ref_links.regulation_type, jadi code
tidak boleh diubah setelah seed masuk. Mengurangi jenis dilakukan lewat
is_active, bukan menghapus baris.

Order dan position dimulai dari 1. Kolom "order" adalah kata reservasi SQL,
SQLAlchemy mengutipnya otomatis, tapi SQL mentah harus mengutipnya sendiri.
"""

import uuid
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database import Base


class LegalRefKind(StrEnum):
    """Rujukan terstruktur (punya link) atau teks bebas (AC7)."""

    STRUCTURED = "structured"
    FREE_TEXT = "free_text"


class LegalRefLinkForm(StrEnum):
    """Seberapa dalam satu link menunjuk peraturannya (AC4).

    Bentuk ada di tiap link, bukan di rujukan, karena satu rangkaian bisa
    punya kedalaman berbeda: Pasal 156 UU 13/2003 jo. Pasal II Angka 5
    UU 6/2023 memakai pasal di link pertama dan pasal_angka di link kedua.
    """

    NAME_ONLY = "name_only"
    PASAL = "pasal"
    PASAL_ANGKA = "pasal_angka"


class LegalRefConnector(StrEnum):
    """Penghubung ke link sebelumnya. none hanya untuk link pertama (AC6)."""

    NONE = "none"
    JO = "jo"
    JIS = "jis"


def _enum(kelas: type[StrEnum], nama: str) -> Enum:
    """Simpan nilai enum, bukan nama member Python."""
    return Enum(
        kelas,
        name=nama,
        values_callable=lambda members: [member.value for member in members],
    )


class RegulationType(Base):
    """Satu jenis peraturan resmi. Di-seed dari daftar JDIH BPK RI (AC2)."""

    __tablename__ = "regulation_types"

    code: Mapped[str] = mapped_column(String(150), primary_key=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    singkatan: Mapped[str | None] = mapped_column(String(50), nullable=True)
    urutan: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )


class LegalRef(Base):
    """Satu rujukan hukum pada satu versi kasus."""

    __tablename__ = "legal_refs"
    __table_args__ = (
        UniqueConstraint("case_version_id", "order", name="uq_legal_refs_case_version_id_order"),
        CheckConstraint('"order" >= 1', name="ck_legal_refs_order_positive"),
        CheckConstraint(
            "(kind = 'free_text' AND free_text IS NOT NULL AND length(trim(free_text)) > 0)"
            " OR (kind <> 'free_text' AND free_text IS NULL)",
            name="ck_legal_refs_free_text_only_for_free_text_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("case_versions.id", name="fk_legal_refs_case_version_id"),
        nullable=False,
    )
    order: Mapped[int] = mapped_column("order", Integer, nullable=False)
    kind: Mapped[LegalRefKind] = mapped_column(
        _enum(LegalRefKind, "legal_ref_kind_enum"), nullable=False
    )
    free_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    links: Mapped[list["LegalRefLink"]] = relationship(
        back_populates="legal_ref",
        cascade="all, delete-orphan",
        order_by="LegalRefLink.position",
    )


class LegalRefLink(Base):
    """Satu peraturan di dalam rujukan, dengan bagian pasal sampai huruf."""

    __tablename__ = "legal_ref_links"
    __table_args__ = (
        UniqueConstraint(
            "legal_ref_id", "position", name="uq_legal_ref_links_legal_ref_id_position"
        ),
        CheckConstraint("position >= 1", name="ck_legal_ref_links_position_positive"),
        CheckConstraint(
            "(position = 1 AND connector = 'none') OR (position > 1 AND connector <> 'none')",
            name="ck_legal_ref_links_connector_matches_position",
        ),
        CheckConstraint("year >= 1 AND year <= 9999", name="ck_legal_ref_links_year_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    legal_ref_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("legal_refs.id", name="fk_legal_ref_links_legal_ref_id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    connector: Mapped[LegalRefConnector] = mapped_column(
        _enum(LegalRefConnector, "legal_ref_connector_enum"), nullable=False
    )
    form: Mapped[LegalRefLinkForm] = mapped_column(
        _enum(LegalRefLinkForm, "legal_ref_link_form_enum"), nullable=False
    )
    regulation_type: Mapped[str] = mapped_column(
        String(150),
        ForeignKey("regulation_types.code", name="fk_legal_ref_links_regulation_type"),
        nullable=False,
    )
    number: Mapped[str] = mapped_column(String(100), nullable=False)
    # Kosong hanya untuk rujukan Sprint 1 yang dulu boleh tanpa tahun (AC9).
    # Penyimpanan baru wajib berisi tahun, dijaga validasi API, bukan DB.
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    about: Mapped[str | None] = mapped_column(Text, nullable=True)
    pasal: Mapped[str | None] = mapped_column(String(40), nullable=True)
    angka: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ayat: Mapped[str | None] = mapped_column(String(20), nullable=True)
    huruf: Mapped[str | None] = mapped_column(String(8), nullable=True)

    legal_ref: Mapped[LegalRef] = relationship(back_populates="links")
