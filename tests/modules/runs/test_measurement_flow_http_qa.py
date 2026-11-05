"""QA alur sesi lewat HTTP, dari tombol Mulai sampai dibaca lagi. SCRUM-76 SCRUM-213.

Tes runtime SCRUM-249 (test_execution_runtime.py) mengecek baris database setelah
worker jalan. Tes ini membaca hasil yang sama lewat endpoint yang dipakai halaman
Eksekusi: detail sesi (AC7, AC8), daftar sesi (AC10), dan notifikasi (AC10). Titik
sambung antara yang ditulis worker dan yang dibaca endpoint adalah tempat yang
paling mudah retak.

Antrean, store, dan database nyata. Hanya provider AI yang diganti dengan provider
terkontrol, dan fixture serta helper dipakai ulang dari tes runtime supaya satu cara
menyiapkan dan menjalankan sesi.

Label: Positif = alur normal, Edge = batas perilaku.
"""

import uuid

from app.shared.security import Role
from tests.modules.cases.test_cases import _ganti
from tests.modules.runs.test_execution_runtime import Provider, runner, start

URL = "/measurements"
NOTIFIKASI = "/notifications"
KASUS = 10
ULANG = 3
PEMULAI_LAIN = uuid.UUID("00000000-0000-0000-0000-00000000f0f0")


def _jalankan(siap, **ubah):
    """Mulai satu sesi lewat HTTP lalu jalankan worker sekali; kembalikan id dan providernya."""
    sesi_id = start(siap, **ubah)
    provider = Provider()
    assert runner(siap, provider)() is True
    siap.db.expire_all()
    return sesi_id, provider


def test_detail_menampilkan_tiga_puluh_jawaban_tanpa_duplikasi(sepuluh):
    """Positif: AC3 dan AC8, 10 kasus x 3 pengulangan terbaca per kasus dan per percobaan."""
    # Arrange + Act
    sesi_id, provider = _jalankan(sepuluh)
    detail = sepuluh.sebagai(Role.ADMIN).get(f"{URL}/{sesi_id}")

    # Assert
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "finished"
    assert len(body["cases"]) == KASUS
    pasangan = set()
    for kasus in body["cases"]:
        assert [a["repetition_no"] for a in kasus["attempts"]] == [1, 2, 3]
        for percobaan in kasus["attempts"]:
            assert percobaan["status"] == "succeeded"
            assert percobaan["model_version"] == "model-v1"
            assert percobaan["answer_text"] == f"answer:{kasus['question']}"
            pasangan.add((kasus["case_id"], percobaan["repetition_no"]))
    assert len(pasangan) == KASUS * ULANG
    assert len(provider.calls) == KASUS * ULANG


def test_daftar_menampilkan_selesai_dengan_progres_penuh(sepuluh):
    """Positif: AC10, baris daftar membaca status, jumlah kasus, dan progres yang ditulis worker."""
    # Arrange + Act
    sesi_id, _ = _jalankan(sepuluh)
    daftar = sepuluh.sebagai(Role.ADMIN).get(URL)

    # Assert
    assert daftar.status_code == 200
    (baris,) = [item for item in daftar.json() if item["id"] == str(sesi_id)]
    assert baris["status"] == "finished"
    assert baris["case_count"] == KASUS
    assert baris["repetitions"] == ULANG
    assert baris["progress"] == {"completed": KASUS * ULANG, "total": KASUS * ULANG}
    assert baris["product_name"] == "AiYU"


def test_pemulai_mendapat_satu_notifikasi_dan_pengguna_lain_tidak(sepuluh, buat_pengguna):
    """Positif: AC10, satu notifikasi selesai ke pemulai; menjalankan worker lagi tidak menambah."""
    # Arrange
    sesi_id = start(sepuluh)
    jalan = runner(sepuluh, Provider())

    # Act
    assert jalan() is True
    assert jalan() is False
    pemulai = sepuluh.sebagai(Role.ADMIN).get(NOTIFIKASI)
    _ganti(sepuluh.client, buat_pengguna(Role.ADMIN, user_id=PEMULAI_LAIN, name="Admin Lain"))
    lain = sepuluh.client.get(NOTIFIKASI)

    # Assert
    assert pemulai.status_code == 200 and lain.status_code == 200
    assert pemulai.json()["total"] == 1
    (notifikasi,) = pemulai.json()["items"]
    assert notifikasi["link"] == f"/runs/{sesi_id}"
    assert notifikasi["read_at"] is None
    assert lain.json()["total"] == 0


def test_sesi_kedua_menunggu_di_daftar_sampai_yang_pertama_selesai(sepuluh):
    """Edge: AC3, hanya satu sesi berjalan; yang kedua antre dengan progres 0 lalu selesai."""
    # Arrange
    pertama, kedua = start(sepuluh), start(sepuluh)
    jalan = runner(sepuluh, Provider())
    klien = sepuluh.sebagai(Role.ADMIN)

    def status() -> dict[str, tuple[str, int]]:
        return {
            item["id"]: (item["status"], item["progress"]["completed"])
            for item in klien.get(URL).json()
        }

    # Act + Assert
    assert status() == {str(pertama): ("queued", 0), str(kedua): ("queued", 0)}
    assert jalan() is True
    sepuluh.db.expire_all()
    assert status() == {
        str(pertama): ("finished", KASUS * ULANG),
        str(kedua): ("queued", 0),
    }
    assert jalan() is True
    sepuluh.db.expire_all()
    assert status() == {
        str(pertama): ("finished", KASUS * ULANG),
        str(kedua): ("finished", KASUS * ULANG),
    }
