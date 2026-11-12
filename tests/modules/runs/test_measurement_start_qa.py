"""QA tombol Mulai: aturan endpoint yang belum dijaga tes lain. SCRUM-76 SCRUM-213.

Melengkapi test_start_endpoint.py (SCRUM-207) dari sisi HTTP: produk manual
(AC1), tidak ada jalur hapus sesi (AC10), provider penilai kosong atau bukan
UUID (kontrak yang dipakai client), dan bentuk galat BUDGET_EXCEEDED (AC5)
yang dibaca halaman Buat Pengukuran. Fixture dan helper dipakai ulang dari
test_start_endpoint supaya satu cara menyiapkan suite, kasus, dan produk.

Label: Positif = sesi dibuat, Negatif = sesi ditolak, Edge = batas perilaku.
"""

import uuid
from decimal import Decimal

import pytest

from app.modules.providers.models import MeasurementType
from app.modules.runs.models import MeasurementSession, SessionStatus
from app.shared.security import Role
from tests.modules.runs.test_start_endpoint import URL, _badan, _jumlah_sesi
from tests.modules.runs.test_summary_endpoint import _tambah_produk


def _produk_manual(db):
    return _tambah_produk(
        db,
        name="Justisio",
        measurement_type=MeasurementType.MANUAL,
        vendor_name="Justisio",
        site_url="https://justisio.test",
        provider_type=None,
        base_url=None,
        model_name=None,
        credential_encrypted=None,
        credential_hint=None,
        rate_limit_per_minute=None,
        monthly_budget_idr=None,
    )


def test_produk_manual_ditolak_dan_tidak_membuat_sesi(siap):
    """Negatif: AC1, satu pengukuran hanya untuk produk otomatis; manual tidak lolos Mulai."""
    # Arrange
    manual = _produk_manual(siap.db)
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap, product_id=str(manual.id)))

    # Assert
    assert response.status_code == 422
    assert response.json()["code"] == "PRODUCT_NOT_AUTOMATIC"
    assert _jumlah_sesi(siap.db) == 0


def test_sesi_tidak_bisa_dihapus_lewat_http(siap):
    """Negatif: AC10, sesi hanya diarsipkan; DELETE ditolak dan sesinya tetap ada."""
    # Arrange
    client = siap.sebagai(Role.ADMIN)
    sesi_id = client.post(URL, json=_badan(siap)).json()["session_id"]

    # Act
    response = client.delete(f"{URL}/{sesi_id}")

    # Assert
    assert response.status_code == 405
    siap.db.expire_all()
    tersimpan = siap.db.get(MeasurementSession, uuid.UUID(sesi_id))
    assert tersimpan is not None
    assert tersimpan.status == SessionStatus.QUEUED


@pytest.mark.parametrize("kosong", ["null-eksplisit", "tanpa-kunci"])
def test_sesi_tanpa_penilai_dibuat_dengan_penilai_kosong(siap, kosong):
    """Positif: K-14 (SCRUM-281), penilai tidak lagi dipilih saat membuat sesi; sesi tetap
    dibuat dan penilainya dipilih Admin di langkah penilaian."""
    # Arrange
    client = siap.sebagai(Role.ADMIN)
    badan = _badan(siap, judge_provider_id=None)
    if kosong == "tanpa-kunci":
        del badan["judge_provider_id"]

    # Act
    response = client.post(URL, json=badan)

    # Assert
    assert response.status_code == 201, response.text
    tersimpan = siap.db.get(MeasurementSession, uuid.UUID(response.json()["session_id"]))
    assert tersimpan.judge_provider_id is None


def test_penilai_berupa_nama_model_ditolak_422(siap):
    """Negatif: kontrak meminta UUID; nama model seperti yang tampil di dropdown tidak boleh tersimpan."""
    # Arrange
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap, judge_provider_id="deepseek-v4-flash"))

    # Assert
    assert response.status_code == 422
    assert _jumlah_sesi(siap.db) == 0


def test_galat_anggaran_membawa_angka_di_level_atas_bukan_di_details(siap):
    """Edge: AC5, client membaca sisa anggaran dan perkiraan biaya dari body galat, bukan dari details."""
    # Arrange: perkiraan di atas Rp100, anggaran tinggal Rp100
    siap.produk.monthly_budget_idr = Decimal("100.00")
    siap.db.commit()
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap))

    # Assert
    body = response.json()
    assert response.status_code == 422
    assert body["code"] == "BUDGET_EXCEEDED"
    assert body["product_name"] == "AiYU"
    assert Decimal(body["remaining_budget_idr"]) == Decimal("100.00")
    assert Decimal(body["estimated_cost_idr"]) > Decimal("100.00")
    assert body.get("details") is None
