"""Token counts from question text. SCRUM-76 SCRUM-244.

The counter reads a question and returns how many tokens the local OpenAI
encoding assigns. The integers below are cl100k_base counts written as
literals. They are not computed by the counter under test. A stand-in that
returns the character length, the word count, or a constant must fail.
"""

import logging
import socket

import pytest
import requests
import tiktoken

from app.modules.runs.tokenizer import TiktokenCounter
from app.shared.exceptions import InternalError

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


def test_menghitung_tidak_membuka_jaringan(monkeypatch):
    # Arrange: berkas encoding ada di cache. Memori dikosongkan, lalu jaringan ditutup.
    # SCRUM-76 SCRUM-244. tiktoken mengunduh lewat requests, bukan httpx.
    tiktoken.get_encoding("cl100k_base")
    tiktoken.registry.ENCODINGS.pop("cl100k_base", None)

    def meledak(*_args, **_kwargs):
        raise AssertionError("tokenizer membuka jaringan")

    monkeypatch.setattr(requests.sessions.Session, "request", meledak)
    monkeypatch.setattr(socket, "create_connection", meledak)
    penghitung = TiktokenCounter()

    # Act
    jumlah = penghitung.count(PERTANYAAN, "gemini_interactions")

    # Assert
    assert jumlah == TOKEN_PERTANYAAN


def test_gagal_memuat_encoding_adalah_kesalahan_internal_500(monkeypatch, caplog):
    # Arrange: SCRUM-76 SCRUM-244. Cache hilang bukan 500 tanpa kode.
    def gagal(_name: str) -> None:
        raise OSError("berkas encoding tidak ada")

    monkeypatch.setattr("app.modules.runs.tokenizer.tiktoken.get_encoding", gagal)
    penghitung = TiktokenCounter()

    # Act
    with caplog.at_level(logging.ERROR), pytest.raises(InternalError) as galat:
        penghitung.count(PERTANYAAN, "openai_compatible")

    # Assert
    assert galat.value.status_code == 500
    assert galat.value.code == "TOKEN_ENCODING_UNAVAILABLE"
    assert "cl100k_base" in galat.value.message
    assert any("cl100k_base" in rekaman.getMessage() for rekaman in caplog.records)


PENANDA = "lihat <|endoftext|> ya"
# cl100k_base, penanda dihitung sebagai teks biasa: sembilan token, bukan 22 huruf.
TOKEN_PENANDA = 9


def test_penanda_khusus_dihitung_sebagai_teks_biasa():
    # Arrange
    penghitung = TiktokenCounter()

    # Act
    jumlah = penghitung.count(PENANDA, "openai_compatible")

    # Assert: SCRUM-76 SCRUM-244. Penanda tidak boleh menjatuhkan ringkasan.
    assert isinstance(jumlah, int)
    assert jumlah == TOKEN_PENANDA
    assert jumlah != len(PENANDA)
