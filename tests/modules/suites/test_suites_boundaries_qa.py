"""Batas nilai dan paginasi suite di tepi API. PBI-2 (SCRUM-69), QA (SCRUM-96).

Dua celah yang ditutup di sini, keduanya di sambungan antara schema dan
endpoint, bukan di dalam salah satunya.

Pertama, paginasi. tests/shared/test_pagination.py sudah membuktikan
PageParams menolak nilai di luar batas dan menghitung offset dengan
benar. Yang belum dibuktikan siapa pun adalah bahwa GET /suites benar
benar memakainya: tidak ada satu pun test yang pernah mengirim page atau
size ke endpoint itu. Unit yang benar tidak menjamin sambungannya
terpasang.

Kedua, panjang isian. name dibatasi 200 karakter dan description 1000,
tetapi yang pernah diuji hanya nama kosong. Batas atas adalah tempat
kesalahan off-by-one bersembunyi, dan kalau batasnya bergeser tanpa
sengaja, yang menemukan adalah Author yang kehilangan tulisannya saat
menyimpan.

Teknik yang dipakai: Input Space Partitioning. Untuk tiap batas diambil
tiga titik, yaitu tepat di bawah, tepat di batas, dan tepat di atas.
"""

import pytest

from app.shared.security import Role

NAMA_PANJANG_MAKS = 200
DESKRIPSI_PANJANG_MAKS = 1000
SIZE_MAKS = 100


def _buat(client, name: str, description: str | None = None):
    return client.post("/suites", json={"name": name, "description": description})


def _buat_banyak(client, jumlah: int) -> None:
    """Isi beberapa suite dengan nama berurutan supaya urutannya bisa diperiksa."""
    for nomor in range(jumlah):
        respons = _buat(client, f"Suite Paginasi {nomor:02d}")
        assert respons.status_code == 201, respons.text


class TestBatasPanjangIsian:
    """AC-1: suite dibuat dengan nama dan deskripsi, dengan batas yang jelas."""

    @pytest.mark.parametrize("panjang", [NAMA_PANJANG_MAKS - 1, NAMA_PANJANG_MAKS])
    def test_nama_sampai_batas_diterima(self, as_role, panjang):
        client = as_role(Role.AUTHOR)

        respons = _buat(client, "n" * panjang)

        assert respons.status_code == 201
        assert len(respons.json()["name"]) == panjang

    def test_nama_satu_karakter_melewati_batas_ditolak(self, as_role):
        client = as_role(Role.AUTHOR)

        respons = _buat(client, "n" * (NAMA_PANJANG_MAKS + 1))

        assert respons.status_code == 422

    def test_nama_satu_karakter_diterima(self, as_role):
        # Tepi bawah. min_length=1, jadi satu karakter harus cukup.
        client = as_role(Role.AUTHOR)

        assert _buat(client, "K").status_code == 201

    @pytest.mark.parametrize("panjang", [DESKRIPSI_PANJANG_MAKS - 1, DESKRIPSI_PANJANG_MAKS])
    def test_deskripsi_sampai_batas_diterima(self, as_role, panjang):
        client = as_role(Role.AUTHOR)

        respons = _buat(client, f"Deskripsi {panjang}", "d" * panjang)

        assert respons.status_code == 201
        assert len(respons.json()["description"]) == panjang

    def test_deskripsi_satu_karakter_melewati_batas_ditolak(self, as_role):
        client = as_role(Role.AUTHOR)

        respons = _buat(client, "Deskripsi kepanjangan", "d" * (DESKRIPSI_PANJANG_MAKS + 1))

        assert respons.status_code == 422

    def test_batas_yang_sama_berlaku_saat_mengubah(self, as_role):
        # PATCH memakai schema lain (SuiteUpdate), jadi batasnya bisa
        # bergeser diam-diam tanpa ada yang tahu.
        client = as_role(Role.AUTHOR)
        suite_id = _buat(client, "Suite untuk diubah").json()["id"]

        terlalu_panjang = client.patch(
            f"/suites/{suite_id}", json={"name": "n" * (NAMA_PANJANG_MAKS + 1)}
        )
        pas_di_batas = client.patch(f"/suites/{suite_id}", json={"name": "n" * NAMA_PANJANG_MAKS})

        assert terlalu_panjang.status_code == 422
        assert pas_di_batas.status_code == 200


