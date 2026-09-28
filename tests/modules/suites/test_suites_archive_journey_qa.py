"""Perjalanan arsip suite yang benar-benar berisi kasus. PBI-2 (SCRUM-69), QA (SCRUM-102).

AC-4 tidak berhenti pada penolakan. Bunyinya: "Suite yang berisi kasus yang
sudah disetujui tidak dapat dihapus. Sebagai gantinya, Author dapat
mengarsipkan suite tersebut sehingga tidak muncul di daftar aktif dan tidak
dapat dipilih untuk pengukuran baru, sementara seluruh kasus, riwayat
review, dan hasil pengukurannya tetap tersimpan dan dapat dibuka kembali."

Yang sudah diuji orang lain: penolakannya. test_cases.py membuktikan DELETE
menjawab 409 dengan kasus approved sungguhan, dan test_suites.py membuktikan
pesannya menyebut arsip, tetapi dengan has_approved_case yang di-monkeypatch.

Yang belum diuji siapa pun adalah separuh keduanya: bahwa mengarsipkan benar
benar menjadi jalan keluarnya, dan bahwa isinya selamat. Kalimat "seluruh
kasus tetap tersimpan dan dapat dibuka kembali" adalah janji kepada Author
yang kasusnya sudah disetujui, dan sampai sekarang tidak ada yang menagihnya.

Semua kasus di sini dibuat lewat API dan statusnya diubah di baris yang
sebenarnya, bukan dipalsukan, sesuai catatan sub task "diuji dengan seed
status approved".
"""

import uuid

import pytest

from app.modules.cases.models import Case, CaseStatus
from app.shared.security import Role


def _suite(client, nama: str = "Ketenagakerjaan QA") -> str:
    respons = client.post("/suites", json={"name": nama, "description": "Tema uji"})
    assert respons.status_code == 201, respons.text
    return respons.json()["id"]


def _kasus(client, suite_id: str, kode: str = "QA-001") -> dict:
    badan = {
        "case_code": kode,
        "identity": {
            "title": "PHK sepihak",
            "question": "Apakah PHK tanpa surat sah?",
            "category": "ketenagakerjaan",
        },
        "legal_refs": [
            {"regulation_type": "uu", "regulation_number": "13", "year": 2003, "pasal": "151"}
        ],
        "answer_criteria": {
            "must_contain": ["surat"],
            "must_not_contain": [],
            "expected_conclusion": "tidak sah",
        },
        "traps": [
            {"description": "Mencampur upah dan pesangon", "expected_model_behavior": "menolak"}
        ],
        "split_tag": "dev",
    }
    respons = client.post(f"/suites/{suite_id}/cases", json=badan)
    assert respons.status_code == 201, respons.text
    return respons.json()


def _setujui(db_session, case_id: str) -> None:
    """Naikkan status kasus ke approved langsung di barisnya.

    Belum ada endpoint yang bisa melakukan ini: transisi status kasus baru
    ada di PBI-3 (SCRUM-106/107). Sampai ada, seed di baris adalah satu
    satunya cara menghadirkan kondisi yang dijaga AC-4.
    """
    kasus = db_session.get(Case, uuid.UUID(case_id))
    kasus.status = CaseStatus.APPROVED
    db_session.commit()


def _daftar(client, status: str) -> list[str]:
    respons = client.get("/suites", params={"status": status, "page": 1, "size": 50})
    assert respons.status_code == 200
    return [item["id"] for item in respons.json()["items"]]


