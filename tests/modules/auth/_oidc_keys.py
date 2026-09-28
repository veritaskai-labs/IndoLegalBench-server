"""Kunci RSA dan pembuat ID token untuk test OIDC. Bukan berkas test.

Dipakai bersama oleh test_oidc_jwt_security.py dan
test_oidc_zitadel_exchange.py. Pembuatan kunci RSA mahal, jadi dibuat
sekali saat modul diimpor lalu dipakai ulang.
"""

from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from jose.utils import base64url_encode

ISSUER = "https://tenant.zitadel.cloud"
AUDIENCE = "client-veritask"
KID = "kunci-uji"


def _buat_kunci(kid: str) -> tuple[str, dict]:
    """Kembalikan (private key PEM, kunci publik bentuk JWKS) untuk RS256."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    angka = private.public_key().public_numbers()

    def b64(nilai: int) -> str:
        lebar = (nilai.bit_length() + 7) // 8
        return base64url_encode(nilai.to_bytes(lebar, "big")).decode()

    return pem, {
        "kty": "RSA",
        "kid": kid,
        "alg": "RS256",
        "use": "sig",
        "n": b64(angka.n),
        "e": b64(angka.e),
    }


PEM_SAH, JWK_SAH = _buat_kunci(KID)
PEM_PENYERANG, _ = _buat_kunci("kunci-penyerang")


def klaim(**ubah) -> dict:
    """Klaim ID token yang sah, bisa ditimpa untuk kasus negatif."""
    sekarang = datetime.now(UTC)
    isi = {
        "iss": ISSUER,
        "sub": "111111111111111111",
        "aud": AUDIENCE,
        "exp": int((sekarang + timedelta(minutes=5)).timestamp()),
        "iat": int(sekarang.timestamp()),
        "email": "author@veritask.test",
        "name": "Author One",
    }
    isi.update(ubah)
    return isi


def id_token(*, pem: str = PEM_SAH, kid: str = KID, **ubah) -> str:
    return jwt.encode(klaim(**ubah), pem, algorithm="RS256", headers={"kid": kid})
