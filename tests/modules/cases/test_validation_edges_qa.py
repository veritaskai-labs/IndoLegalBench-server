"""Jalur tepi modul validasi dan kelengkapan kasus. PBI-3 (SCRUM-70), QA (SCRUM-111).

Sub task ini bernama "Testing editor, validation, completeness", dan modul
validasinya sendiri ditandai di docstring-nya sebagai berkas paling penting
di Sprint 1: satu tempat yang menentukan aturan untuk backend, editor,
ekspor, dan dokumentasi API sekaligus.

test_validation.py sudah menutup jalur utamanya dengan rapat. Yang tersisa
adalah jalur pertahanan terakhirnya: apa yang terjadi ketika bentuk data yang
masuk sama sekali bukan yang diharapkan, misalnya legal_refs berupa teks dan
bukan daftar, atau kelengkapan dihitung dari baris lama yang tersimpan dengan
bentuk berbeda.

Jalur itu penting justru karena jarang: ia dipanggil saat sesuatu sudah salah,
dan kalau ikut meledak, kesalahan kecil di klien berubah jadi 500 alih-alih
pesan yang bisa dibaca Author. Semuanya diuji di sini tanpa lewat HTTP,
kecuali dua yang memang hanya muncul lewat alur nyata.
"""

import uuid

import pytest

from app.modules.cases.models import Case
from app.modules.cases.validation import (
    FIELD_REQUIRED,
    VALIDATION_ERROR,
    completeness,
    field_path,
    response_from_pydantic,
    validate_payload,
)
from app.shared.exceptions import ValidationError
from app.shared.security import Role

LENGKAP = {
    "case_code": "QA-001",
    "identity": {"title": "PHK sepihak", "question": "Apakah sah?", "category": None},
    "legal_refs": [
        {"regulation_type": "uu", "regulation_number": "13", "year": 2003, "pasal": "151"}
    ],
    "answer_criteria": {
        "must_contain": ["surat"],
        "must_not_contain": [],
        "expected_conclusion": None,
    },
    "traps": [{"description": "Mencampur upah dan pesangon", "expected_model_behavior": None}],
    "split_tag": "dev",
}


def _payload(**ubah) -> dict:
    data = {**LENGKAP, **ubah}
    return data


class TestFieldPath:
    """Jalur field adalah kontrak: editor memakainya untuk menempel pesan ke baris."""

    def test_melewati_prefiks_body_dari_pydantic(self):
        assert field_path(("body", "legal_refs", 0, "pasal")) == "legal_refs[0].pasal"

    def test_indeks_di_posisi_pertama_tidak_menempel_ke_apa_pun(self):
        # Terjadi kalau Pydantic melaporkan error pada elemen daftar teratas.
        # Tanpa penanganan ini, indeksnya akan hilang dan pesan menempel di
        # baris yang salah.
        assert field_path((0, "pasal")) == "[0].pasal"

    def test_loc_kosong_menghasilkan_jalur_kosong(self):
        assert field_path(()) == ""
        assert field_path(None) == ""

    def test_hanya_body_juga_menghasilkan_jalur_kosong(self):
        assert field_path(("body",)) == ""


class TestPemetaanErrorPydantic:
    def test_daftar_error_kosong_tetap_menjawab_rapi(self):
        """FastAPI seharusnya tidak pernah mengirim daftar kosong, tetapi kalau
        terjadi, Author tetap harus menerima pesan, bukan halaman error."""
        hasil = response_from_pydantic([])

        assert hasil == {"code": VALIDATION_ERROR, "message": "Isian kasus tidak valid"}

    def test_error_tanpa_lokasi_field_jadi_pesan_umum(self):
        hasil = response_from_pydantic(
            [{"type": "value_error", "loc": ("body",), "msg": "apa pun", "input": {}}]
        )

        assert hasil["code"] == VALIDATION_ERROR
        assert "field" not in hasil

    def test_pesan_pydantic_yang_kosong_diganti_pesan_umum(self):
        hasil = response_from_pydantic(
            [{"type": "value_error", "loc": ("body", "case_code"), "msg": "   ", "input": "x"}]
        )

        assert hasil["message"] == "Isian kasus tidak valid"
        assert hasil["field"] == "case_code"

    def test_pesan_pydantic_yang_hilang_diganti_pesan_umum(self):
        hasil = response_from_pydantic(
            [{"type": "value_error", "loc": ("body", "case_code"), "input": "x"}]
        )

        assert hasil["message"] == "Isian kasus tidak valid"


class TestBentukDataYangSamaSekaliSalah:
    """Pertahanan terakhir: klien yang mengirim bentuk lain tetap dapat 422, bukan 500."""

    def test_rujukan_hukum_bukan_daftar_ditolak_dengan_rapi(self):
        with pytest.raises(ValidationError) as kena:
            validate_payload(_payload(legal_refs="uu 13"))

        assert kena.value.code == FIELD_REQUIRED
        assert kena.value.field == "legal_refs"

    def test_satu_baris_rujukan_bukan_objek_menunjuk_barisnya(self):
        # Editor mengirim daftar baris; kalau satu baris rusak, pesannya harus
        # tetap menunjuk baris itu, bukan seluruh daftar.
        with pytest.raises(ValidationError) as kena:
            validate_payload(
                _payload(
                    legal_refs=[
                        {"regulation_type": "uu", "regulation_number": "13", "pasal": "151"},
                        "rusak",
                    ]
                )
            )

        assert kena.value.field == "legal_refs[1]"
        assert kena.value.code == FIELD_REQUIRED


