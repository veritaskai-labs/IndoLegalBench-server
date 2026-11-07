"""Aturan murni sesi manual per D8. PBI-22 (SCRUM-176) AC5, AC8, AC13; SCRUM-230.

D8 (State Diagram Sesi Manual): status sesi manual dihitung ulang server setiap kali
jawaban disimpan. Belum terisi (not_filled) selama belum ada jawaban, Terisi sebagian
(partially_filled) selama masih ada yang kosong, Lengkap (complete) bila semua kasus x
semua percobaan terisi. Dinilai (graded) dan Selesai (finalized) tidak dihitung dari
jumlah jawaban, jadi tidak turun lagi saat jawaban disimpan.

Selesai hanya dari Lengkap atau Dinilai (keputusan SA 7 November 2026; urutan Selesai
dan Nilai masih TBA di tiket), dan setelah Selesai sesi tidak bisa dibuka kembali (AC8).

Rentang tanggal jawaban (AC13): tanggal paling awal dan paling akhir jawaban diperoleh
dari platform. Batas hari peringatan masih TBA dari klien, jadi peringatan mati selama
batasnya belum diisi.

Label: Positif = perilaku yang diharapkan, Negatif = ditolak, Edge = batas perilaku.
"""

from datetime import UTC, datetime

import pytest

from app.shared.exceptions import ConflictError

HARI_1 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
HARI_3 = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
HARI_8 = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


@pytest.fixture
def aturan():
    # Diimpor lewat fixture: modul yang belum ada membuat tes merah, bukan galat koleksi.
    from app.modules.runs import manual_session_rules

    return manual_session_rules


@pytest.mark.parametrize(
    ("terisi", "total", "status"),
    [
        # Positif: tiga status D8 yang dihitung dari jumlah jawaban.
        (0, 4, "not_filled"),
        (1, 4, "partially_filled"),
        (3, 4, "partially_filled"),
        (4, 4, "complete"),
        # Edge: jawaban pertama langsung melengkapi sesi 1 kasus x 1 percobaan.
        (1, 1, "complete"),
    ],
)
@pytest.mark.parametrize("sekarang", ["not_filled", "partially_filled", "complete"])
def test_status_pengisian_dihitung_dari_jumlah_jawaban(aturan, sekarang, terisi, total, status):
    """Positif: D8, status mengikuti jumlah jawaban terisi, apa pun status sebelumnya."""
    # Act
    hasil = aturan.filling_status(sekarang, aturan.ManualProgress(filled=terisi, total=total))

    # Assert
    assert hasil == status


@pytest.mark.parametrize("sekarang", ["graded", "finalized"])
def test_dinilai_dan_selesai_tidak_turun_saat_jawaban_disimpan(aturan, sekarang):
    """Edge: Dinilai dan Selesai bukan hasil hitungan jawaban, jadi tidak berubah di sini."""
    # Act
    hasil = aturan.filling_status(sekarang, aturan.ManualProgress(filled=3, total=4))

    # Assert
    assert hasil == sekarang


@pytest.mark.parametrize(("terisi", "total"), [(5, 4), (-1, 4), (0, 0)])
def test_jumlah_jawaban_yang_mustahil_ditolak(aturan, terisi, total):
    """Negatif: terisi melebihi total, negatif, atau sesi tanpa percobaan adalah galat program."""
    # Act / Assert
    with pytest.raises(ValueError):
        aturan.ManualProgress(filled=terisi, total=total)


@pytest.mark.parametrize("sekarang", ["running", "finished", "queued"])
def test_status_otomatis_bukan_status_pengisian(aturan, sekarang):
    """Negatif: status D4 tidak boleh dihitung ulang sebagai sesi manual."""
    # Act / Assert
    with pytest.raises(ValueError):
        aturan.filling_status(sekarang, aturan.ManualProgress(filled=1, total=4))


@pytest.mark.parametrize("sekarang", ["complete", "graded"])
def test_selesai_boleh_dari_lengkap_atau_dinilai(aturan, sekarang):
    """Positif: AC8, semua kasus dan percobaan terisi; Nilai lebih dulu tidak diwajibkan."""
    # Act / Assert
    aturan.ensure_finalizable(sekarang)


