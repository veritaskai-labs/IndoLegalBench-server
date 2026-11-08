"""QA hak akses dan batas input sesi manual. PBI-22 (SCRUM-176) AC1, AC5, AC8, AC11, SCRUM-238.

Desain tes: Input Space Partitioning dengan Base Choice Coverage.
Karakteristik dan bloknya:
    peran  : admin* | contributor | viewer | developer_ai
    status : not_filled* | complete | finalized
    aksi   : simpan (PUT dengan confirm)* | baca | riwayat | tandai Selesai
(* = base choice). Tes base memakai semua blok base; tiap tes lain mengganti satu
karakteristik saja, jadi kalau gagal, penyebabnya ada di karakteristik itu.

Lalu boundary value untuk dua angka di kontrak: jumlah percobaan sesi (1-5, AC1) dan
nomor percobaan saat menyimpan (1..R, AC5), serta IDOR: slot milik sesi lain.

Label: Positif = diizinkan, Negatif = ditolak, Edge = batas perilaku.
"""

import uuid

import pytest

from app.shared.security import Role
from tests.modules.runs.manual_qa import URL, baca, buat_sesi, isi, isi_semua, orang, riwayat, slot
from tests.modules.runs.test_manual_session_endpoint import _badan

PERAN = {
    "admin": Role.ADMIN,
    "contributor": Role.CONTRIBUTOR,
    "viewer": Role.VIEWER,
    "developer_ai": Role.DEVELOPER_AI,
}


def _sesi_berstatus(siap, status: str) -> dict:
    """Sesi 3 kasus x 1 percobaan dalam status D8 yang diminta."""
    sesi = buat_sesi(siap, repeat_count=1)
    if status in ("complete", "finalized"):
        isi_semua(siap, sesi["id"])
    if status == "finalized":
        assert (
            orang(siap, Role.ADMIN, "Admin Satu").post(f"{URL}/{sesi['id']}/finalize").status_code
            == 200
        )
    assert baca(siap, sesi["id"])["status"] == status
    return sesi


def _lakukan(siap, sesi: dict, aksi: str, peran: str):
    """Jalankan `aksi` sebagai pengguna berperan `peran` dan kembalikan respons HTTP."""
    tempat = slot(sesi, 0)
    nama = f"QA {peran}"
    if aksi == "simpan":
        return isi(
            siap, sesi["id"], tempat, "Jawaban QA", oleh=nama, peran=PERAN[peran], confirm=True
        )
    client = orang(siap, PERAN[peran], nama)
    if aksi == "baca":
        return client.get(f"{URL}/{sesi['id']}")
    if aksi == "riwayat":
        return riwayat(siap, sesi["id"], tempat, sebagai=nama, peran=PERAN[peran])
    return client.post(f"{URL}/{sesi['id']}/finalize")


BASE = {"peran": "admin", "status": "not_filled", "aksi": "simpan"}


@pytest.mark.parametrize(
    ("ubah", "harapan"),
    [
        pytest.param({}, (200, None), id="base: admin, belum terisi, simpan"),
        # karakteristik peran (AC11: Admin dan Kontributor mengisi; Viewer dan Dev AI tidak)
        pytest.param({"peran": "contributor"}, (200, None), id="peran kontributor"),
        pytest.param({"peran": "viewer"}, (403, "FORBIDDEN"), id="peran viewer"),
        pytest.param({"peran": "developer_ai"}, (403, "FORBIDDEN"), id="peran developer AI"),
        # karakteristik status (AC8: setelah Selesai tidak ada perubahan)
        pytest.param({"status": "complete"}, (200, None), id="status lengkap"),
        pytest.param(
            {"status": "finalized"}, (409, "MANUAL_SESSION_FINALIZED"), id="status selesai"
        ),
        # karakteristik aksi
        pytest.param({"aksi": "baca"}, (200, None), id="aksi baca"),
        pytest.param({"aksi": "riwayat"}, (200, None), id="aksi riwayat"),
        pytest.param(
            {"aksi": "tandai Selesai"}, (409, "MANUAL_SESSION_INCOMPLETE"), id="aksi Selesai"
        ),
    ],
)
def test_base_choice_peran_status_aksi(siap_manual, ubah, harapan):
    """ISP Base Choice: satu karakteristik berubah dari base di setiap kasus."""
    # Arrange
    kombinasi = {**BASE, **ubah}
    sesi = _sesi_berstatus(siap_manual, kombinasi["status"])

    # Act
    respons = _lakukan(siap_manual, sesi, kombinasi["aksi"], kombinasi["peran"])

    # Assert
    kode_status, kode_galat = harapan
    assert respons.status_code == kode_status, respons.text
    if kode_galat:
        assert respons.json()["code"] == kode_galat


@pytest.mark.parametrize("peran", ["contributor", "viewer", "developer_ai"])
def test_hanya_admin_yang_menandai_selesai(siap_manual, peran):
    """Negatif: AC8, sesi Lengkap hanya bisa ditutup Admin; peran lain 403 dan status tetap."""
    # Arrange
    sesi = _sesi_berstatus(siap_manual, "complete")

    # Act
    respons = _lakukan(siap_manual, sesi, "tandai Selesai", peran)

    # Assert
    assert respons.status_code == 403
    assert baca(siap_manual, sesi["id"])["status"] == "complete"


