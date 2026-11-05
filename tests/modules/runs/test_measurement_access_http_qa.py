"""QA akses Developer AI dan data sensitif setelah run nyata. SCRUM-76 SCRUM-213.

AC9 dan butir 4 tiket: Developer AI boleh menjalankan kasus test tetapi tidak melihat
isinya; hasil tidak membocorkan ciphertext, petunjuk kunci, atau alamat produk; yang
dikirim ke provider hanya teks pertanyaan. Tes lain memakai data seed. Di sini sesi
dimulai Developer AI lewat HTTP, dijalankan worker nyata dengan provider terkontrol,
lalu dibaca lagi lewat HTTP.

Label: Positif = akses sesuai peran, Negatif = data sensitif tidak boleh keluar.
"""

from app.shared.security import Role
from tests.modules.runs.test_execution_runtime import Provider, runner
from tests.modules.runs.test_start_endpoint import URL, _badan

# Nilai dari _tambah_produk: credential_encrypted, credential_hint, base_url.
RAHASIA = ("terenkripsi", "9xQ2", "api.example.test")
NOTIFIKASI = "/notifications"
PERCOBAAN_PER_KASUS = 3


def _bocor(teks: str) -> list[str]:
    return [nilai for nilai in RAHASIA if nilai in teks]


def _jalankan_sebagai(siap, role: Role):
    """Sesi dimulai oleh role itu, dijalankan worker; kembalikan id, klien, dan provider."""
    klien = siap.sebagai(role)
    response = klien.post(URL, json=_badan(siap))
    assert response.status_code == 201
    sesi_id = response.json()["session_id"]
    provider = Provider()
    assert runner(siap, provider)() is True
    siap.db.expire_all()
    return sesi_id, klien, provider


def test_developer_ai_melihat_kasus_dev_utuh_dan_kasus_test_tersamar_setelah_selesai(siap):
    """Positif: AC9, Developer AI menjalankan kasus test tetapi hanya melihat statusnya."""
    # Arrange + Act
    sesi_id, klien, _ = _jalankan_sebagai(siap, Role.DEVELOPER_AI)
    detail = klien.get(f"{URL}/{sesi_id}")

    # Assert
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "finished"
    terbuka = [kasus for kasus in body["cases"] if not kasus["masked"]]
    tersamar = [kasus for kasus in body["cases"] if kasus["masked"]]
    assert len(terbuka) == 2 and len(tersamar) == 1
    for kasus in terbuka:
        assert kasus["question"] and kasus["sections"] is not None
        assert [a["answer_text"] for a in kasus["attempts"]] == [
            f"answer:{kasus['question']}"
        ] * PERCOBAAN_PER_KASUS
    (test,) = tersamar
    assert test["question"] is None
    assert test["sections"] is None
    assert test["version_no"] is None
    assert [a["status"] for a in test["attempts"]] == ["succeeded"] * PERCOBAAN_PER_KASUS
    assert [a["answer_text"] for a in test["attempts"]] == [None] * PERCOBAAN_PER_KASUS
    assert "PHK-003" not in detail.text


def test_admin_melihat_kasus_test_utuh_pada_sesi_yang_sama(siap):
    """Positif: AC9, pembeda dari Developer AI: Admin membaca isi dan jawaban kasus test."""
    # Arrange + Act
    sesi_id, _, _ = _jalankan_sebagai(siap, Role.DEVELOPER_AI)
    body = siap.sebagai(Role.ADMIN).get(f"{URL}/{sesi_id}").json()

    # Assert
    assert [kasus["masked"] for kasus in body["cases"]] == [False] * 3
    test = next(kasus for kasus in body["cases"] if kasus["case_code"] == "PHK-003")
    assert test["question"]
    assert all(a["answer_text"] for a in test["attempts"])


def test_hasil_run_tidak_membocorkan_kredensial_atau_alamat_produk(siap):
    """Negatif: AC6, detail, daftar, dan notifikasi setelah run nyata bersih dari data produk."""
    # Arrange + Act
    sesi_id, _, _ = _jalankan_sebagai(siap, Role.ADMIN)
    bacaan = []
    for role in (Role.ADMIN, Role.DEVELOPER_AI):
        klien = siap.sebagai(role)
        bacaan += [
            klien.get(f"{URL}/{sesi_id}"),
            klien.get(URL),
            klien.get(NOTIFIKASI),
        ]

    # Assert
    assert [r.status_code for r in bacaan] == [200] * 6
    assert [_bocor(r.text) for r in bacaan] == [[]] * 6
    assert "AiYU" in bacaan[0].text  # nama produk tetap tampil, hanya rahasianya yang tidak


def test_provider_hanya_menerima_teks_pertanyaan_tanpa_rahasia(siap):
    """Negatif: AC4, setiap panggilan membawa pertanyaan apa adanya dan tidak membawa data produk."""
    # Arrange + Act
    sesi_id, klien, provider = _jalankan_sebagai(siap, Role.ADMIN)
    pertanyaan = {kasus["question"] for kasus in klien.get(f"{URL}/{sesi_id}").json()["cases"]}

    # Assert
    assert len(provider.calls) == 3 * PERCOBAAN_PER_KASUS
    assert {prompt for prompt, _ in provider.calls} == pertanyaan
    assert _bocor(repr(provider.calls)) == []
