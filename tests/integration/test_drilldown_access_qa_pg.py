"""QA hak akses penelusuran angka laporan lewat API mentah. PBI-16 (SCRUM-83) AC3, AC4; SCRUM-330.

Matriks D9 Sprint 4 diuji per peran x split kasus x endpoint, tanpa bantuan UI: tombol yang
disembunyikan klien tidak dihitung. Desain: base choice (Admin, kasus test, detail kasus),
lalu satu karakteristik diganti per tes supaya kegagalan menunjuk karakteristik itu.

Label: Positif = boleh, Negatif = ditolak.
"""

from app.main import app
from app.modules.cases.service import MASKED_CASE_CODE
from app.shared.security import get_current_user
from tests.drilldown_qa_pg import (
    ADMIN,
    BUKTI_DI,
    DEV_AI,
    HANYA_POSTGRES,
    KASUS,
    KONTRIBUTOR,
    RAHASIA,
    SEMUA,
    VIEWER,
    angka,
    buat_laporan,
    detail_kasus,
    id_kasus,
    jalur_kasus,
    terbitkan,
)
from tests.modules.runs.test_manual_evidence_http_qa import PNG

pytestmark = HANYA_POSTGRES

PERTANYAAN = tuple(k.pertanyaan for k in KASUS)


def _laporan_terbit(dunia) -> dict:
    """Laporan nilai test yang diterbitkan Admin, jadi semua peran boleh membacanya (D9)."""
    laporan = buat_laporan(dunia, scope="test")
    terbitkan(dunia, laporan)
    return laporan


def _tanpa_isi_kasus(teks: str) -> bool:
    return RAHASIA not in teks and not any(p in teks for p in PERTANYAAN)


def test_daftar_kasus_terbuka_untuk_semua_peran_tanpa_isi_kasus(dunia):
    """Positif: D9 'Penelusuran: daftar kasus dan skor', semua peran melihat kode, versi, dan
    skor; isi kasus dan jawaban asli tidak pernah ikut, dan kode test disamarkan untuk Dev AI."""
    # Arrange
    laporan = _laporan_terbit(dunia)
    jalur = jalur_kasus(laporan, angka(laporan, "AiYU", "test"))

    # Act
    respons = {o: dunia.sebagai(o).get(jalur) for o in SEMUA}

    # Assert
    assert {o.nama: r.status_code for o, r in respons.items()} == {o.nama: 200 for o in SEMUA}
    terbuka = {
        o.nama: {k["content_visible"] for k in r.json()["cases"]} for o, r in respons.items()
    }
    assert terbuka == {
        ADMIN.nama: {True},
        KONTRIBUTOR.nama: {True},
        VIEWER.nama: {False},
        DEV_AI.nama: {False},
    }
    assert all(_tanpa_isi_kasus(r.text) for r in respons.values())
    assert {k["case_code"] for k in respons[DEV_AI].json()["cases"]} == {MASKED_CASE_CODE}
    assert all(k["det_score"] is not None for k in respons[VIEWER].json()["cases"])


def test_isi_kasus_test_hanya_untuk_admin_dan_kontributor(dunia):
    """Positif dan Negatif: AC3, isi kasus test dan jawaban asli hanya Admin dan Kontributor.
    Viewer dan Dev AI mendapat 403 dengan email kontak, tanpa satu pun isi kasus."""
    # Arrange
    laporan = _laporan_terbit(dunia)
    hasil = angka(laporan, "AiYU", "test")

    # Act
    respons = {o: detail_kasus(dunia, laporan, hasil, "PHK-001", o) for o in SEMUA}

    # Assert
    assert {o.nama: r.status_code for o, r in respons.items()} == {
        ADMIN.nama: 200,
        KONTRIBUTOR.nama: 200,
        VIEWER.nama: 403,
        DEV_AI.nama: 403,
    }
    assert RAHASIA in respons[KONTRIBUTOR].json()["attempts"][0]["answer_text"]
    for ditolak in (respons[VIEWER], respons[DEV_AI]):
        assert ditolak.json()["code"] == "CASE_CONTENT_FORBIDDEN"
        assert "indolegalbench@veritask.ai" in ditolak.json()["message"]
        assert _tanpa_isi_kasus(ditolak.text)


def test_isi_kasus_dev_hanya_untuk_admin_dan_developer_ai(dunia):
    """Positif dan Negatif: D9 'isi kasus dev' hanya Admin dan Dev AI. Kontributor dan Viewer
    tidak bisa membuat laporan dev maupun membuka laporan dev milik Admin."""
    # Arrange
    milik_admin = buat_laporan(dunia)
    milik_dev_ai = buat_laporan(dunia, oleh=DEV_AI)
    dev_admin = angka(milik_admin, "AiYU", "dev")

    # Act
    admin = detail_kasus(dunia, milik_admin, dev_admin, "PHK-003", ADMIN)
    dev_ai_dev = detail_kasus(
        dunia, milik_dev_ai, angka(milik_dev_ai, "AiYU", "dev"), "PHK-003", DEV_AI
    )
    dev_ai_test = detail_kasus(
        dunia, milik_dev_ai, angka(milik_dev_ai, "AiYU", "test"), "PHK-001", DEV_AI
    )
    lain = {
        o: detail_kasus(dunia, milik_admin, dev_admin, "PHK-003", o) for o in (KONTRIBUTOR, VIEWER)
    }
    buat_dev = {
        o: dunia.sebagai(o).post(
            "/reports",
            json={
                "title": "Coba dev",
                "session_ids": [str(dunia.otomatis.id), str(dunia.manual.id)],
                "score_scope": "dev_test",
            },
        )
        for o in (KONTRIBUTOR, VIEWER)
    }

    # Assert
    assert (admin.status_code, dev_ai_dev.status_code) == (200, 200)
    assert (dev_ai_test.status_code, dev_ai_test.json()["code"]) == (403, "CASE_CONTENT_FORBIDDEN")
    assert {o.nama: (r.status_code, r.json()["code"]) for o, r in lain.items()} == {
        KONTRIBUTOR.nama: (404, "REPORT_NOT_FOUND"),
        VIEWER.nama: (404, "REPORT_NOT_FOUND"),
    }
    assert {o.nama: (r.status_code, r.json()["code"]) for o, r in buat_dev.items()} == {
        KONTRIBUTOR.nama: (403, "REPORT_SCOPE_FORBIDDEN"),
        VIEWER.nama: (403, "REPORT_SCOPE_FORBIDDEN"),
    }


