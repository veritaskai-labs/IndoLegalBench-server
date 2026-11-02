"""Token counts from question text. SCRUM-76 SCRUM-244.

The counter reads a question and returns how many tokens the local OpenAI
encoding assigns. The integers below are cl100k_base counts written as
literals. They are not computed by the counter under test. A stand-in that
returns the character length, the word count, or a constant must fail.
"""

import httpx
import pytest

from app.modules.runs.tokenizer import TiktokenCounter

SAPAAN = "hello"
PERTANYAAN = "Apakah PHK tanpa surat sah?"
# cl100k_base: "hello" is one token; the question is eleven, not 27 letters or 5 words.
TOKEN_SAPAAN = 1
TOKEN_PERTANYAAN = 11


def test_teks_kosong_berjumlah_nol_token():
    # Arrange
    penghitung = TiktokenCounter()

    # Act
    jumlah = penghitung.count("", "openai_compatible")

    # Assert
    assert jumlah == 0


def test_hello_mengembalikan_jumlah_token_cl100k():
    # Arrange
    penghitung = TiktokenCounter()

    # Act
    jumlah = penghitung.count(SAPAAN, "openai_compatible")

    # Assert: satu token, bukan lima huruf
    assert jumlah == TOKEN_SAPAAN
    assert jumlah != len(SAPAAN)


def test_pertanyaan_hukum_mengembalikan_jumlah_token_bukan_panjang_teks():
    # Arrange
    penghitung = TiktokenCounter()

    # Act
    jumlah = penghitung.count(PERTANYAAN, "openai_compatible")

    # Assert
    assert jumlah == TOKEN_PERTANYAAN
    assert jumlah != len(PERTANYAAN)
    assert jumlah != len(PERTANYAAN.split())


def test_teks_yang_lebih_panjang_menghasilkan_lebih_banyak_token():
    # Arrange
    penghitung = TiktokenCounter()

    # Act
    pendek = penghitung.count(SAPAAN, "openai_compatible")
    panjang = penghitung.count(PERTANYAAN, "openai_compatible")

    # Assert: jawaban tetap tidak boleh sama untuk kedua teks
    assert panjang > pendek


@pytest.mark.parametrize(
    "provider_type",
    ["openai_compatible", "gemini_interactions", "anthropic_messages"],
)
def test_setiap_provider_mengembalikan_jumlah_token_openai_yang_sama(provider_type):
    # Arrange
    penghitung = TiktokenCounter()

    # Act
    jumlah = penghitung.count(PERTANYAAN, provider_type)

    # Assert
    assert jumlah == TOKEN_PERTANYAAN


def test_menghitung_tidak_membuka_klien_http(monkeypatch):
    # Arrange: klien HTTP proyek meledak. Penghitung lokal tidak boleh menyentuhnya.
    def meledak(*_args, **_kwargs):
        raise AssertionError("tokenizer membuka jaringan")

    monkeypatch.setattr(httpx.Client, "request", meledak)
    monkeypatch.setattr(httpx, "request", meledak)
    penghitung = TiktokenCounter()

    # Act
    jumlah = penghitung.count(PERTANYAAN, "gemini_interactions")

    # Assert
    assert jumlah == TOKEN_PERTANYAAN
