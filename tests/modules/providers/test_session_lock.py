"""Kunci produk untuk sesi pengukuran baru. SCRUM-76 SCRUM-207, kontrak #90 (SCRUM-204).

providers.service.lock_for_new_session mengunci baris produk (FOR SHARE) sampai
transaksi pembuat sesi selesai dan mengembalikan jenis pengukuran yang terbaca
setelah kunci didapat. Modul runs memanggilnya sebelum menyimpan sesi, supaya
ganti jenis yang bersamaan tidak meloloskan sesi otomatis untuk produk Manual
(D1 C6, PBI-23 AC4). SQLite mengabaikan kunci baris; perilaku kunci diuji di
tests/integration/test_start_session_lock_pg.py. Di sini hanya hasil bacaannya.

Label: Positif = jenis terbaca, Negatif = produk tidak bisa dikunci.
"""

from datetime import UTC, datetime

import pytest

from app.modules.providers import service
from app.modules.providers.models import MeasurementType
from app.shared.exceptions import NotFoundError
from tests.modules.runs.test_summary_endpoint import _tambah_produk


def test_jenis_produk_terbaca_setelah_dikunci(db_session):
    """Positif: produk Otomatis yang masih ada mengembalikan jenisnya."""
    # Arrange
    produk = _tambah_produk(db_session)

    # Act
    jenis = service.lock_for_new_session(db_session, produk.id)

    # Assert
    assert jenis == MeasurementType.AUTOMATIC


def test_jenis_yang_dibaca_adalah_nilai_di_database_bukan_salinan_di_session(db_session):
    """Positif: nilai lama di identity map diganti nilai database, seperti for_update di #90."""
    # Arrange: salinan di memori sengaja berbeda dan tidak di-flush
    produk = _tambah_produk(db_session)
    with db_session.no_autoflush:
        produk.measurement_type = MeasurementType.MANUAL

        # Act
        jenis = service.lock_for_new_session(db_session, produk.id)

    # Assert
    assert jenis == MeasurementType.AUTOMATIC


def test_produk_yang_dihapus_tidak_bisa_dikunci(db_session):
    """Negatif: produk yang sudah dihapus menjadi 404 dengan kode yang sama seperti ringkasan."""
    # Arrange
    produk = _tambah_produk(db_session, deleted_at=datetime.now(UTC))

    # Act
    with pytest.raises(NotFoundError) as tertangkap:
        service.lock_for_new_session(db_session, produk.id)

    # Assert
    assert tertangkap.value.code == "AI_PRODUCT_NOT_FOUND"
