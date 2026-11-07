"""Aturan teks, URL, dan kredensial yang dipakai bersama schema produk AI dan penilai.

Satu tempat supaya pengetatan berikutnya (misalnya menolak http:// polos saat
kredensial dikirim) berlaku untuk kedua jalur sekaligus (review #118). Dipanggil
dari field_validator pydantic, jadi galatnya ValueError biasa.
"""

from urllib.parse import urlparse

from app.shared.text import strip_blank

# Hint kredensial = 4 karakter terakhir (crypto.HINT_LENGTH); 8 memastikan hint tidak
# pernah sama dengan seluruh kunci.
JUDGE_CREDENTIAL_MIN = 8


def required_text(value: str) -> str:
    """Buang karakter kosong (termasuk zero-width) di tepi; hasil kosong ditolak."""
    bersih = strip_blank(value)
    if not bersih:
        raise ValueError("tidak boleh kosong")
    return bersih


def http_url(value: str) -> str:
    bersih = strip_blank(value)
    parsed = urlparse(bersih)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("harus berupa URL http atau https")
    return bersih


def judge_credential(value: str) -> str:
    """Kunci yang ditempel sering membawa spasi atau baris baru di ujung; dibuang dulu."""
    bersih = strip_blank(value)
    if len(bersih) < JUDGE_CREDENTIAL_MIN:
        raise ValueError(f"minimal {JUDGE_CREDENTIAL_MIN} karakter")
    return bersih
