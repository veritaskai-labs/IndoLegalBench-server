"""QA isi sesi manual: snapshot beku, jawaban utuh, dan tanpa sifat sesi otomatis.

PBI-22 (SCRUM-176) AC2, AC4, AC10, SCRUM-238. Lewat HTTP, seperti halaman sesi (SCRUM-235)
dan kartu jawaban (SCRUM-237) membacanya.

- AC2: kasus yang diubah lalu disetujui lagi setelah sesi dibuat tidak mengubah snapshot.
- AC4: jawaban sepanjang sekitar 30 halaman dengan tabel, tautan, dan baris kosong tersimpan
  dan terbaca kembali persis sama, juga versi lamanya di riwayat.
- AC10: sesi manual tidak masuk antrean, tidak punya biaya atau anggaran, dan tidak
  mengirim notifikasi selama dibuat, diisi, dan ditandai Selesai.

Label: Positif = perilaku yang diminta, Negatif = yang ditolak, Edge = batas perilaku.
"""

import uuid

from sqlalchemy import func, select

from app.modules.notifications.models import Notification
from app.modules.runs.models import MeasurementSession
from app.shared.security import Role
from tests.modules.cases.test_cases import _badan, _setujui
from tests.modules.runs.manual_qa import URL, baca, buat_sesi, isi, isi_semua, orang, riwayat, slot

# Sekitar 30 halaman A4 (kira-kira 3.000 karakter per halaman), seperti jawaban terpanjang
# di catatan klien. Berisi tabel, tautan, daftar, baris kosong ganda, spasi di akhir baris,
# CRLF, dan disclaimer, karena semuanya harus disalin apa adanya (AC4).
_HALAMAN = (
    "## Analisis Pasal 151 UU 13/2003\r\n\r\n"
    "Saya bukan pengacara; ini bukan nasihat hukum.  \n"
    "| Unsur | Terpenuhi | Dasar |\n|---|---|---|\n| Surat PHK | Tidak | [UU 13/2003](https://peraturan.go.id/uu13-2003) |\n\n\n"
    "- Langkah 1: bipartit\n- Langkah 2: mediasi\n\n"
    "Pekerja berhak atas pesangon sesuai Pasal 156 ayat (2). " * 40 + "\n\n"
)
JAWABAN_PANJANG = _HALAMAN * 30


def test_kasus_yang_disetujui_ulang_setelah_sesi_dibuat_tidak_mengubah_snapshot(siap_manual):
    """Positif: AC2 dan catatan edge Jira 'kasus diedit setelah sesi dibuat -> snapshot tetap'.
    Versi baru kasus PHK-001 dengan pertanyaan lain disetujui, sesi tetap memakai yang lama."""
    # Arrange
    sesi = buat_sesi(siap_manual, repeat_count=1)
    kasus = next(c for c in sesi["cases"] if c["case_code"] == "PHK-001")
    admin = orang(siap_manual, Role.ADMIN, "Admin Satu")
    assert admin.post(f"/cases/{kasus['case_id']}/versions").status_code == 201
    pertanyaan_baru = {
        "title": "PHK sepihak (revisi)",
        "question": "Pertanyaan versi baru?",
        "category": "ketenagakerjaan",
    }
    ubah = admin.put(
        f"/cases/{kasus['case_id']}", json=_badan(case_code="PHK-001", identity=pertanyaan_baru)
    )
    assert ubah.status_code == 200, ubah.text
    _setujui(siap_manual.db, kasus["case_id"])

    # Act
    sesudah = baca(siap_manual, sesi["id"])
    sesi_baru = buat_sesi(siap_manual, repeat_count=1, label="Sesi sesudah revisi")

    # Assert
    lama = next(c for c in sesudah["cases"] if c["case_code"] == "PHK-001")
    baru = next(c for c in sesi_baru["cases"] if c["case_code"] == "PHK-001")
    assert (lama["title"], lama["question"]) == (kasus["title"], kasus["question"])
    assert baru["question"] == "Pertanyaan versi baru?"