def test_bukti_jawaban_manual_hanya_untuk_admin_dan_kontributor(dunia):
    """Positif dan Negatif: AC4, bukti dibuka dan diunduh dari penelusuran hanya oleh Admin dan
    Kontributor, selalu sebagai lampiran. Viewer dan Dev AI mendapat 403."""
    # Arrange
    laporan = _laporan_terbit(dunia)
    kasus = jalur_kasus(
        laporan, angka(laporan, dunia.manual.produk.name, "test"), id_kasus(dunia, BUKTI_DI)
    )
    (bukti,) = dunia.sebagai(ADMIN).get(f"{kasus}/attempts/1/evidence").json()

    # Act
    daftar = {o: dunia.sebagai(o).get(f"{kasus}/attempts/1/evidence") for o in SEMUA}
    unduh = {o: dunia.sebagai(o).get(f"{kasus}/evidence/{bukti['id']}/file") for o in SEMUA}

    # Assert
    harapan = {ADMIN.nama: 200, KONTRIBUTOR.nama: 200, VIEWER.nama: 403, DEV_AI.nama: 403}
    assert {o.nama: r.status_code for o, r in daftar.items()} == harapan
    assert {o.nama: r.status_code for o, r in unduh.items()} == harapan
    berkas = unduh[KONTRIBUTOR]
    assert berkas.content == PNG
    assert berkas.headers["content-disposition"].startswith("attachment;")
    assert berkas.headers["x-content-type-options"] == "nosniff"
    assert PNG.decode("latin-1") not in unduh[VIEWER].text


def test_bukti_kasus_lain_tidak_bisa_dibuka_lewat_kasus_ini(dunia):
    """Negatif: AC4, id bukti PHK-001 lewat jalur PHK-002 di laporan yang sama dijawab 404."""
    # Arrange
    laporan = _laporan_terbit(dunia)
    hasil = angka(laporan, dunia.manual.produk.name, "test")
    pemilik = jalur_kasus(laporan, hasil, id_kasus(dunia, BUKTI_DI))
    (bukti,) = dunia.sebagai(ADMIN).get(f"{pemilik}/attempts/1/evidence").json()
    lain = jalur_kasus(laporan, hasil, id_kasus(dunia, "PHK-002"))

    # Act
    respons = dunia.sebagai(ADMIN).get(f"{lain}/evidence/{bukti['id']}/file")

    # Assert
    assert (respons.status_code, respons.json()["code"]) == (404, "REPORT_EVIDENCE_NOT_FOUND")


def test_laporan_mode_kode_tidak_membuka_sesi_produk_maupun_nama_aslinya(dunia):
    """Negatif: PBI-15 AC9 x AC1, penelusuran laporan berlabel kode tidak membocorkan id sesi,
    id produk, atau nama asli produk di balik kodenya."""
    # Arrange
    laporan = buat_laporan(dunia, label="code")
    rahasia = [
        str(dunia.otomatis.id),
        str(dunia.manual.id),
        str(dunia.otomatis.produk.id),
        str(dunia.manual.produk.id),
        "AiYU",
        "LegalHero",
    ]

    # Act
    teks = []
    for sesi in laporan["sessions"]:
        hasil = angka(laporan, sesi["product_label"], "combined")
        teks.append(dunia.sebagai(ADMIN).get(jalur_kasus(laporan, hasil)).text)
        teks.append(detail_kasus(dunia, laporan, hasil, "PHK-001").text)

    # Assert
    assert {s["product_label"] for s in laporan["sessions"]} == {"Product A", "Product B"}
    assert [r for r in rahasia if any(r in t for t in teks)] == []


def test_tanpa_login_semua_jalur_penelusuran_ditolak_401(dunia):
    """Negatif: semua endpoint penelusuran dan unduhan menolak permintaan tanpa sesi login."""
    # Arrange
    laporan = _laporan_terbit(dunia)
    kasus = jalur_kasus(laporan, angka(laporan, "AiYU", "test"), id_kasus(dunia, "PHK-001"))
    jalur = [
        jalur_kasus(laporan, angka(laporan, "AiYU", "test")),
        kasus,
        f"{kasus}/attempts/1/evidence",
        f"/reports/sessions/{dunia.otomatis.id}/csv",
    ]
    app.dependency_overrides.pop(get_current_user)

    # Act
    status = {j: dunia.client.get(j).status_code for j in jalur}

    # Assert
    assert status == dict.fromkeys(jalur, 401)
