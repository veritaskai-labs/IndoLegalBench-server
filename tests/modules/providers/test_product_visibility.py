"""Siapa boleh melihat daftar pilihan produk AI untuk tujuan apa. PBI-23 AC7-AC8, SCRUM-201.

Unit test aturan murni di app/modules/providers/visibility.py, tanpa database dan
tanpa HTTP. Sumber aturannya D10 baris "Produk AI: lihat daftar". Perilaku yang sama
lewat endpoint diuji di test_product_lists.py.

Label di docstring: Positif (diharapkan boleh), Negatif (harus ditolak), Edge (batas).
"""

import pytest

from app.modules.providers.models import MeasurementType
from app.modules.providers.visibility import (
    OPTION_ROLES,
    ProductPurpose,
    can_list_options,
    measurement_type_for,
)
from app.shared.security import Role


@pytest.mark.parametrize(
    ("purpose", "jenis"),
    [
        (ProductPurpose.AUTOMATIC_RUN, MeasurementType.AUTOMATIC),
        (ProductPurpose.MANUAL_ENTRY, MeasurementType.MANUAL),
    ],
)
def test_setiap_tujuan_hanya_satu_jenis(purpose, jenis):
    """Positif: pengukuran otomatis hanya produk otomatis, pencatatan manual hanya manual (AC7)."""
    # Act + Assert
    assert measurement_type_for(purpose) is jenis


@pytest.mark.parametrize(
    ("role", "purpose", "boleh"),
    [
        # Positif: peran yang berhak menurut D10.
        (Role.ADMIN, ProductPurpose.AUTOMATIC_RUN, True),
        (Role.ADMIN, ProductPurpose.MANUAL_ENTRY, True),
        (Role.CONTRIBUTOR, ProductPurpose.MANUAL_ENTRY, True),
        (Role.DEVELOPER_AI, ProductPurpose.AUTOMATIC_RUN, True),
        # Negatif: peran yang hanya berhak atas tujuan lain, atau tidak berhak sama sekali.
        (Role.CONTRIBUTOR, ProductPurpose.AUTOMATIC_RUN, False),
        (Role.DEVELOPER_AI, ProductPurpose.MANUAL_ENTRY, False),
        (Role.VIEWER, ProductPurpose.AUTOMATIC_RUN, False),
        (Role.VIEWER, ProductPurpose.MANUAL_ENTRY, False),
    ],
)
def test_matriks_peran_daftar_pilihan_sesuai_d10(role, purpose, boleh):
    """Positif dan negatif: seluruh kombinasi peran × tujuan (AC8).

    Kontributor hanya produk manual; Developer AI hanya produk otomatis di form
    pengukuran (D10, usulan SA); Viewer tidak melihat daftar produk.
    """
    # Act + Assert
    assert can_list_options(role, purpose) is boleh


def test_peran_yang_boleh_membuka_endpoint_pilihan():
    """Edge: penjaga endpoint adalah gabungan peran semua tujuan, tanpa Viewer.

    Router memakai himpunan ini untuk 403 awal; aturan per tujuan tetap dicek
    service, jadi himpunan ini tidak boleh lebih luas dari matriks di atas.
    """
    # Act + Assert
    assert {Role.ADMIN, Role.CONTRIBUTOR, Role.DEVELOPER_AI} == OPTION_ROLES