class TestAC4ArsipSebagaiJalanKeluar:
    def test_hapus_ditolak_lalu_arsip_menyelamatkan_isinya(self, as_role, db_session):
        """Perjalanan penuh AC-4: ditolak, diarahkan ke arsip, isinya utuh, bisa dibuka lagi."""
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client)
        kasus = _kasus(client, suite_id)
        _setujui(db_session, kasus["id"])

        ditolak = client.delete(f"/suites/{suite_id}")
        assert ditolak.status_code == 409
        assert ditolak.json()["code"] == "SUITE_HAS_APPROVED_CASES"
        # Pesannya harus menyebut jalan keluarnya, bukan sekadar melarang.
        assert "arsip" in ditolak.json()["message"].lower()

        diarsipkan = client.post(f"/suites/{suite_id}/archive")
        assert diarsipkan.status_code == 200
        assert str(diarsipkan.json()["status"]).lower() == "archived"

        assert suite_id not in _daftar(client, "active")
        assert suite_id in _daftar(client, "archived")

        # "seluruh kasus tetap tersimpan dan dapat dibuka kembali"
        assert client.get(f"/suites/{suite_id}").status_code == 200
        tersimpan = client.get(f"/cases/{kasus['id']}")
        assert tersimpan.status_code == 200
        assert tersimpan.json()["case_code"] == kasus["case_code"]
        assert tersimpan.json()["status"] == CaseStatus.APPROVED.value

        kembali = client.post(f"/suites/{suite_id}/unarchive")
        assert kembali.status_code == 200
        assert suite_id in _daftar(client, "active")

    def test_setelah_diaktifkan_kembali_penjagaan_hapus_masih_berlaku(self, as_role, db_session):
        """Mengaktifkan kembali bukan celah untuk menghapus suite berisi kasus approved."""
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client)
        kasus = _kasus(client, suite_id)
        _setujui(db_session, kasus["id"])
        client.post(f"/suites/{suite_id}/archive")
        client.post(f"/suites/{suite_id}/unarchive")

        respons = client.delete(f"/suites/{suite_id}")

        assert respons.status_code == 409
        assert respons.json()["code"] == "SUITE_HAS_APPROVED_CASES"

    def test_suite_arsip_ditolak_karena_arsipnya_lebih_dulu(self, as_role, db_session):
        """Dua penjaga bisa berlaku sekaligus; yang menjawab adalah status arsipnya.

        Author yang mengarsipkan lalu mencoba menghapus perlu diberi tahu
        langkah yang benar (aktifkan kembali dulu), bukan alasan yang tidak
        bisa ditindaklanjuti.
        """
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client)
        kasus = _kasus(client, suite_id)
        _setujui(db_session, kasus["id"])
        client.post(f"/suites/{suite_id}/archive")

        respons = client.delete(f"/suites/{suite_id}")

        assert respons.status_code == 409
        assert respons.json()["code"] != "SUITE_HAS_APPROVED_CASES"
        assert "aktifkan" in respons.json()["message"].lower()

    def test_suite_berisi_draft_saja_tetap_boleh_dihapus(self, as_role):
        """Penjagaannya khusus kasus yang sudah disetujui, bukan semua isi."""
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client)
        _kasus(client, suite_id)

        assert client.delete(f"/suites/{suite_id}").status_code == 204


class TestAC5JumlahKasusMengikutiIsiSebenarnya:
    """AC-5 dan AC-6: penanda kosong dan jumlah kasus harus mengikuti isi nyata.

    Sampai PBI-3 mendarat, angka ini selalu nol karena cases masih stub, jadi
    tidak ada yang bisa membedakan "benar" dari "belum diisi". Sekarang bisa.
    """

    @pytest.mark.parametrize("jumlah", [1, 3])
    def test_jumlah_kasus_benar_setelah_kasus_ditambah(self, as_role, jumlah):
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client)
        for nomor in range(jumlah):
            _kasus(client, suite_id, kode=f"QA-{nomor:03d}")

        badan = client.get(f"/suites/{suite_id}").json()

        assert badan["case_count"] == jumlah
        assert badan["is_empty"] is False
        assert badan["exportable"] is True

    def test_jumlah_kasus_ikut_terbaca_di_daftar_bukan_hanya_di_detail(self, as_role):
        # AC-6: "Daftar suite menampilkan jumlah kasus di masing-masing suite
        # agar mudah dipantau." Detail dan daftar memakai jalur berbeda.
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client)
        _kasus(client, suite_id, kode="QA-A")
        _kasus(client, suite_id, kode="QA-B")

        daftar = client.get("/suites", params={"page": 1, "size": 50}).json()["items"]
        baris = next(item for item in daftar if item["id"] == suite_id)

        assert baris["case_count"] == 2
        assert baris["is_empty"] is False

    def test_kasus_milik_suite_lain_tidak_ikut_terhitung(self, as_role):
        """Hitungannya harus per suite, bukan seluruh tabel."""
        client = as_role(Role.AUTHOR)
        suite_a = _suite(client, "Ketenagakerjaan A")
        suite_b = _suite(client, "Ketenagakerjaan B")
        _kasus(client, suite_a, kode="QA-A1")
        _kasus(client, suite_a, kode="QA-A2")
        _kasus(client, suite_b, kode="QA-B1")

        assert client.get(f"/suites/{suite_a}").json()["case_count"] == 2
        assert client.get(f"/suites/{suite_b}").json()["case_count"] == 1

    def test_suite_berisi_kasus_tapi_diarsipkan_tidak_bisa_diekspor(self, as_role):
        """AC-5: tidak dapat dipilih untuk pengukuran baru selama diarsipkan."""
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client)
        _kasus(client, suite_id)
        client.post(f"/suites/{suite_id}/archive")

        badan = client.get(f"/suites/{suite_id}").json()

        assert badan["case_count"] == 1
        assert badan["is_empty"] is False
        assert badan["exportable"] is False

    def test_mengubah_deskripsi_tidak_mengusik_jumlah_kasus(self, as_role):
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client)
        _kasus(client, suite_id)

        diubah = client.patch(f"/suites/{suite_id}", json={"description": "Deskripsi baru"})

        assert diubah.status_code == 200
        assert diubah.json()["case_count"] == 1
        assert diubah.json()["exportable"] is True
