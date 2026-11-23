"""QA unduh skor per sesi (CSV). PBI-16 (SCRUM-83) AC5, AC6, AC7; SCRUM-330.

GET /reports/sessions/{id}/csv di atas sesi sungguhan (tests/drilldown_qa_pg.py). Desain: decision table
status sesi x hasil unduhan untuk AC6, state transition Dibatalkan -> Dilanjutkan -> Selesai,
dan perbandingan isi berkas dengan penelusuran laporan yang sama.

Label: Positif = boleh diunduh, Negatif = ditolak, Edge = batas perilaku.
"""

import re

import pytest
from sqlalchemy import func, select

from app.modules.reports.models import SessionScoreFile
from tests.drilldown_qa_pg import (
    ADMIN,
    DEV_AI,
    HANYA_POSTGRES,
    KASUS,
    KASUS_GAGAL_SEKALI,
    KODE_DEV,
    KODE_TEST,
    KONTRIBUTOR,
    RAHASIA,
    SEMUA,
    VIEWER,
    PenilaiPelit,
    ProviderQA,
    angka,
    baris_csv,
    buat_laporan,
    buat_sesi_manual,
    daftar_kasus,
    isi_jawaban_manual,
    jalankan_sesi_otomatis,
    jalankan_worker,
    mulai_sesi_otomatis,
    nilai,
    sesi_manual_selesai,
    terima_apa_adanya,
    unduh_skor,
)

pytestmark = HANYA_POSTGRES

KOLOM = [
    "Kasus",
    "Versi Kasus",
    "Produk",
    "Jenis Pengukuran",
    "Status Penilaian",
    "Skor Deterministik",
    "Skor LLM",
    "Versi Rubrik",
    "Versi Model Produk",
    "Model Penilai",
    "Uji Coba",
]


def _kode_tolak(respons) -> str | int:
    return respons.json()["code"] if respons.status_code == 409 else respons.status_code


def _jumlah_berkas(dunia) -> int:
    return dunia.db.scalar(select(func.count()).select_from(SessionScoreFile))


def test_berkas_skor_berisi_kasus_produk_skor_jenis_dan_catatan_versi(dunia):
    """Positif: AC5, satu sesi satu berkas CSV: kolom kasus, produk, skor dua desimal, jenis
    pengukuran, dan catatan versi kasus, rubrik, serta model; skornya sama dengan penelusuran."""
    # Arrange
    laporan = buat_laporan(dunia)
    ditelusuri = daftar_kasus(dunia, laporan, angka(laporan, "AiYU", "combined"))["cases"]

    # Act
    respons = unduh_skor(dunia, dunia.otomatis.id)

    # Assert
    assert respons.status_code == 200, respons.text
    assert respons.headers["content-type"].startswith("text/csv")
    nama = f'filename="skor-{dunia.otomatis.id}.csv"'
    assert respons.headers["content-disposition"] == f"attachment; {nama}"
    baris = baris_csv(respons.content)
    assert list(baris[0]) == KOLOM
    assert {
        (b["Produk"], b["Jenis Pengukuran"], b["Versi Kasus"], b["Versi Rubrik"]) for b in baris
    } == {("AiYU", "automatic", "1", "1")}
    assert {(b["Versi Model Produk"], b["Model Penilai"], b["Uji Coba"]) for b in baris} == {
        ("aiyu-2026-11", "judge-qa-1", "tidak")
    }
    assert all(re.fullmatch(r"\d\.\d\d", b["Skor LLM"]) for b in baris)
    assert {b["Kasus"]: (b["Skor Deterministik"], b["Skor LLM"]) for b in baris} == {
        k["case_code"]: (k["det_score"], k["llm_score"]) for k in ditelusuri
    }