def _bagian_kurang(hasil: dict) -> list[str]:
    """Nama bagian yang belum terisi. Sejak SCRUM-107 tiap item missing adalah {field, message}."""
    return [item["field"] for item in hasil["missing"]]


class TestKelengkapanDariBentukYangTidakTerduga:
    """Indikator kelengkapan dihitung ulang dari data tersimpan, termasuk baris lama."""

    def test_rujukan_bukan_daftar_dihitung_belum_terisi(self):
        hasil = completeness(_payload(legal_refs="uu 13"))

        assert "legal_refs" in _bagian_kurang(hasil)

    def test_kriteria_jawaban_bukan_objek_dihitung_belum_terisi(self):
        hasil = completeness(_payload(answer_criteria="harus menyebut surat"))

        assert "answer_criteria" in _bagian_kurang(hasil)

    def test_identitas_bukan_objek_tidak_meledak(self):
        hasil = completeness(_payload(identity="PHK sepihak"))

        assert "identity.title" in _bagian_kurang(hasil)
        assert "identity.question" in _bagian_kurang(hasil)

    def test_isian_lengkap_tetap_seratus_persen(self):
        hasil = completeness(_payload())

        assert hasil["pct"] == 100
        assert hasil["missing"] == []

    def test_rujukan_yang_isinya_setengah_belum_dihitung_lengkap(self):
        hasil = completeness(_payload(legal_refs=[{"regulation_type": "uu", "pasal": "151"}]))

        assert "legal_refs" in _bagian_kurang(hasil)

    def test_kriteria_jawaban_berisi_frasa_kosong_belum_dihitung_terisi(self):
        hasil = completeness(
            _payload(
                answer_criteria={
                    "must_contain": ["   "],
                    "must_not_contain": [],
                    "expected_conclusion": None,
                }
            )
        )

        assert "answer_criteria" in _bagian_kurang(hasil)

    def test_kesimpulan_saja_sudah_menghitung_kriteria_jawaban_terisi(self):
        hasil = completeness(
            _payload(
                answer_criteria={
                    "must_contain": [],
                    "must_not_contain": [],
                    "expected_conclusion": "tidak sah",
                }
            )
        )

        assert "answer_criteria" not in _bagian_kurang(hasil)


class TestIsianOpsionalDibersihkan:
    def test_ayat_dan_huruf_yang_hanya_spasi_disimpan_sebagai_kosong(self, as_role):
        """Spasi yang tidak sengaja terketik tidak boleh tersimpan sebagai isi."""
        client = as_role(Role.AUTHOR)
        suite_id = client.post("/suites", json={"name": "S", "description": None}).json()["id"]

        respons = client.post(
            f"/suites/{suite_id}/cases",
            json=_payload(
                legal_refs=[
                    {
                        "regulation_type": "uu",
                        "regulation_number": "13",
                        "year": 2003,
                        "pasal": "151",
                        "ayat": "   ",
                        "huruf": "  ",
                    }
                ]
            ),
        )

        assert respons.status_code == 201
        rujukan = respons.json()["legal_refs"][0]
        assert rujukan["ayat"] is None
        assert rujukan["huruf"] is None

    def test_case_code_bukan_teks_diteruskan_apa_adanya_agar_pydantic_yang_menolak(self, as_role):
        """Pembersih hanya bekerja pada teks; tipe lain diserahkan ke schema.

        Kalau pembersihnya ikut memaksa tipe, pesan yang sampai ke Author jadi
        pesan pembersih, bukan pesan tipe yang sebenarnya salah.
        """
        client = as_role(Role.AUTHOR)
        suite_id = client.post("/suites", json={"name": "S", "description": None}).json()["id"]

        respons = client.post(f"/suites/{suite_id}/cases", json=_payload(case_code=12345))

        assert respons.status_code == 422
        assert respons.json()["field"] == "case_code"


class TestNilaiTersimpanYangTidakTerbaca:
    def test_kode_bentrok_dengan_suite_yang_sudah_dihapus_tetap_terbaca(self, as_role):
        """AC-3 tetap harus menjawab meski suite pemilik kode sudah tidak ada.

        case_code unik lintas seluruh tabel, jadi kode milik suite yang sudah
        dihapus tetap memblokir. Pesannya tidak boleh kosong atau meledak; ia
        harus mengakui bahwa pemiliknya ada tetapi tidak bisa ditunjuk lagi.
        """
        client = as_role(Role.AUTHOR)
        suite_lama = client.post("/suites", json={"name": "Lama", "description": None}).json()["id"]
        client.post(f"/suites/{suite_lama}/cases", json=_payload())
        assert client.delete(f"/suites/{suite_lama}").status_code == 204
        suite_baru = client.post("/suites", json={"name": "Baru", "description": None}).json()["id"]

        respons = client.post(f"/suites/{suite_baru}/cases", json=_payload())

        assert respons.status_code == 409
        badan = respons.json()
        assert badan["code"] == "CASE_CODE_TAKEN"
        assert "QA-001" in badan["message"]
        assert badan["message"].strip()
