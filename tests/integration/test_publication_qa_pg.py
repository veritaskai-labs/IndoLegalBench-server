"""QA penerbitan laporan dan daftar laporan terbit. PBI-17 (SCRUM-84) AC9, AC10, AC12; SCRUM-336.

POST /reports/{id}/publish dan GET /published-reports di atas laporan sungguhan: dua produk
diukur dan dinilai lewat API (tests/trend_qa_pg.py), lalu laporan dibuat lewat POST /reports.
Desain: state transition Tersimpan -> Terbit -> Digantikan, matriks peran untuk tombol
Terbitkan, dan decision table jenis nilai laporan x hasil terbit.

Label: Positif = boleh dan berhasil, Negatif = ditolak, Edge = batas perilaku.
"""

import pytest
from sqlalchemy import func, select

from app.main import app
from app.modules.notifications.models import Notification
from app.shared.security import get_current_user
from tests.trend_qa_pg import (
    ADMIN,
    DEV_AI,
    HANYA_POSTGRES,
    KONTRIBUTOR,
    SEMUA,
    VIEWER,
    buat_laporan,
    sesi_manual,
    sesi_otomatis,
    waktu_di_bulan,
)

pytestmark = HANYA_POSTGRES


@pytest.fixture
def sesi(dunia_tren):
    """AiYU (otomatis) dan LegalHero (manual), keduanya selesai dan dinilai bulan ini."""
    return dunia_tren, [
        sesi_otomatis(dunia_tren, "AiYU", kapan=waktu_di_bulan(0)),
        sesi_manual(dunia_tren, "LegalHero", kapan=waktu_di_bulan(0)),
    ]


def _terbitkan(d, laporan: dict, oleh=ADMIN, **badan):
    return d.sebagai(oleh).post(f"/reports/{laporan['id']}/publish", json=badan)


def _daftar_terbit(d, oleh=ADMIN) -> dict:
    respons = d.sebagai(oleh).get("/published-reports")
    assert respons.status_code == 200, respons.text
    return respons.json()


def _status(d, laporan: dict, oleh=ADMIN) -> int | str:
    respons = d.sebagai(oleh).get(f"/reports/{laporan['id']}")
    return respons.json()["status"] if respons.status_code == 200 else respons.status_code


def test_laporan_terbit_muncul_di_daftar_untuk_semua_pengguna_login(sesi):
    """Positif: AC9, Admin menerbitkan laporan nilai test; laporan itu muncul sebagai versi 1
    di daftar terbit yang dibaca setiap peran, dan bisa dibuka setiap peran. Sebelum
    diterbitkan, daftar itu kosong: membuat laporan tidak pernah menerbitkannya."""
    # Arrange
    d, sesi_id = sesi
    laporan = buat_laporan(d, sesi_id, judul="Perbandingan November")
    sebelum = _daftar_terbit(d)

    # Act
    terbit = _terbitkan(d, laporan)

    # Assert
    assert sebelum["total"] == 0
    assert terbit.status_code == 201, terbit.text
    assert (terbit.json()["version_no"], terbit.json()["supersedes_id"]) == (1, None)
    for orang in SEMUA:
        (baris,) = _daftar_terbit(d, orang)["items"]
        assert (baris["title"], baris["version_no"], baris["report_id"]) == (
            "Perbandingan November",
            1,
            laporan["id"],
        )
        assert sorted(baris["product_labels"]) == ["AiYU", "LegalHero"]
        assert _status(d, laporan, orang) == "published"


def test_ganti_versi_menyimpan_versi_lama_yang_tetap_bisa_dibuka(sesi):
    """Positif: AC9 sebagai state transition, laporan pengganti terbit sebagai versi 2; daftar
    hanya memuat versi yang berlaku, dan versi lama menjadi Digantikan tetapi tetap tersimpan
    dan bisa dibuka setiap peran."""
    # Arrange
    d, sesi_id = sesi
    lama = buat_laporan(d, sesi_id, judul="Perbandingan versi 1")
    versi_1 = _terbitkan(d, lama).json()
    baru = buat_laporan(d, sesi_id, judul="Perbandingan versi 2")

    # Act
    versi_2 = _terbitkan(d, baru, supersedes_id=versi_1["id"])

    # Assert
    assert versi_2.status_code == 201, versi_2.text
    assert (versi_2.json()["version_no"], versi_2.json()["supersedes_id"]) == (2, versi_1["id"])
    daftar = _daftar_terbit(d)
    assert (daftar["total"], [b["title"] for b in daftar["items"]]) == (1, ["Perbandingan versi 2"])
    assert {o.nama: _status(d, lama, o) for o in SEMUA} == {o.nama: "superseded" for o in SEMUA}


