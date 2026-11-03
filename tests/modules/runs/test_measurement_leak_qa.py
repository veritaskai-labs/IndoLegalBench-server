"""QA kebocoran data sensitif pada alur sesi. SCRUM-76 SCRUM-213.

Kredensial produk AI milik klien (PBI-10) tidak boleh muncul di response
Mulai, daftar sesi, detail sesi, maupun log saat sesi dibuat dan diambil
antrean (AC6: salinan produk tanpa kredensial). Yang dicari: ciphertext,
petunjuk kunci, dan alamat endpoint produk dari _tambah_produk.

Label: Negatif = data sensitif tidak boleh keluar.
"""

import logging
import uuid

from app.modules.runs.dependencies import build_session_queue
from app.shared.security import Role
from tests.modules.runs.test_start_endpoint import URL, _badan, siap  # noqa: F401

# Nilai dari _tambah_produk: credential_encrypted, credential_hint, base_url.
RAHASIA = ("terenkripsi", "9xQ2", "api.example.test")


def _bocor(teks: str) -> list[str]:
    return [nilai for nilai in RAHASIA if nilai in teks]


def test_response_mulai_daftar_dan_detail_tidak_memuat_kredensial_atau_alamat_produk(siap):  # noqa: F811
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


def test_log_saat_sesi_dibuat_dan_diambil_antrean_tidak_memuat_kredensial(siap, caplog):  # noqa: F811
    """Negatif: AC6, tidak ada log (termasuk DEBUG) yang menyebut kredensial atau alamat produk."""
    # Arrange
    client = siap.sebagai(Role.ADMIN)

    # Act
    with caplog.at_level(logging.DEBUG):
        sesi_id = client.post(URL, json=_badan(siap)).json()["session_id"]
        build_session_queue(siap.db).take_next()

    # Assert
    assert uuid.UUID(sesi_id)
    assert _bocor("\n".join(rekaman.getMessage() for rekaman in caplog.records)) == []
