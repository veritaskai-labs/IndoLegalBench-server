"""Bentuk error kasus yang dibaca frontend. PBI-3 (SCRUM-70), QA (SCRUM-111).

Sub task ini menuntut satu hal yang tidak dimiliki repo mana pun sendirian:
"error FE dan error BE (422) untuk input yang sama harus identik".

Frontend membaca badan error lewat `parseError` di src/lib/apiClient.ts dan
hanya mengenal tiga kunci: `code`, `message`, dan `field`. Nilai `field`
dipakai untuk menempelkan pesan ke baris yang tepat di editor, misalnya
`legal_refs[1].pasal` menandai baris rujukan kedua.

Test editor di repo client membuktikan pesan itu tampil di tempat yang benar,
tetapi memakai respons tiruan yang ditulis tangan. Tidak ada satu pun test
yang membuktikan backend benar-benar mengirim bentuk itu. Kalau format jalur
berubah menjadi `legal_refs.1.pasal`, atau kunci `field` hilang, editor tetap
menampilkan pesan tetapi di tempat yang salah, atau tidak sama sekali, dan
seluruh test di kedua repo tetap hijau.

Berkas ini yang menagihnya. Isinya bukan menguji ulang validasi, melainkan
mengunci bentuk jawaban yang sudah menjadi tanggungan frontend.
"""

import json
import re

import pytest

from app.shared.security import Role

# Kunci yang dibaca parseError di client. Tidak boleh berkurang.
KUNCI_WAJIB = {"code", "message"}

# Bentuk jalur field yang diharapkan editor: nama.nama[indeks].nama
POLA_FIELD = re.compile(r"^[a-z_]+(\[\d+\])?(\.[a-z_]+(\[\d+\])?)*$")

BADAN = {
    "case_code": "QA-001",
    "identity": {"title": "PHK sepihak", "question": "Apakah sah?", "category": None},
    "legal_refs": [
        {"regulation_type": "uu", "regulation_number": "13", "year": 2003, "pasal": "151"},
        {"regulation_type": "uu", "regulation_number": "13", "year": 2003, "pasal": "152"},
    ],
    "answer_criteria": {
        "must_contain": ["surat"],
        "must_not_contain": [],
        "expected_conclusion": None,
    },
    "traps": [{"description": "Mencampur upah dan pesangon", "expected_model_behavior": None}],
    "split_tag": "dev",
}


def _badan(**ubah) -> dict:
    salinan = json.loads(json.dumps(BADAN))
    salinan.update(ubah)
    return salinan


def _suite(client, nama: str) -> str:
    respons = client.post("/suites", json={"name": nama, "description": None})
    assert respons.status_code == 201, respons.text
    return respons.json()["id"]


def _tanpa_pasal(baris: int) -> list[dict]:
    refs = json.loads(json.dumps(BADAN["legal_refs"]))
    refs[baris].pop("pasal")
    return refs


class TestBentukBadanError:
    """Tiga kunci itu kontrak lintas repo, bukan detail implementasi."""

    @pytest.mark.parametrize(
        "nama,ubah",
        [
            ("pasal hilang", {"legal_refs": _tanpa_pasal(1)}),
            ("split_tag kosong", {"split_tag": ""}),
            ("case_code tidak sah", {"case_code": "!"}),
            (
                "judul kepanjangan",
                {"identity": {"title": "j" * 301, "question": "Sah?", "category": None}},
            ),
            (
                "jebakan tanpa deskripsi",
                {"traps": [{"description": "", "expected_model_behavior": None}]},
            ),
        ],
    )
    def test_setiap_penolakan_memakai_kunci_yang_dibaca_frontend(self, as_role, nama, ubah):
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client, "Ketenagakerjaan QA")

        respons = client.post(f"/suites/{suite_id}/cases", json=_badan(**ubah))

        assert respons.status_code == 422, nama
        badan = respons.json()
        assert set(badan) >= KUNCI_WAJIB, (
            f"{nama}: badan error kehilangan kunci {KUNCI_WAJIB - set(badan)}"
        )
        assert isinstance(badan["code"], str) and badan["code"]
        assert isinstance(badan["message"], str) and badan["message"].strip()
        # field boleh tidak ada, tetapi kalau ada harus string, bukan objek.
        if "field" in badan:
            assert isinstance(badan["field"], str)

    def test_jalur_field_memakai_kurung_siku_bukan_titik(self, as_role):
        """Editor menempelkan pesan ke baris lewat jalur ini.

        `legal_refs[1].pasal` menandai baris rujukan kedua. Kalau formatnya
        berubah jadi `legal_refs.1.pasal`, pesannya tetap muncul tetapi tidak
        lagi menempel di baris mana pun, dan tidak ada yang merah.
        """
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client, "Ketenagakerjaan QA")

        respons = client.post(f"/suites/{suite_id}/cases", json=_badan(legal_refs=_tanpa_pasal(1)))

        assert respons.json()["field"] == "legal_refs[1].pasal"

    @pytest.mark.parametrize("baris", [0, 1])
    def test_jalur_menunjuk_baris_yang_benar_bukan_selalu_yang_pertama(self, as_role, baris):
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client, "Ketenagakerjaan QA")

        respons = client.post(
            f"/suites/{suite_id}/cases", json=_badan(legal_refs=_tanpa_pasal(baris))
        )

        assert respons.json()["field"] == f"legal_refs[{baris}].pasal"

    def test_jalur_bersarang_tetap_terbaca(self, as_role):
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client, "Ketenagakerjaan QA")

        respons = client.post(
            f"/suites/{suite_id}/cases",
            json=_badan(identity={"title": "j" * 301, "question": "Sah?", "category": None}),
        )

        field = respons.json()["field"]
        assert field == "identity.title"
        assert POLA_FIELD.match(field), f"jalur {field!r} di luar format yang dikenal editor"

    def test_jalur_pada_daftar_jebakan_menunjuk_barisnya(self, as_role):
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client, "Ketenagakerjaan QA")

        respons = client.post(
            f"/suites/{suite_id}/cases",
            json=_badan(traps=[{"description": "", "expected_model_behavior": None}]),
        )

        assert respons.json()["field"] == "traps[0].description"