class TestPaginasiDaftarSuite:
    """Sambungan PageParams ke GET /suites, yang selama ini tidak pernah dipanggil."""

    def test_nilai_bawaan_dipakai_saat_query_kosong(self, as_role):
        client = as_role(Role.AUTHOR)
        _buat_banyak(client, 3)

        badan = client.get("/suites").json()

        assert badan["page"] == 1
        assert badan["size"] == 20
        assert badan["total"] == 3
        assert len(badan["items"]) == 3

    def test_halaman_kedua_melanjutkan_bukan_mengulang(self, as_role):
        client = as_role(Role.AUTHOR)
        _buat_banyak(client, 5)

        pertama = client.get("/suites", params={"page": 1, "size": 2}).json()
        kedua = client.get("/suites", params={"page": 2, "size": 2}).json()

        assert len(pertama["items"]) == 2
        assert len(kedua["items"]) == 2
        id_pertama = {item["id"] for item in pertama["items"]}
        id_kedua = {item["id"] for item in kedua["items"]}
        assert id_pertama.isdisjoint(id_kedua), "halaman kedua mengulang isi halaman pertama"

    def test_total_menghitung_seluruh_suite_bukan_hanya_satu_halaman(self, as_role):
        # Kalau total ikut terpotong size, penghitung halaman di frontend
        # akan selalu menunjukkan satu halaman.
        client = as_role(Role.AUTHOR)
        _buat_banyak(client, 5)

        badan = client.get("/suites", params={"page": 1, "size": 2}).json()

        assert badan["total"] == 5
        assert len(badan["items"]) == 2

    def test_halaman_melewati_akhir_kosong_tapi_total_tetap_benar(self, as_role):
        client = as_role(Role.AUTHOR)
        _buat_banyak(client, 3)

        badan = client.get("/suites", params={"page": 9, "size": 20}).json()

        assert badan["items"] == []
        assert badan["total"] == 3
        assert badan["page"] == 9

    @pytest.mark.parametrize("size", [1, SIZE_MAKS])
    def test_size_di_tepi_batas_diterima_endpoint(self, as_role, size):
        client = as_role(Role.AUTHOR)
        _buat_banyak(client, 1)

        respons = client.get("/suites", params={"page": 1, "size": size})

        assert respons.status_code == 200
        assert respons.json()["size"] == size

    @pytest.mark.parametrize(
        "query",
        [
            {"page": 0, "size": 20},
            {"page": -1, "size": 20},
            {"page": 1, "size": 0},
            {"page": 1, "size": SIZE_MAKS + 1},
        ],
    )
    def test_nilai_di_luar_batas_ditolak_endpoint(self, as_role, query):
        # Batas yang hanya ditegakkan di PageParams tidak menolong kalau
        # endpoint-nya ternyata membaca query mentah.
        client = as_role(Role.AUTHOR)

        assert client.get("/suites", params=query).status_code == 422

    def test_paginasi_tetap_terpasang_pada_daftar_arsip(self, as_role):
        client = as_role(Role.AUTHOR)
        _buat_banyak(client, 3)
        aktif = client.get("/suites", params={"page": 1, "size": 20}).json()["items"]
        for item in aktif[:2]:
            assert client.post(f"/suites/{item['id']}/archive").status_code == 200

        arsip = client.get("/suites", params={"status": "archived", "page": 1, "size": 1}).json()

        assert arsip["total"] == 2
        assert len(arsip["items"]) == 1
