"""Pertukaran kode OIDC dengan Zitadel, PBI-1 (SCRUM-68) sub task QA (SCRUM-96).

test_oidc_zitadel.py yang sudah ada hanya memeriksa bentuk URL dan
kegagalan konfigurasi; kode menyebutnya sendiri: discovery, token, JWKS,
dan userinfo belum diuji. File ini menutup bagian itu.

Zitadel diganti httpx.MockTransport, jadi test tidak menyentuh jaringan
dan tidak butuh kredensial Veritask. Yang dibuktikan adalah perilaku
klien kita: nonce dicocokkan, kegagalan IdP tidak bocor ke pengguna, dan
userinfo dipakai hanya bila ID token kurang lengkap.
"""

import httpx
import pytest
from _oidc_keys import AUDIENCE, ISSUER, JWK_SAH, id_token

from app.modules.auth import oidc_zitadel
from app.modules.auth.oidc_zitadel import ZitadelOidcClient
from app.shared.config import Settings
from app.shared.exceptions import OidcExchangeFailedError

TOKEN_ENDPOINT = f"{ISSUER}/oauth/v2/token"
JWKS_URI = f"{ISSUER}/oauth/v2/keys"
USERINFO_ENDPOINT = f"{ISSUER}/oidc/v1/userinfo"

DISCOVERY = {
    "issuer": ISSUER,
    "token_endpoint": TOKEN_ENDPOINT,
    "jwks_uri": JWKS_URI,
    "userinfo_endpoint": USERINFO_ENDPOINT,
}

_HTTPX_CLIENT_ASLI = httpx.Client


def _settings() -> Settings:
    return Settings(
        auth_oidc_mode="zitadel",
        zitadel_issuer=ISSUER,
        zitadel_client_id=AUDIENCE,
        zitadel_redirect_uri="http://localhost:8000/auth/callback",
        cors_origins=["http://localhost:3000"],
    )


@pytest.fixture
def zitadel_palsu(monkeypatch):
    """Ganti Zitadel dengan server tiruan; kembalikan pencatat permintaan.

    exchange_code membuat httpx.Client sendiri, jadi kelasnya yang
    diganti supaya seluruh panggilan keluar diarahkan ke MockTransport.
    """
    jejak: list[str] = []
    perilaku = {
        "token_status": 200,
        "token_body": None,
        "userinfo": {"email": "author@veritask.test", "name": "Author One"},
        "discovery_status": 200,
    }

    def tangani(request: httpx.Request) -> httpx.Response:
        jejak.append(str(request.url))
        if str(request.url) == TOKEN_ENDPOINT:
            perilaku["badan_permintaan_token"] = request.content.decode()
        if request.url.path.endswith("/.well-known/openid-configuration"):
            if perilaku["discovery_status"] != 200:
                return httpx.Response(perilaku["discovery_status"])
            return httpx.Response(200, json=DISCOVERY)
        if str(request.url) == TOKEN_ENDPOINT:
            if perilaku["token_status"] != 200:
                return httpx.Response(perilaku["token_status"], json={"error": "invalid_grant"})
            return httpx.Response(200, json=perilaku["token_body"])
        if str(request.url) == JWKS_URI:
            return httpx.Response(200, json={"keys": [JWK_SAH]})
        if str(request.url) == USERINFO_ENDPOINT:
            return httpx.Response(200, json=perilaku["userinfo"])
        return httpx.Response(404)

    def klien_palsu(*args, **kwargs):
        kwargs.pop("timeout", None)
        return _HTTPX_CLIENT_ASLI(transport=httpx.MockTransport(tangani), **kwargs)

    monkeypatch.setattr(oidc_zitadel.httpx, "Client", klien_palsu)
    perilaku["jejak"] = jejak
    return perilaku


