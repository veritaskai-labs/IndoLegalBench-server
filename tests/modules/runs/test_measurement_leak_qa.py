"""QA kebocoran data sensitif pada alur sesi. SCRUM-76 SCRUM-213.

Kredensial produk AI milik klien (PBI-10) tidak boleh muncul di response
Mulai, daftar sesi, detail sesi, maupun log saat sesi dibuat dan diambil
antrean (AC6: salinan produk tanpa kredensial). Yang dicari: ciphertext,
petunjuk kunci, dan alamat endpoint produk dari _tambah_produk.

Label: Negatif = data sensitif tidak boleh keluar.
"""

import logging
import uuid
from datetime import UTC, datetime

import pytest

from app.modules.runs.dependencies import build_session_queue
from app.modules.runs.models import MeasurementSession, SessionStatus
from app.shared.security import Role
from tests.modules.runs.test_start_endpoint import URL, _badan

# Nilai dari _tambah_produk: credential_encrypted, credential_hint, base_url.
RAHASIA = ("terenkripsi", "9xQ2", "api.example.test")


def _bocor(teks: str) -> list[str]:
    return [nilai for nilai in RAHASIA if nilai in teks]


def test_response_mulai_daftar_dan_detail_tidak_memuat_kredensial_atau_alamat_produk(siap):
    """Negatif: AC6, tiga response yang dibaca halaman Eksekusi bersih dari data produk."""
    # Arrange
    client = siap.sebagai(Role.ADMIN)

    # Act
    mulai = client.post(URL, json=_badan(siap))
    sesi_id = mulai.json()["session_id"]
    daftar = client.get(URL)
    detail = client.get(f"{URL}/{sesi_id}")

    # Assert
    assert [r.status_code for r in (mulai, daftar, detail)] == [201, 200, 200]
    assert _bocor(mulai.text) == []
    assert _bocor(daftar.text) == []
    assert _bocor(detail.text) == []
    assert "AiYU" in detail.text  # nama produk tetap tampil, hanya rahasianya yang tidak


def _hapus_produk(siap, monkeypatch):
    siap.produk.deleted_at = datetime.now(UTC)
    siap.db.commit()


def _galat_saat_menyalin_produk(siap, monkeypatch):
    def rusak(**_):
        raise RuntimeError("salinan produk gagal dibuat")

    monkeypatch.setattr("app.modules.runs.adapters.ProductCopy", rusak)


@pytest.mark.parametrize(
    "rusakkan",
    [_hapus_produk, _galat_saat_menyalin_produk],
    ids=["produk-dihapus", "galat-tak-terduga"],
)
def test_log_jalur_gagal_tidak_memuat_kredensial_atau_alamat_produk(
    siap, caplog, monkeypatch, rusakkan
):
    """Negatif: AC6, sesi gagal mulai menulis log (juga traceback) tanpa data sensitif produk.

    Jalur sukses tidak menulis log sama sekali; satu-satunya log aplikasi ada di jalur gagal
    (logger.warning dan logger.exception di SessionQueue.take_next), jadi jalur itu yang dijalankan.
    """
    # Arrange: sesi sudah antre, lalu langkah mulai dirusak
    client = siap.sebagai(Role.ADMIN)
    sesi_id = uuid.UUID(client.post(URL, json=_badan(siap)).json()["session_id"])
    rusakkan(siap, monkeypatch)

    # Act
    with caplog.at_level(logging.DEBUG):
        build_session_queue(siap.db).take_next()

    # Assert: jalur gagal benar-benar jalan dan menulis log aplikasi, lalu tidak ada rahasia
    siap.db.expire_all()
    assert siap.db.get(MeasurementSession, sesi_id).status == SessionStatus.FAILED
    assert "failed before start" in caplog.text
    assert any(rekaman.name.startswith("app.") for rekaman in caplog.records)
    assert _bocor(caplog.text) == []
