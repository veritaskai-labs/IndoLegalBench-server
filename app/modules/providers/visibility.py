"""Siapa boleh melihat daftar pilihan produk AI untuk tujuan apa (PBI-23 AC7, AC8; D10).

Aturan murni tanpa database, supaya matriks perannya bisa diuji sendiri dan
service cukup memanggilnya sebelum membaca produk. Daftar lengkap produk
tetap hanya untuk Admin lewat /admin/providers.
"""

from enum import StrEnum

from app.modules.providers.models import MeasurementType
from app.shared.security import Role


class ProductPurpose(StrEnum):
    """Untuk apa daftar pilihan produk dipakai."""

    AUTOMATIC_RUN = "automatic_run"  # form pengukuran otomatis (PBI-11)
    MANUAL_ENTRY = "manual_entry"  # pencatatan jawaban manual (PBI-22)


# AC7: satu tujuan satu jenis; produk berjenis lain tidak dikirim sama sekali (D1 C4).
_JENIS_PER_TUJUAN = {
    ProductPurpose.AUTOMATIC_RUN: MeasurementType.AUTOMATIC,
    ProductPurpose.MANUAL_ENTRY: MeasurementType.MANUAL,
}

# D10 "Produk AI: lihat daftar": Admin semua; Kontributor nama produk manual aktif;
# Developer AI produk otomatis aktif di form pengukuran (usulan SA). Viewer tidak.
_PERAN_PER_TUJUAN = {
    ProductPurpose.AUTOMATIC_RUN: frozenset({Role.ADMIN, Role.DEVELOPER_AI}),
    ProductPurpose.MANUAL_ENTRY: frozenset({Role.ADMIN, Role.CONTRIBUTOR}),
}

# Penjaga pertama di router: peran yang tidak berhak atas tujuan apa pun langsung 403.
# Pasangan peran dan tujuan tetap dicek service lewat can_list_options.
OPTION_ROLES = frozenset().union(*_PERAN_PER_TUJUAN.values())


def measurement_type_for(purpose: ProductPurpose) -> MeasurementType:
    """Jenis produk yang boleh dipilih untuk tujuan ini."""
    return _JENIS_PER_TUJUAN[purpose]


def can_list_options(role: Role, purpose: ProductPurpose) -> bool:
    """Peran ini boleh melihat daftar pilihan produk untuk tujuan ini."""
    return role in _PERAN_PER_TUJUAN[purpose]
