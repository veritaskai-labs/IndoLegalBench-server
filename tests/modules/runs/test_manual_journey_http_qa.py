"""QA perjalanan sesi manual dari dibuat sampai Selesai. PBI-22 (SCRUM-176), SCRUM-238.

Satu sesi dilalui seperti di catatan klien: Admin membuat sesi, beberapa orang tim legal
mengisi bergantian, ada jawaban yang ditimpa, ada platform yang menolak menjawab, lalu
Admin menandai Selesai. Tes unit SCRUM-230/231 menguji tiap aturan sendiri-sendiri; di sini
urutan endpoint-nya yang diuji, lewat HTTP, dengan pengguna yang benar-benar berbeda.

Desain tes: state transition testing untuk status D8 (Belum terisi -> Terisi sebagian ->
Lengkap -> Selesai) beserta transisi yang terlarang.

Label: Positif = alur berhasil, Negatif = alur ditolak, Edge = batas perilaku.
"""

from app.shared.security import Role
from tests.modules.runs.manual_qa import (
    URL,
    baca,
    buat_sesi,
    id_orang,
    isi,
    isi_semua,
    orang,
    riwayat,
    slot,
)


def test_perjalanan_status_dari_belum_terisi_sampai_selesai(siap_manual):
    """Positif: AC8, status mengikuti jumlah jawaban terisi, lalu Selesai oleh Admin."""
    # Arrange: 3 kasus x 2 percobaan = 6 jawaban
    sesi = buat_sesi(siap_manual, repeat_count=2)
    sid = sesi["id"]
    urutan = [sesi["status"]]

    # Act: isi satu, isi sisanya, lalu tandai Selesai
    assert (
        isi(siap_manual, sid, slot(sesi, 0), "Jawaban pertama", oleh="Kontributor Satu").status_code
        == 200
    )
    satu_terisi = baca(siap_manual, sid)
    urutan.append(satu_terisi["status"])
    isi_semua(siap_manual, sid)
    urutan.append(baca(siap_manual, sid)["status"])
    selesai = orang(siap_manual, Role.ADMIN, "Admin Satu").post(f"{URL}/{sid}/finalize")

    # Assert
    assert urutan == ["not_filled", "partially_filled", "complete"]
    assert (satu_terisi["filled_answers"], satu_terisi["total_answers"]) == (1, 6)
    assert selesai.status_code == 200, selesai.text
    assert selesai.json()["status"] == "finalized"
    assert selesai.json()["finalized_by"] == id_orang("Admin Satu")


def test_dua_orang_mengisi_sesi_yang_sama_dan_tercatat_masing_masing(siap_manual):
    """Positif: AC5 dan catatan klien (tim legal 4 orang), tiap jawaban mencatat pengisinya."""
    # Arrange
    sesi = buat_sesi(siap_manual)
    sid = sesi["id"]

    # Act
    isi(siap_manual, sid, slot(sesi, 0), "Jawaban dari Rina", oleh="Rina")
    isi(siap_manual, sid, slot(sesi, 1), "Jawaban dari Budi", oleh="Budi")
    detail = baca(siap_manual, sid)

    # Assert
    pengisi = {
        kasus["case_code"]: kasus["attempts"][0]["filled_by"]
        for kasus in detail["cases"]
        if kasus["attempts"][0]["filled"]
    }
    assert sorted(pengisi.values()) == sorted([id_orang("Rina"), id_orang("Budi")])


def test_menimpa_jawaban_orang_lain_butuh_konfirmasi_dan_versinya_masuk_riwayat(siap_manual):
    """Positif + Negatif: AC5, tanpa konfirmasi ditolak 409, dengan konfirmasi versi lama
    tersimpan di riwayat lengkap dengan siapa pengisi dan siapa penimpanya."""
    # Arrange
    sesi = buat_sesi(siap_manual)
    sid, tempat = sesi["id"], slot(sesi, 0)
    isi(siap_manual, sid, tempat, "Versi Rina", oleh="Rina")

    # Act
    tanpa_konfirmasi = isi(siap_manual, sid, tempat, "Versi Budi", oleh="Budi")
    dengan_konfirmasi = isi(siap_manual, sid, tempat, "Versi Budi", oleh="Budi", confirm=True)
    jejak = riwayat(siap_manual, sid, tempat)

    # Assert
    assert tanpa_konfirmasi.status_code == 409
    assert tanpa_konfirmasi.json()["code"] == "CONFIRMATION_REQUIRED"
    assert dengan_konfirmasi.status_code == 200
    assert dengan_konfirmasi.json()["answer_md"] == "Versi Budi"
    assert jejak.status_code == 200
    [lama] = jejak.json()
    assert (lama["answer_md"], lama["filled_by"], lama["overwritten_by"]) == (
        "Versi Rina",
        id_orang("Rina"),
        id_orang("Budi"),
    )


def test_jawaban_yang_ditolak_tanpa_konfirmasi_tidak_mengubah_apa_pun(siap_manual):
    """Negatif: AC5, penolakan 409 tidak menyentuh jawaban, riwayat, maupun progres."""
    # Arrange
    sesi = buat_sesi(siap_manual)
    sid, tempat = sesi["id"], slot(sesi, 0)
    isi(siap_manual, sid, tempat, "Versi Rina", oleh="Rina")
    sebelum = baca(siap_manual, sid)

    # Act
    isi(siap_manual, sid, tempat, "Versi Budi", oleh="Budi")
    sesudah = baca(siap_manual, sid)

    # Assert
    assert sesudah == sebelum
    assert riwayat(siap_manual, sid, tempat).json() == []