def test_sesi_manual_uji_coba_ditandai_di_setiap_baris(dunia):
    """Positif: AC5 dan D2, jenis pengukuran manual dan tanda Uji Coba ada di setiap baris;
    versi model produk manual yang tidak ada ditulis 'tidak tercatat', bukan kosong."""
    # Arrange
    sesi_id = sesi_manual_selesai(dunia, purpose="trial")

    # Act
    baris = baris_csv(unduh_skor(dunia, sesi_id).content)

    # Assert
    assert len(baris) == len(KASUS)
    assert {(b["Jenis Pengukuran"], b["Uji Coba"], b["Versi Model Produk"]) for b in baris} == {
        ("manual", "ya", "tidak tercatat")
    }


def test_berkas_tidak_memuat_jawaban_asli_maupun_alasan_penilaian(dunia):
    """Negatif: AC5, untuk peran apa pun berkas hanya berisi skor: tanpa jawaban asli, tanpa
    pertanyaan, dan tanpa alasan penilaian."""
    # Act
    isi = {o.nama: unduh_skor(dunia, dunia.otomatis.id, o).content.decode() for o in SEMUA}

    # Assert
    terlarang = [RAHASIA, "criterion_unmet", *(k.pertanyaan for k in KASUS)]
    bocor = {nama: [t for t in terlarang if t in teks] for nama, teks in isi.items()}
    assert bocor == {o.nama: [] for o in SEMUA}


def test_baris_berkas_mengikuti_peran_pengunduh(dunia):
    """Positif: D6 dan D9, Viewer dan Kontributor hanya mendapat baris kasus test, Admin semua."""
    # Act
    kode = {
        o.nama: sorted(
            b["Kasus"] for b in baris_csv(unduh_skor(dunia, dunia.otomatis.id, o).content)
        )
        for o in (ADMIN, KONTRIBUTOR, VIEWER)
    }

    # Assert
    assert kode == {
        ADMIN.nama: sorted(KODE_TEST + KODE_DEV),
        KONTRIBUTOR.nama: list(KODE_TEST),
        VIEWER.nama: list(KODE_TEST),
    }


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Temuan SCRUM-330: D6/D9 'Dev AI hanya kasus dev', berkas masih memuat kasus test "
    "yang disamarkan. Menunggu jawaban SA; hapus tanda ini bila diperbaiki atau sesuaikan tesnya.",
)
def test_dev_ai_hanya_mendapat_baris_kasus_dev(dunia):
    """Positif: D6 Keputusan SA dan D9 'Unduh CSV: Dev AI = Ya, kasus dev'."""
    # Act
    baris = baris_csv(unduh_skor(dunia, dunia.otomatis.id, DEV_AI).content)

    # Assert
    assert sorted(b["Kasus"] for b in baris) == list(KODE_DEV)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Temuan SCRUM-330: D6 'Admin semua (dengan kolom split)', berkas belum punya kolom "
    "split. Menunggu jawaban SA; hapus tanda ini bila diperbaiki atau sesuaikan tesnya.",
)
def test_berkas_admin_menandai_split_setiap_kasus(dunia):
    """Positif: D6 Keputusan SA, Admin mendapat semua kasus dengan kolom split."""
    # Act
    baris = baris_csv(unduh_skor(dunia, dunia.otomatis.id).content)

    # Assert
    assert any("split" in kolom.lower() for kolom in baris[0])