def test_jawaban_sekitar_30_halaman_tersimpan_dan_terbaca_persis(siap_manual):
    """Edge: AC4, tanpa batas karakter dan format dipertahankan (tabel, tautan, CRLF,
    spasi akhir baris, baris kosong ganda, disclaimer)."""
    # Arrange
    sesi = buat_sesi(siap_manual, repeat_count=1)
    tempat = slot(sesi, 0)
    assert len(JAWABAN_PANJANG) > 80_000

    # Act
    simpan = isi(siap_manual, sesi["id"], tempat, JAWABAN_PANJANG, oleh="Rina")
    terbaca = baca(siap_manual, sesi["id"])

    # Assert
    assert simpan.status_code == 200, simpan.text[:300]
    kasus = next(c for c in terbaca["cases"] if c["snapshot_item_id"] == tempat["snapshot_item_id"])
    assert kasus["attempts"][0]["answer_md"] == JAWABAN_PANJANG


def test_versi_panjang_yang_ditimpa_tersimpan_utuh_di_riwayat(siap_manual):
    """Edge: AC4 + AC5, versi lama di riwayat sama persis dengan yang dulu disimpan."""
    # Arrange
    sesi = buat_sesi(siap_manual, repeat_count=1)
    tempat = slot(sesi, 0)
    isi(siap_manual, sesi["id"], tempat, JAWABAN_PANJANG, oleh="Rina")

    # Act
    isi(siap_manual, sesi["id"], tempat, "Ringkasan pendek", oleh="Budi", confirm=True)
    jejak = riwayat(siap_manual, sesi["id"], tempat).json()

    # Assert
    assert [v["answer_md"] for v in jejak] == [JAWABAN_PANJANG]


def test_markup_di_jawaban_disimpan_sebagai_teks_apa_adanya(siap_manual):
    """Edge: AC4, server tidak membersihkan atau mengubah HTML di jawaban. Penyaringan saat
    tampil adalah tugas client (MarkdownAnswer, SCRUM-260), jadi teks mentah harus utuh."""
    # Arrange
    sesi = buat_sesi(siap_manual, repeat_count=1)
    tempat = slot(sesi, 0)
    markup = '<img src="x" onerror="alert(1)"> dan <script>alert(2)</script>'

    # Act
    isi(siap_manual, sesi["id"], tempat, markup, oleh="Rina")

    # Assert
    kasus = next(
        c
        for c in baca(siap_manual, sesi["id"])["cases"]
        if c["snapshot_item_id"] == tempat["snapshot_item_id"]
    )
    assert kasus["attempts"][0]["answer_md"] == markup


def test_sesi_manual_tidak_punya_antrean_biaya_anggaran_maupun_tingkat_kegagalan(siap_manual):
    """Positif: AC10, halaman sesi manual tidak memuat konsep sesi otomatis."""
    # Arrange
    sesi = buat_sesi(siap_manual, repeat_count=1)

    # Act
    kunci = set(sesi)
    baris = siap_manual.db.get(MeasurementSession, uuid.UUID(sesi["id"]))

    # Assert
    terlarang = {
        "queued_at",
        "queue_position",
        "estimated_cost",
        "cost",
        "budget",
        "failure_rate",
        "failed_count",
    }
    assert kunci.isdisjoint(terlarang)
    assert baris.queued_at is None
    assert baris.status.value == "not_filled"


def test_sesi_manual_tidak_mengirim_notifikasi_dari_dibuat_sampai_selesai(siap_manual):
    """Positif: AC10, tidak ada notifikasi atau pengingat selama sesi manual berjalan."""

    # Arrange
    def jumlah_notifikasi() -> int:
        return siap_manual.db.scalar(select(func.count()).select_from(Notification))

    sebelum = jumlah_notifikasi()

    # Act
    sesi = buat_sesi(siap_manual, repeat_count=1)
    isi_semua(siap_manual, sesi["id"])
    selesai = orang(siap_manual, Role.ADMIN, "Admin Satu").post(f"{URL}/{sesi['id']}/finalize")

    # Assert
    assert selesai.status_code == 200
    assert jumlah_notifikasi() == sebelum