def test_platform_menolak_menjawab_terhitung_terisi_dan_wajib_bercatatan(siap_manual):
    """Positif + Negatif: AC6, "Platform menolak menjawab" mengisi slot dan menaikkan progres,
    tapi wajib catatan bebas dari tim legal."""
    # Arrange
    sesi = buat_sesi(siap_manual)
    sid, tempat = sesi["id"], slot(sesi, 0)

    # Act
    tanpa_catatan = isi(siap_manual, sid, tempat, None, oleh="Rina", platform_refused=True)
    dengan_catatan = isi(
        siap_manual,
        sid,
        tempat,
        None,
        oleh="Rina",
        platform_refused=True,
        note="Diblokir filter konten platform",
    )
    detail = baca(siap_manual, sid)

    # Assert
    assert tanpa_catatan.status_code == 422
    assert dengan_catatan.status_code == 200, dengan_catatan.text
    assert detail["filled_answers"] == 1
    terisi = slot_detail(detail, tempat)
    assert (terisi["filled"], terisi["platform_refused"], terisi["note"]) == (
        True,
        True,
        "Diblokir filter konten platform",
    )


def test_filter_belum_terisi_menyusut_saat_kasus_dituntaskan(siap_manual):
    """Positif: AC8, filter 'belum terisi' hanya menyisakan kasus yang masih kosong, dan
    angka X dari N tetap untuk seluruh sesi."""
    # Arrange: 3 kasus x 1 percobaan
    sesi = buat_sesi(siap_manual, repeat_count=1)
    sid = sesi["id"]
    assert isi(siap_manual, sid, slot(sesi, 0), "Jawaban", oleh="Rina").status_code == 200

    # Act
    tersaring = baca(siap_manual, sid, unfilled="true")

    # Assert
    assert len(tersaring["cases"]) == 2
    assert all(not kasus["filled"] for kasus in tersaring["cases"])
    assert (tersaring["filled_answers"], tersaring["total_answers"]) == (1, 3)


def test_selesai_ditolak_selama_masih_ada_jawaban_kosong(siap_manual):
    """Negatif: AC8, Selesai baru bisa bila semua kasus dan percobaan terisi."""
    # Arrange
    sesi = buat_sesi(siap_manual)
    sid = sesi["id"]
    isi(siap_manual, sid, slot(sesi, 0), "Jawaban", oleh="Rina")

    # Act
    respons = orang(siap_manual, Role.ADMIN, "Admin Satu").post(f"{URL}/{sid}/finalize")

    # Assert
    assert respons.status_code == 409
    assert respons.json()["code"] == "MANUAL_SESSION_INCOMPLETE"
    assert baca(siap_manual, sid)["status"] == "partially_filled"


def test_sesi_selesai_tidak_bisa_diisi_ditimpa_atau_ditutup_lagi(siap_manual):
    """Negatif: AC8, setelah Selesai tidak ada jawaban baru, tidak ada timpa walau
    dikonfirmasi, dan Selesai kedua ditolak. Halamannya tetap bisa dibaca."""
    # Arrange
    sesi = buat_sesi(siap_manual)
    sid, tempat = sesi["id"], slot(sesi, 0)
    isi_semua(siap_manual, sid)
    admin = lambda: orang(siap_manual, Role.ADMIN, "Admin Satu")  # noqa: E731
    assert admin().post(f"{URL}/{sid}/finalize").status_code == 200

    # Act
    timpa = isi(siap_manual, sid, tempat, "Koreksi setelah Selesai", oleh="Rina", confirm=True)
    ulang = admin().post(f"{URL}/{sid}/finalize")
    detail = baca(siap_manual, sid)

    # Assert
    assert (timpa.status_code, timpa.json()["code"]) == (409, "MANUAL_SESSION_FINALIZED")
    assert (ulang.status_code, ulang.json()["code"]) == (409, "MANUAL_SESSION_FINALIZED")
    assert detail["status"] == "finalized"
    assert slot_detail(detail, tempat)["answer_md"] != "Koreksi setelah Selesai"


def test_jawaban_diubah_saat_lengkap_tetap_lengkap(siap_manual):
    """Edge: AC5 + AC8, menimpa jawaban di sesi Lengkap tidak menurunkan statusnya."""
    # Arrange
    sesi = buat_sesi(siap_manual)
    sid, tempat = sesi["id"], slot(sesi, 0)
    isi_semua(siap_manual, sid)

    # Act
    respons = isi(siap_manual, sid, tempat, "Koreksi", oleh="Budi", confirm=True)

    # Assert
    assert respons.status_code == 200
    assert baca(siap_manual, sid)["status"] == "complete"


def slot_detail(detail: dict, tempat: dict) -> dict:
    """Isi slot `tempat` dari halaman sesi."""
    kasus = next(c for c in detail["cases"] if c["snapshot_item_id"] == tempat["snapshot_item_id"])
    return next(a for a in kasus["attempts"] if a["repetition_no"] == tempat["attempt_no"])