def test_versi_yang_sudah_diganti_atau_sudah_terbit_tidak_bisa_diterbitkan_lagi(sesi):
    """Negatif: AC9, versi lama yang sudah diganti tidak bisa diganti lagi, dan laporan yang
    sudah terbit tidak bisa diterbitkan dua kali; keduanya 409 tanpa mengubah daftar."""
    # Arrange
    d, sesi_id = sesi
    lama = buat_laporan(d, sesi_id, judul="Versi 1")
    versi_1 = _terbitkan(d, lama).json()
    _terbitkan(d, buat_laporan(d, sesi_id, judul="Versi 2"), supersedes_id=versi_1["id"])
    ketiga = buat_laporan(d, sesi_id, judul="Versi 3")

    # Act
    ganti_yang_lama = _terbitkan(d, ketiga, supersedes_id=versi_1["id"])
    terbit_ulang = _terbitkan(d, lama)

    # Assert
    kode = {r.status_code: r.json()["code"] for r in (ganti_yang_lama, terbit_ulang)}
    assert kode == {409: "REPORT_NOT_PUBLISHABLE"}
    assert [b["title"] for b in _daftar_terbit(d)["items"]] == ["Versi 2"]
    assert _status(d, ketiga) == "saved"


def test_hanya_admin_yang_bisa_menerbitkan(sesi):
    """Negatif: AC9, penerbitan manual oleh Admin saja. Kontributor, Viewer, dan Developer AI
    ditolak 403 lewat API mentah, laporannya tetap tersimpan dan daftar terbit tetap kosong."""
    # Arrange
    d, sesi_id = sesi
    laporan = buat_laporan(d, sesi_id)

    # Act
    ditolak = {o.nama: _terbitkan(d, laporan, o).status_code for o in (KONTRIBUTOR, VIEWER, DEV_AI)}

    # Assert
    assert ditolak == {o.nama: 403 for o in (KONTRIBUTOR, VIEWER, DEV_AI)}
    assert (_status(d, laporan), _daftar_terbit(d)["total"]) == ("saved", 0)


@pytest.mark.parametrize("lingkup", ["dev", "dev_test"])
def test_laporan_dengan_skor_dev_ditolak_409_dengan_pesan_jelas(sesi, lingkup):
    """Negatif: AC12, laporan yang memuat skor dev tidak bisa diterbitkan, juga oleh Admin;
    ditolak 409 REPORT_HAS_DEV_SCORES dengan pesan yang menyebut nilai Test."""
    # Arrange
    d, sesi_id = sesi
    laporan = buat_laporan(d, sesi_id, scope=lingkup)

    # Act
    respons = _terbitkan(d, laporan)

    # Assert
    assert (respons.status_code, respons.json()["code"]) == (409, "REPORT_HAS_DEV_SCORES")
    assert "Test" in respons.json()["message"]
    assert (_status(d, laporan), _daftar_terbit(d)["total"]) == ("saved", 0)


@pytest.mark.parametrize(
    ("label", "harapan"),
    [
        # Positif: mode nama asli menampilkan nama produk.
        ("real_name", ["AiYU", "LegalHero"]),
        # Negatif: mode kode tidak membocorkan nama asli ke peran mana pun.
        ("code", ["Product A", "Product B"]),
    ],
)
def test_nama_produk_di_daftar_terbit_mengikuti_aturan_pbi15(sesi, label, harapan):
    """Positif dan Negatif: AC10, nama produk pesaing di daftar terbit adalah label beku laporan
    menurut PBI-15; laporan mode kode tidak membuka nama asli maupun id produk."""
    # Arrange
    d, sesi_id = sesi
    _terbitkan(d, buat_laporan(d, sesi_id, label=label))
    rahasia = ["AiYU", "LegalHero", *(str(p.id) for p in d.produk.values())]

    # Act
    daftar = {o.nama: d.sebagai(o).get("/published-reports") for o in SEMUA}

    # Assert
    for respons in daftar.values():
        (baris,) = respons.json()["items"]
        assert sorted(baris["product_labels"]) == harapan
        if label == "code":
            assert [r for r in rahasia if r in respons.text] == []


def test_tanpa_tautan_publik_jadwal_maupun_notifikasi(sesi):
    """Negatif: AC9, tidak ada tautan publik (daftar dan laporan terbit menolak tanpa login,
    respons terbit tanpa URL), tidak ada jadwal terbit (badan selain supersedes_id ditolak 422),
    dan tidak ada notifikasi yang dikirim saat terbit."""
    # Arrange
    d, sesi_id = sesi
    laporan = buat_laporan(d, sesi_id)
    notifikasi = d.db.scalar(select(func.count()).select_from(Notification))

    # Act
    dijadwalkan = _terbitkan(d, laporan, publish_at="2026-12-01T00:00:00Z")
    terbit = _terbitkan(d, laporan)
    app.dependency_overrides.pop(get_current_user)
    tanpa_login = {
        jalur: d.client.get(jalur).status_code
        for jalur in ("/published-reports", f"/reports/{laporan['id']}")
    }

    # Assert
    assert dijadwalkan.status_code == 422
    assert set(terbit.json()) == {"id", "report_id", "version_no", "supersedes_id"}
    assert tanpa_login == {"/published-reports": 401, f"/reports/{laporan['id']}": 401}
    assert d.db.scalar(select(func.count()).select_from(Notification)) == notifikasi