@pytest.mark.parametrize("sekarang", ["not_filled", "partially_filled"])
def test_selesai_ditolak_selama_masih_ada_jawaban_kosong(aturan, sekarang):
    """Negatif: AC8, sesi hanya bisa ditandai Selesai bila semua kasus dan percobaan terisi."""
    # Act
    with pytest.raises(ConflictError) as galat:
        aturan.ensure_finalizable(sekarang)

    # Assert
    assert galat.value.code == "MANUAL_SESSION_INCOMPLETE"


def test_selesai_tidak_bisa_diulang(aturan):
    """Negatif: AC8, sesi yang sudah Selesai tidak dapat dibuka atau ditandai lagi."""
    # Act
    with pytest.raises(ConflictError) as galat:
        aturan.ensure_finalizable("finalized")

    # Assert
    assert galat.value.code == "MANUAL_SESSION_FINALIZED"


@pytest.mark.parametrize("sekarang", ["not_filled", "partially_filled", "complete", "graded"])
def test_jawaban_masih_bisa_disimpan_sebelum_selesai(aturan, sekarang):
    """Positif: AC8, pengisian sebagian boleh dilanjutkan kapan saja sebelum Selesai."""
    # Act / Assert
    aturan.ensure_answers_open(sekarang)


def test_jawaban_tidak_bisa_disimpan_setelah_selesai(aturan):
    """Negatif: AC8, setelah Selesai sesi tidak dapat dibuka kembali (dipakai SCRUM-231)."""
    # Act
    with pytest.raises(ConflictError) as galat:
        aturan.ensure_answers_open("finalized")

    # Assert
    assert galat.value.code == "MANUAL_SESSION_FINALIZED"


@pytest.mark.parametrize("sekarang", ["running", "finished"])
def test_status_otomatis_tidak_menerima_jawaban_manual(aturan, sekarang):
    """Negatif: penjaga jawaban manual tidak dipakai untuk sesi otomatis (galat program)."""
    # Act / Assert
    with pytest.raises(ValueError):
        aturan.ensure_answers_open(sekarang)


def test_rentang_tanggal_dari_jawaban_paling_awal_dan_paling_akhir(aturan):
    """Positif: AC13, urutan pengisian tidak berpengaruh; percobaan kosong dilewati."""
    # Act
    hasil = aturan.answer_date_range([HARI_3, None, HARI_1, HARI_8])

    # Assert
    assert hasil == (HARI_1, HARI_8)


def test_rentang_tanggal_kosong_tanpa_jawaban(aturan):
    """Edge: sesi yang belum terisi belum punya rentang tanggal."""
    # Act
    hasil = aturan.answer_date_range([None, None])

    # Assert
    assert hasil == (None, None)


@pytest.mark.parametrize(
    ("akhir", "batas", "peringatan"),
    [
        # Negatif: batas TBA dari klien, jadi belum ada peringatan.
        (HARI_8, None, False),
        # Edge: tepat di batas belum melebihi.
        (HARI_3, 2, False),
        # Positif: rentang 7 hari melebihi batas 2 hari.
        (HARI_8, 2, True),
    ],
)
def test_peringatan_rentang_tanggal_mengikuti_batas_hari(aturan, akhir, batas, peringatan):
    """Positif/Negatif/Edge: AC13, peringatan bila rentang melebihi batas hari."""
    # Act
    hasil = aturan.date_range_exceeds(HARI_1, akhir, limit_days=batas)

    # Assert
    assert hasil is peringatan


def test_tanpa_rentang_tanggal_tidak_ada_peringatan(aturan):
    """Edge: sesi tanpa jawaban tidak memicu peringatan walau batasnya ada."""
    # Act / Assert
    assert aturan.date_range_exceeds(None, None, limit_days=2) is False


@pytest.mark.parametrize(
    ("status", "berakhir"),
    [
        ("finalized", True),
        ("graded", False),
        ("complete", False),
        ("not_filled", False),
    ],
)
def test_hanya_selesai_yang_terhitung_berakhir(status, berakhir):
    """Positif/Negatif: D8, Selesai adalah akhir sesi manual (arsip dan tampilan Dev AI)."""
    # Arrange
    from app.modules.runs.session_rules import has_ended

    # Act / Assert
    assert has_ended(status) is berakhir
