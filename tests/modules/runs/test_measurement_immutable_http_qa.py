"""QA hasil sesi tidak berubah setelah selesai, dibaca lewat HTTP. SCRUM-76 SCRUM-213.

AC6 dan AC8: perubahan kasus atau produk setelah sesi berjalan tidak memengaruhi hasil
yang sudah ada, dan hasil tidak boleh diubah atau dihapus. Tes basis data (PostgreSQL)
menjaga triggernya. Di sini yang dijaga adalah apa yang dilihat halaman Eksekusi: detail
sesi yang dibaca sebelum dan sesudah kasus dan produk berubah harus sama persis, dan
tidak ada jalur HTTP untuk mengubah atau menghapus sesi.

Label: Positif = hasil tetap, Negatif = perubahan ditolak.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.shared.security import Role
from tests.modules.cases.test_cases import _badan, _ganti, _setujui
from tests.modules.runs.test_execution_runtime import Provider, runner, start

URL = "/measurements"
JUDUL_BARU = "PHK sepihak tanpa surat peringatan"


def _selesai(siap):
    sesi_id = start(siap)
    assert runner(siap, Provider())() is True
    siap.db.expire_all()
    return sesi_id


def _detail(siap, sesi_id):
    response = siap.sebagai(Role.ADMIN).get(f"{URL}/{sesi_id}")
    assert response.status_code == 200
    return response.json()


def _revisi_dan_setujui(siap, buat_pengguna, case_id: str) -> None:
    """Penulis kasus membuka versi 2 dengan judul baru, lalu versi itu disetujui."""
    penulis = siap.client
    _ganti(penulis, buat_pengguna(Role.CONTRIBUTOR))
    assert penulis.post(f"/cases/{case_id}/versions").status_code == 201
    revisi = _badan()
    revisi["identity"] = {**revisi["identity"], "title": JUDUL_BARU}
    assert penulis.put(f"/cases/{case_id}", json=revisi).status_code == 200
    _setujui(siap.db, case_id)
    live = siap.sebagai(Role.ADMIN).get(f"/cases/{case_id}")
    assert live.status_code == 200
    assert live.json()["identity"]["title"] == JUDUL_BARU  # kasus hidup benar-benar berubah


def test_detail_tetap_sama_setelah_kasus_disetujui_ulang_dan_produk_diubah_lalu_dihapus(
    siap, buat_pengguna
):
    """Positif: AC6, snapshot dan salinan produk tidak ikut berubah; detail sama persis."""
    # Arrange
    sesi_id = _selesai(siap)
    sebelum = _detail(siap, sesi_id)
    case_id = sebelum["cases"][0]["case_id"]

    # Act: kasus hidup menjadi versi 2, produk diganti model dan harganya, lalu dihapus
    _revisi_dan_setujui(siap, buat_pengguna, case_id)
    siap.produk.model_name = "model-lain"
    siap.produk.price_output_usd_per_mtok = Decimal("99")
    siap.db.commit()
    siap.produk.deleted_at = datetime.now(UTC)
    siap.db.commit()
    siap.db.expire_all()
    sesudah = _detail(siap, sesi_id)

    # Assert
    assert sesudah == sebelum
    kasus = next(item for item in sesudah["cases"] if item["case_id"] == case_id)
    assert kasus["version_no"] == 1
    assert kasus["sections"]["identity.title"] != JUDUL_BARU
    assert {a["model_version"] for item in sesudah["cases"] for a in item["attempts"]} == {
        "model-v1"
    }
    assert sesudah["product_name"] == "AiYU"


@pytest.mark.parametrize("metode", ["PUT", "PATCH", "DELETE", "POST"])
def test_sesi_selesai_tidak_bisa_diubah_atau_dihapus_lewat_http(siap, metode):
    """Negatif: AC8 dan AC10, tidak ada jalur ubah atau hapus; sesinya tetap utuh."""
    # Arrange
    sesi_id = _selesai(siap)
    sebelum = _detail(siap, sesi_id)

    # Act
    response = siap.sebagai(Role.ADMIN).request(metode, f"{URL}/{sesi_id}", json={})

    # Assert
    assert response.status_code == 405
    assert _detail(siap, sesi_id) == sebelum
    assert sebelum["status"] == "finished"