def test_hanya_sesi_yang_selesai_sepenuhnya_dan_dinilai_yang_bisa_diunduh(dunia):
    """Positif dan Negatif: AC6 dan D2 kotak A sebagai decision table. Setiap status sesi
    dipetakan ke 200 atau 409 dengan kode alasan pertamanya."""
    # Arrange
    admin = dunia.sebagai(ADMIN)
    dibatalkan = mulai_sesi_otomatis(dunia)
    assert admin.post(f"/measurements/{dibatalkan}/cancel").status_code == 202
    belum_diterima = jalankan_sesi_otomatis(dunia)
    nilai(dunia, belum_diterima)
    belum_dinilai = jalankan_sesi_otomatis(dunia, ProviderQA(gagal=()))
    diarsipkan = jalankan_sesi_otomatis(dunia, ProviderQA(gagal=()))
    nilai(dunia, diarsipkan)
    assert admin.patch(f"/measurements/{diarsipkan}/archive").status_code == 200
    berjalan = mulai_sesi_otomatis(dunia)
    selama_jalan = []
    provider = ProviderQA(gagal=())
    provider.on_call = lambda: (
        selama_jalan.append(unduh_skor(dunia, berjalan)) or setattr(provider, "on_call", None)
    )
    jalankan_worker(dunia, provider)
    manual_kosong = buat_sesi_manual(dunia)
    manual_sebagian = buat_sesi_manual(dunia)
    isi_jawaban_manual(dunia, manual_sebagian, ["PHK-001"])
    manual_lengkap = buat_sesi_manual(dunia)
    isi_jawaban_manual(dunia, manual_lengkap)
    manual_dinilai = buat_sesi_manual(dunia)
    isi_jawaban_manual(dunia, manual_dinilai)
    nilai(dunia, manual_dinilai)
    menunggu = mulai_sesi_otomatis(dunia)
    sesi = {
        "otomatis selesai dengan kegagalan, diterima apa adanya, dinilai": dunia.otomatis.id,
        "manual Selesai dan dinilai": dunia.manual.id,
        "menunggu": menunggu,
        "dibatalkan": dibatalkan,
        "selesai dengan kegagalan, belum diterima": belum_diterima,
        "selesai, belum dinilai": belum_dinilai,
        "diarsipkan": diarsipkan,
        "manual belum terisi": manual_kosong,
        "manual terisi sebagian": manual_sebagian,
        "manual lengkap": manual_lengkap,
        "manual dinilai, belum Selesai": manual_dinilai,
    }

    # Act
    hasil = {nama: _kode_tolak(unduh_skor(dunia, sid)) for nama, sid in sesi.items()}
    hasil["berjalan"] = _kode_tolak(selama_jalan[0])

    # Assert
    assert hasil == {
        "otomatis selesai dengan kegagalan, diterima apa adanya, dinilai": 200,
        "manual Selesai dan dinilai": 200,
        "menunggu": "SESSION_NOT_FINISHED",
        "berjalan": "SESSION_NOT_FINISHED",
        "dibatalkan": "SESSION_CANCELLED",
        "selesai dengan kegagalan, belum diterima": "SESSION_NOT_ACCEPTED_AS_IS",
        "selesai, belum dinilai": "SESSION_NOT_GRADED",
        "diarsipkan": "SESSION_ARCHIVED",
        "manual belum terisi": "SESSION_NOT_FINISHED",
        "manual terisi sebagian": "SESSION_NOT_FINISHED",
        "manual lengkap": "SESSION_NOT_FINISHED",
        "manual dinilai, belum Selesai": "SESSION_NOT_FINISHED",
    }


def test_penolakan_menjelaskan_semua_alasannya(dunia):
    """Negatif: AC6, sesi yang dibatalkan dan belum dinilai ditolak dengan kedua alasannya,
    masing-masing dengan kalimat yang memberi tahu langkah berikutnya."""
    # Arrange
    sesi_id = mulai_sesi_otomatis(dunia)
    dunia.sebagai(ADMIN).post(f"/measurements/{sesi_id}/cancel")

    # Act
    respons = unduh_skor(dunia, sesi_id, VIEWER)

    # Assert
    alasan = respons.json()["reasons"]
    assert [a["code"] for a in alasan] == ["SESSION_CANCELLED", "SESSION_NOT_GRADED"]
    assert "Lanjutkan sesi sampai selesai" in alasan[0]["message"]
    assert respons.json()["message"] == alasan[0]["message"]