def _tukar(perilaku: dict, *, nonce: str = "nonce-1", **token_klaim):
    if perilaku["token_body"] is None:
        perilaku["token_body"] = {
            "id_token": id_token(nonce="nonce-1", **token_klaim),
            "access_token": "access-token-uji",
        }
    client = ZitadelOidcClient(_settings())
    return client.exchange_code(code="kode-1", code_verifier="verifier-1", expected_nonce=nonce)


def test_penukaran_kode_berhasil_mengembalikan_identitas(zitadel_palsu):
    hasil = _tukar(zitadel_palsu)

    assert hasil.sub == "111111111111111111"
    assert hasil.email == "author@veritask.test"
    assert hasil.name == "Author One"
    assert hasil.raw_id_token


def test_discovery_diambil_sekali_lalu_dipakai_ulang(zitadel_palsu):
    client = ZitadelOidcClient(_settings())
    zitadel_palsu["token_body"] = {
        "id_token": id_token(nonce="nonce-1"),
        "access_token": "access-token-uji",
    }

    for _ in range(2):
        client.exchange_code(code="kode-1", code_verifier="verifier-1", expected_nonce="nonce-1")

    discovery = [u for u in zitadel_palsu["jejak"] if u.endswith("openid-configuration")]
    assert len(discovery) == 1


def test_token_endpoint_menolak_maka_pesannya_tidak_membocorkan_detail(zitadel_palsu):
    zitadel_palsu["token_status"] = 400

    with pytest.raises(OidcExchangeFailedError) as info:
        _tukar(zitadel_palsu)

    assert "invalid_grant" not in str(info.value)


def test_nonce_yang_tidak_cocok_ditolak(zitadel_palsu):
    """Penjaga replay: token untuk permintaan login lain tidak boleh diterima."""
    with pytest.raises(OidcExchangeFailedError, match="Nonce mismatch"):
        _tukar(zitadel_palsu, nonce="nonce-milik-permintaan-lain")


def test_discovery_gagal_maka_penukaran_gagal_rapi(zitadel_palsu):
    zitadel_palsu["discovery_status"] = 500

    with pytest.raises(OidcExchangeFailedError):
        _tukar(zitadel_palsu)


def test_id_token_tanpa_email_dilengkapi_dari_userinfo(zitadel_palsu):
    zitadel_palsu["token_body"] = {
        "id_token": id_token(nonce="nonce-1", email=None, name=None),
        "access_token": "access-token-uji",
    }
    zitadel_palsu["userinfo"] = {"email": "dari.userinfo@veritask.test", "name": "Dari Userinfo"}

    hasil = _tukar(zitadel_palsu)

    assert hasil.email == "dari.userinfo@veritask.test"
    assert hasil.name == "Dari Userinfo"
    assert any(u == USERINFO_ENDPOINT for u in zitadel_palsu["jejak"])


def test_client_secret_dikirim_hanya_kalau_dikonfigurasi(zitadel_palsu):
    """Aplikasi publik dengan PKCE tidak punya secret; aplikasi rahasia punya."""
    zitadel_palsu["token_body"] = {
        "id_token": id_token(nonce="nonce-1"),
        "access_token": "access-token-uji",
    }
    pengaturan = _settings()

    ZitadelOidcClient(pengaturan).exchange_code(
        code="kode-1", code_verifier="verifier-1", expected_nonce="nonce-1"
    )
    assert "client_secret" not in zitadel_palsu["badan_permintaan_token"]

    pengaturan.zitadel_client_secret = "rahasia-uji"
    ZitadelOidcClient(pengaturan).exchange_code(
        code="kode-1", code_verifier="verifier-1", expected_nonce="nonce-1"
    )
    assert "client_secret=rahasia-uji" in zitadel_palsu["badan_permintaan_token"]


def test_client_id_wajib_diisi_saat_mode_zitadel():
    with pytest.raises(RuntimeError, match="ZITADEL_CLIENT_ID"):
        ZitadelOidcClient(
            Settings(auth_oidc_mode="zitadel", zitadel_issuer=ISSUER, zitadel_client_id="")
        )
