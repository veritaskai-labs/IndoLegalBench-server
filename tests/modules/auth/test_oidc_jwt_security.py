"""Negative test verifikasi ID token, PBI-1 (SCRUM-68) sub task QA (SCRUM-96).

test_oidc_jwt.py yang sudah ada menguji dua hal: pemilihan nama tampilan
dan penolakan algoritma yang tidak didukung. File ini melengkapi sisanya,
yaitu hal-hal yang membuat verifikasi token benar-benar berguna: token
kedaluwarsa, audience salah, issuer salah, tanda tangan palsu, dan kunci
yang tidak dikenal.

Tidak ada panggilan ke Zitadel. Kunci RSA dibuat sendiri dan JWKS
disajikan lewat httpx.MockTransport, jadi test tetap cepat dan tidak
bergantung jaringan.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from _oidc_keys import AUDIENCE, ISSUER, JWK_SAH, KID, PEM_PENYERANG, id_token, klaim
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError, JWTError

from app.modules.auth.oidc_jwt import verify_id_token
from app.shared.exceptions import OidcExchangeFailedError


@pytest.fixture
def http_jwks():
    """Client httpx palsu yang menyajikan JWKS berisi kunci sah."""

    def tangani(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [JWK_SAH]})

    with httpx.Client(transport=httpx.MockTransport(tangani)) as client:
        yield client


def _verifikasi(raw: str, http: httpx.Client, **ubah):
    argumen = {"issuer": ISSUER, "audience": AUDIENCE}
    argumen.update(ubah)
    return verify_id_token(raw, {"jwks_uri": f"{ISSUER}/oauth/v2/keys"}, http, **argumen)


def test_token_sah_diterima_dan_klaimnya_dikembalikan(http_jwks):
    hasil = _verifikasi(id_token(), http_jwks)

    assert hasil["sub"] == "111111111111111111"
    assert hasil["email"] == "author@veritask.test"


def test_token_kedaluwarsa_ditolak(http_jwks):
    sudah_lewat = datetime.now(UTC) - timedelta(minutes=1)

    with pytest.raises(ExpiredSignatureError):
        _verifikasi(id_token(exp=int(sudah_lewat.timestamp())), http_jwks)


def test_audience_milik_aplikasi_lain_ditolak(http_jwks):
    with pytest.raises(JWTClaimsError):
        _verifikasi(id_token(aud="aplikasi-lain"), http_jwks)


def test_issuer_yang_bukan_tenant_kita_ditolak(http_jwks):
    with pytest.raises(JWTClaimsError):
        _verifikasi(id_token(iss="https://penyerang.example"), http_jwks)


def test_tanda_tangan_dari_kunci_lain_ditolak(http_jwks):
    """Penyerang menandatangani dengan kuncinya sendiri, tapi memakai kid yang sah."""
    palsu = jwt.encode(klaim(), PEM_PENYERANG, algorithm="RS256", headers={"kid": KID})

    with pytest.raises(JWTError):
        _verifikasi(palsu, http_jwks)


def test_kid_yang_tidak_ada_di_jwks_ditolak(http_jwks):
    with pytest.raises(OidcExchangeFailedError, match="No JWKS key"):
        _verifikasi(id_token(kid="kid-tak-dikenal"), http_jwks)


def test_jwks_tidak_bisa_diambil_maka_verifikasi_gagal():
    def tangani(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with (
        httpx.Client(transport=httpx.MockTransport(tangani)) as http,
        pytest.raises(httpx.HTTPStatusError),
    ):
        _verifikasi(id_token(), http)