def test_sesi_dibatalkan_lalu_dilanjutkan_sampai_selesai_bisa_diunduh(dunia):
    """Edge: AC6 sebagai state transition Berjalan -> Dibatalkan (ditolak) -> Dilanjutkan ->
    Selesai -> Dinilai (boleh)."""
    # Arrange
    admin = dunia.sebagai(ADMIN)
    sesi_id = mulai_sesi_otomatis(dunia)
    provider = ProviderQA(gagal=())
    provider.on_call = lambda: (
        admin.post(f"/measurements/{sesi_id}/cancel") and setattr(provider, "on_call", None)
    )
    jalankan_worker(dunia, provider)

    # Act
    saat_batal = unduh_skor(dunia, sesi_id)
    lanjut = dunia.sebagai(ADMIN).post(f"/measurements/{sesi_id}/resume")
    jalankan_worker(dunia, provider)
    nilai(dunia, sesi_id)
    sesudah = unduh_skor(dunia, sesi_id)

    # Assert
    assert _kode_tolak(saat_batal) == "SESSION_CANCELLED"
    assert lanjut.status_code == 202
    assert sesudah.status_code == 200, sesudah.text


def test_terima_apa_adanya_membuka_unduhan_sesi_selesai_dengan_kegagalan(dunia):
    """Edge: AC6, sesi otomatis selesai dengan kegagalan baru boleh diunduh setelah Admin
    menandainya 'terima apa adanya' dengan alasan tertulis."""
    # Arrange
    sesi_id = jalankan_sesi_otomatis(dunia)
    nilai(dunia, sesi_id)
    sebelum = unduh_skor(dunia, sesi_id)

    # Act
    terima_apa_adanya(dunia, sesi_id)
    sesudah = unduh_skor(dunia, sesi_id)

    # Assert
    assert _kode_tolak(sebelum) == "SESSION_NOT_ACCEPTED_AS_IS"
    assert sesudah.status_code == 200, sesudah.text


def test_kasus_yang_semua_percobaannya_gagal_teknis_tertulis_tidak_dinilai(dunia):
    """Edge: catatan QA, kasus tanpa satu pun percobaan berhasil tetap ada di berkas dengan
    status 'tidak dinilai' dan skor kosong, bukan nol."""
    # Arrange
    sesi_id = jalankan_sesi_otomatis(dunia, ProviderQA(gagal=(), selalu_gagal=[KASUS_GAGAL_SEKALI]))
    terima_apa_adanya(dunia, sesi_id)
    nilai(dunia, sesi_id)

    # Act
    baris = {b["Kasus"]: b for b in baris_csv(unduh_skor(dunia, sesi_id).content)}

    # Assert
    gagal = baris.pop(KASUS_GAGAL_SEKALI)
    assert (gagal["Status Penilaian"], gagal["Skor Deterministik"], gagal["Skor LLM"]) == (
        "tidak dinilai",
        "",
        "",
    )
    assert {b["Status Penilaian"] for b in baris.values()} == {"dinilai"}


def test_berkas_disimpan_sekali_dan_nilai_ulang_menambah_berkas_tanpa_mengubah_yang_lama(dunia):
    """Positif: AC7, unduhan ulang memakai berkas tersimpan; setelah nilai ulang berkasnya baru
    dan berkas lama tetap tersimpan dengan angka lamanya."""
    # Arrange
    pertama = unduh_skor(dunia, dunia.otomatis.id).content

    # Act
    kedua = unduh_skor(dunia, dunia.otomatis.id).content
    setelah_unduh_ulang = _jumlah_berkas(dunia)
    dunia.pakai_penilai(PenilaiPelit)
    nilai(dunia, dunia.otomatis.id)
    sesudah_nilai_ulang = unduh_skor(dunia, dunia.otomatis.id).content

    # Assert
    assert (kedua, setelah_unduh_ulang) == (pertama, 1)
    assert sesudah_nilai_ulang != pertama
    tersimpan = dunia.db.scalars(
        select(SessionScoreFile.content).where(SessionScoreFile.session_id == dunia.otomatis.id)
    ).all()
    assert sorted(tersimpan) == sorted([pertama, sesudah_nilai_ulang])