class TestKodeYangDicabangkanFrontend:
    """Editor bercabang berdasarkan `code`. Tiap cabang harus benar-benar ada."""

    def test_field_required_untuk_isian_wajib_yang_kosong(self, as_role):
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client, "Ketenagakerjaan QA")

        respons = client.post(f"/suites/{suite_id}/cases", json=_badan(legal_refs=_tanpa_pasal(0)))

        assert respons.json()["code"] == "FIELD_REQUIRED"

    def test_split_tag_required_didahulukan_agar_pesannya_khusus(self, as_role):
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client, "Ketenagakerjaan QA")

        respons = client.post(f"/suites/{suite_id}/cases", json=_badan(split_tag=""))

        badan = respons.json()
        assert badan["code"] == "SPLIT_TAG_REQUIRED"
        assert badan["field"] == "split_tag"

    def test_validation_error_untuk_pelanggaran_yang_bukan_soal_kosong(self, as_role):
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client, "Ketenagakerjaan QA")

        respons = client.post(
            f"/suites/{suite_id}/cases",
            json=_badan(identity={"title": "j" * 301, "question": "Sah?", "category": None}),
        )

        assert respons.json()["code"] == "VALIDATION_ERROR"

    def test_case_code_taken_menyebut_suite_pemiliknya(self, as_role):
        """AC-3: sistem memberi tahu bila ID sudah dipakai di suite lain.

        Nama suite di dalam pesan adalah satu-satunya petunjuk Author untuk
        menemukan kasus yang bentrok, dan editor menampilkannya apa adanya.
        """
        client = as_role(Role.AUTHOR)
        suite_a = _suite(client, "Ketenagakerjaan A")
        suite_b = _suite(client, "Ketenagakerjaan B")
        client.post(f"/suites/{suite_a}/cases", json=BADAN)

        respons = client.post(f"/suites/{suite_b}/cases", json=_badan())

        assert respons.status_code == 409
        badan = respons.json()
        assert badan["code"] == "CASE_CODE_TAKEN"
        assert "QA-001" in badan["message"]
        assert "Ketenagakerjaan A" in badan["message"], "pesan tidak menyebut suite pemilik kode"

    def test_suite_tidak_aktif_punya_kode_sendiri_agar_bisa_jadi_spanduk(self, as_role):
        """Editor menampilkannya sebagai spanduk di atas formulir, bukan di satu field."""
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client, "Ketenagakerjaan QA")
        client.post(f"/suites/{suite_id}/archive")

        respons = client.post(f"/suites/{suite_id}/cases", json=BADAN)

        badan = respons.json()
        assert badan["code"] == "SUITE_NOT_ACTIVE"
        assert "field" not in badan or badan["field"] is None


class TestPesanTidakMembocorkanIsiDalam:
    @pytest.mark.parametrize(
        "ubah",
        [
            {"legal_refs": _tanpa_pasal(0)},
            {"split_tag": ""},
            {"case_code": "!"},
            {"identity": {"title": "j" * 301, "question": "Sah?", "category": None}},
        ],
    )
    def test_pesan_tidak_memuat_jejak_teknis(self, as_role, ubah):
        """OWASP A05: kegagalan tidak boleh membocorkan isi dalam sistem."""
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client, "Ketenagakerjaan QA")

        pesan = client.post(f"/suites/{suite_id}/cases", json=_badan(**ubah)).json()["message"]

        for bocoran in ("Traceback", "sqlalchemy", "psycopg", "app/modules", "app\\modules"):
            assert bocoran.lower() not in pesan.lower(), f"pesan memuat {bocoran!r}"

    def test_badan_error_tidak_membawa_kunci_tak_terduga(self, as_role):
        """Kunci tambahan bukan salah, tetapi harus disengaja dan diumumkan.

        Frontend mengabaikan kunci yang tidak dikenalnya, jadi kunci baru yang
        muncul diam-diam berarti ada informasi yang dikirim tetapi tidak pernah
        sampai ke pengguna.
        """
        client = as_role(Role.AUTHOR)
        suite_id = _suite(client, "Ketenagakerjaan QA")

        badan = client.post(f"/suites/{suite_id}/cases", json=_badan(split_tag="")).json()

        assert set(badan) <= {"code", "message", "field"}, (
            f"kunci baru pada badan error: {set(badan) - {'code', 'message', 'field'}}"
        )