def test_kontributor_melihat_dan_mengisi_sesi_buatan_admin_lain(siap_manual):
    """Positif: AC11, sesi dibuat Admin A, dibaca dan diisi Kontributor yang bukan pembuatnya."""
    # Arrange
    sesi = buat_sesi(siap_manual, oleh="Admin Dua", repeat_count=1)

    # Act
    detail = baca(siap_manual, sesi["id"], sebagai="Kontributor Lain", peran=Role.CONTRIBUTOR)
    simpan = isi(siap_manual, sesi["id"], slot(sesi, 0), "Jawaban", oleh="Kontributor Lain")

    # Assert
    assert detail["created_by"] != detail["cases"][0]["attempts"][0]["filled_by"]
    assert simpan.status_code == 200


@pytest.mark.parametrize(
    ("percobaan", "harapan"),
    [
        pytest.param(0, (422, None), id="0: di bawah batas kontrak"),
        pytest.param(1, (200, None), id="1: batas bawah"),
        pytest.param(2, (200, None), id="R=2: batas atas"),
        pytest.param(
            3, (422, "MANUAL_ATTEMPT_NO_OUT_OF_RANGE"), id="R+1: di atas jumlah percobaan sesi"
        ),
    ],
)
def test_batas_nomor_percobaan_saat_menyimpan(siap_manual, percobaan, harapan):
    """Boundary value: AC5 dan catatan negatif Jira, nomor percobaan di luar sesi ditolak 422."""
    # Arrange
    sesi = buat_sesi(siap_manual, repeat_count=2)

    # Act
    respons = isi(siap_manual, sesi["id"], slot(sesi, 0, percobaan), "Jawaban", oleh="Rina")

    # Assert
    kode_status, kode_galat = harapan
    assert respons.status_code == kode_status, respons.text
    if kode_galat:
        assert respons.json()["code"] == kode_galat


@pytest.mark.parametrize(
    ("jumlah", "harapan_total"),
    [
        pytest.param(0, None, id="0: di bawah batas"),
        pytest.param(1, 3, id="1: batas bawah"),
        pytest.param(5, 15, id="5: batas atas"),
        pytest.param(6, None, id="6: di atas batas"),
    ],
)
def test_batas_jumlah_percobaan_saat_membuat_sesi(siap_manual, jumlah, harapan_total):
    """Boundary value: AC1 (standar 1, maksimal 5) dan AC12 (jumlah tersimpan untuk PBI-15)."""
    # Arrange
    client = orang(siap_manual, Role.ADMIN, "Admin Satu")

    # Act
    respons = client.post(URL, json=_badan(siap_manual, repeat_count=jumlah))

    # Assert
    if harapan_total is None:
        assert respons.status_code == 422
        return
    assert respons.status_code == 201, respons.text
    detail = baca(siap_manual, respons.json()["session_id"])
    assert (detail["repetitions"], detail["total_answers"]) == (jumlah, harapan_total)


def test_slot_milik_sesi_lain_ditolak_tanpa_menyentuh_sesi_itu(siap_manual):
    """Negatif (IDOR): catatan Jira 'kasus di luar snapshot -> 422'. Item snapshot sesi B
    dikirim ke sesi A: ditolak, dan jawaban sesi B tetap kosong."""
    # Arrange
    sesi_a = buat_sesi(siap_manual, repeat_count=1, label="Sesi A")
    sesi_b = buat_sesi(siap_manual, repeat_count=1, label="Sesi B")

    # Act
    respons = isi(siap_manual, sesi_a["id"], slot(sesi_b, 0), "Jawaban nyasar", oleh="Rina")

    # Assert
    assert respons.status_code == 422
    assert respons.json()["code"] == "MANUAL_CASE_NOT_IN_SNAPSHOT"
    assert baca(siap_manual, sesi_b["id"])["filled_answers"] == 0
    assert baca(siap_manual, sesi_a["id"])["filled_answers"] == 0


def test_riwayat_slot_sesi_lain_tidak_membocorkan_jawabannya(siap_manual):
    """Negatif (IDOR): riwayat dibaca lewat sesi A untuk item sesi B tidak mengembalikan
    jawaban sesi B."""
    # Arrange
    sesi_a = buat_sesi(siap_manual, repeat_count=1, label="Sesi A")
    sesi_b = buat_sesi(siap_manual, repeat_count=1, label="Sesi B")
    tempat_b = slot(sesi_b, 0)
    isi(siap_manual, sesi_b["id"], tempat_b, "Rahasia sesi B", oleh="Rina")
    isi(siap_manual, sesi_b["id"], tempat_b, "Rahasia sesi B v2", oleh="Rina", confirm=True)

    # Act
    respons = riwayat(siap_manual, sesi_a["id"], tempat_b)

    # Assert
    assert respons.status_code == 404
    assert "Rahasia sesi B" not in respons.text


def test_sesi_yang_tidak_ada_404_untuk_semua_aksi(siap_manual):
    """Negatif: id sesi acak tidak membuka apa pun, baik baca, simpan, riwayat, maupun Selesai."""
    # Arrange
    sesi = buat_sesi(siap_manual, repeat_count=1)
    palsu = {**sesi, "id": str(uuid.uuid4())}

    # Act
    hasil = {
        aksi: _lakukan(siap_manual, palsu, aksi, "admin").status_code
        for aksi in ("baca", "simpan", "riwayat", "tandai Selesai")
    }

    # Assert
    assert hasil == {"baca": 404, "simpan": 404, "riwayat": 404, "tandai Selesai": 404}
