"""Unit test penyaring nilai rahasia sebelum masuk before/after.

PBI-18, PBI-10 AC2. Murni fungsi, tanpa database.
"""

import pytest

from app.modules.audit.redaction import CREDENTIAL_ROTATED, without_secrets


@pytest.mark.parametrize(
    "kunci",
    [
        "credential",
        "credential_encrypted",
        "credential_hint",
        "api_key",
        "apiKey",
        "access_token",
        "refresh_token",
        "token",
        "password",
        "client_secret",
        "Authorization",
    ],
)
def test_kunci_rahasia_dibuang(kunci):
    # Arrange
    nilai = {"name": "GPT", kunci: "sk-rahasia"}

    # Act
    hasil = without_secrets(nilai)

    # Assert
    assert hasil == {"name": "GPT"}


@pytest.mark.parametrize(
    "kunci", ["max_tokens", "token_limit_note", "secretary", "passwords_policy"]
)
def test_kunci_mirip_rahasia_tetap_dicatat(kunci):
    """Edge case: kecocokan per segmen nama, bukan potongan kata."""
    # Arrange
    nilai = {kunci: 1024}

    # Act
    hasil = without_secrets(nilai)

    # Assert
    assert hasil == {kunci: 1024}


def test_dict_bersarang_ikut_disaring():
    # Arrange
    nilai = {"config": {"headers": {"api_key": "sk-x", "model": "gpt"}}}

    # Act
    hasil = without_secrets(nilai)

    # Assert
    assert hasil == {"config": {"headers": {"model": "gpt"}}}


def test_dict_di_dalam_daftar_ikut_disaring():
    # Arrange
    nilai = {"headers": [{"token": "t-1", "name": "auth"}]}

    # Act
    hasil = without_secrets(nilai)

    # Assert
    assert hasil == {"headers": [{"name": "auth"}]}


def test_penanda_rotasi_kredensial_dipertahankan():
    """D6a: ai_product.credential_rotated cukup mencatat penanda ini."""
    # Act
    hasil = without_secrets(CREDENTIAL_ROTATED)

    # Assert
    assert hasil == {"credential": "rotated"}


def test_penanda_tidak_bisa_dipakai_menyelundupkan_nilai_lain():
    # Arrange
    nilai = {"credential": "sk-asli", "api_key": "rotated-but-real"}

    # Act
    hasil = without_secrets(nilai)

    # Assert
    assert hasil == {}


@pytest.mark.parametrize("nilai", [None, "teks", 3, ["a", 1]])
def test_nilai_bukan_dict_dikembalikan_apa_adanya(nilai):
    # Act + Assert
    assert without_secrets(nilai) == nilai


def test_masukan_tidak_diubah():
    """Penyaring membuat salinan. Dict milik pemanggil tetap utuh."""
    # Arrange
    asli = {"api_key": "sk-x", "name": "GPT"}

    # Act
    without_secrets(asli)

    # Assert
    assert asli == {"api_key": "sk-x", "name": "GPT"}
