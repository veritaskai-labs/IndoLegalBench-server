"""QA gangguan teknis: jawaban kosong, penolakan, dan judge error. PBI-14 AC4; SCRUM-229.

Jawaban kosong atau penolakan dihitung gangguan teknis, bukan jawaban salah, jadi tidak
diberi skor. Yang paling berisiko di sini bukan penolakan yang jelas, tetapi batasnya:
jawaban hukum sungguhan yang dibuka dengan penafian "sebagai model AI" harus tetap
dinilai. Kalau batas itu bergeser, jawaban yang sah dibuang diam-diam dari skor dan
nilai produk ikut turun tanpa jejak.

Tiga kasus dasarnya sudah dijaga test_session_grade.py (jawaban kosong, kegagalan
penyedia, judge error), jadi file ini menguji batas yang belum terjaga.

Label: Positif = dinilai, Negatif = gangguan teknis, Edge = batas perilaku.
"""

import pytest

from app.modules.runs.models import AttemptStatus
from app.modules.runs.session_grade import (
    check_technical_disruption,
    is_ai_refusal,
    is_empty_answer,
)

JAWABAN_SAH = (
    "Maaf, saya tidak dapat memberikan nasihat hukum resmi. Namun berdasarkan "
    "Pasal 156 ayat (1) Undang-Undang Nomor 13 Tahun 2003, pekerja yang diberhentikan "
    "berhak atas uang pesangon yang besarnya dihitung dari masa kerjanya."
)
PENOLAKAN = "Maaf, saya tidak dapat menjawab pertanyaan ini."


# Penafian di depan jawaban sungguhan tidak membuang jawabannya


def test_penafian_lalu_jawaban_hukum_tetap_dinilai():
    """Positif: penafian yang disusul analisis hukum bukan penolakan (AC4)."""
    assert is_ai_refusal(JAWABAN_SAH) is False


def test_penafian_lalu_jawaban_hukum_bukan_gangguan_teknis():
    """Positif: jawaban itu masuk penilaian, bukan dilewati (AC4)."""
    assert (
        check_technical_disruption(
            status=AttemptStatus.SUCCEEDED,
            answer_text=JAWABAN_SAH,
        )
        is False
    )


@pytest.mark.parametrize("kata_sambung", ["Namun", "Tetapi", "Adapun", "Berikut"])
def test_setiap_kata_sambung_penafian_menyelamatkan_jawaban(kata_sambung):
    """Edge: daftar kata sambung penafian dikunci, satu pun hilang akan terlihat (AC4)."""
    teks = f"Maaf, sebagai model AI saya tidak berwenang. {kata_sambung} ketentuannya diatur tersendiri."

    assert is_ai_refusal(teks) is False


@pytest.mark.parametrize(
    "teks",
    [
        "Maaf, sebagai model AI saya tidak berwenang. "
        "Namun saya tetap tidak dapat menjawab pertanyaan ini.",
        "Maaf, sebagai asisten AI saya tidak berwenang. "
        "Tetapi saya tidak bisa menjawab pertanyaan ini.",
    ],
)
def test_penolakan_berlapis_dengan_kata_sambung_tetap_gangguan_teknis(teks):
    assert is_ai_refusal(teks) is True

    assert (
        check_technical_disruption(
            status=AttemptStatus.SUCCEEDED,
            answer_text=teks,
        )
        is True
    )


def test_penolakan_yang_menyebut_dasar_hukum_tetap_dinilai():
    """Edge: adanya substansi hukum mengalahkan frasa penolakan (AC4)."""
    teks = "Maaf, saya tidak dapat menjawab. Rujukannya Pasal 1365 KUHPer."

    assert is_ai_refusal(teks) is False


# Penolakan sungguhan tetap ditandai


def test_penolakan_pendek_tanpa_substansi_adalah_gangguan_teknis():
    """Negatif: frasa penolakan, tanpa dasar hukum, dan pendek (AC4)."""
    assert is_ai_refusal(PENOLAKAN) is True


def test_penanda_platform_menolak_selalu_gangguan_teknis():
    """Negatif: penanda eksplisit tidak butuh tebak-tebakan panjang teks (AC4)."""
    assert is_ai_refusal("[Platform menolak menjawab] lihat catatan pengisi.") is True


def test_flag_penyedia_ai_refused_dipercaya_tanpa_melihat_teks():
    """Negatif: penyedia sudah bilang menolak, teksnya tidak perlu dibaca (AC4)."""
    assert is_ai_refusal("Pasal 156 Undang-Undang Nomor 13 Tahun 2003.", "ai_refused") is True


# Gangguan teknis tidak diberi skor


@pytest.mark.parametrize("teks", ["", "   ", "\n\t ", None])
def test_jawaban_kosong_atau_hanya_spasi_adalah_gangguan_teknis(teks):
    """Negatif: kosong dan whitespace saja sama-sama tanpa skor (AC4)."""
    assert is_empty_answer(teks) is True
    assert check_technical_disruption(status=AttemptStatus.SUCCEEDED, answer_text=teks) is True


def test_judge_error_adalah_gangguan_teknis_walau_jawabannya_bagus():
    """Negatif: gagalnya penilai bukan salahnya jawaban (AC4)."""
    assert (
        check_technical_disruption(
            status=AttemptStatus.SUCCEEDED,
            answer_text=JAWABAN_SAH,
            judge_error="timeout saat memanggil penilai",
        )
        is True
    )


def test_platform_menolak_pada_sesi_manual_adalah_gangguan_teknis():
    """Negatif: opsi 'Platform menolak menjawab' tidak dinilai salah (AC4, PBI-22 AC6)."""
    assert (
        check_technical_disruption(
            status=AttemptStatus.SUCCEEDED,
            answer_text=None,
            platform_refused=True,
        )
        is True
    )


@pytest.mark.parametrize(
    "failure_kind",
    ["no_response", "provider_error", "safety_blocked", "truncated", "timeout"],
)
def test_setiap_kegagalan_penyedia_adalah_gangguan_teknis(failure_kind):
    """Negatif: daftar kegagalan teknis penyedia dikunci seluruhnya (AC4)."""
    assert (
        check_technical_disruption(
            status=AttemptStatus.SUCCEEDED,
            answer_text=JAWABAN_SAH,
            failure_kind=failure_kind,
        )
        is True
    )


def test_jawaban_hukum_normal_tidak_pernah_jadi_gangguan_teknis():
    """Positif: jalur bahagia, supaya tes di atas tidak lolos karena selalu True (AC4)."""
    assert (
        check_technical_disruption(
            status=AttemptStatus.SUCCEEDED,
            answer_text="Pesangon diatur Pasal 156 Undang-Undang Nomor 13 Tahun 2003.",
        )
        is False
    )
