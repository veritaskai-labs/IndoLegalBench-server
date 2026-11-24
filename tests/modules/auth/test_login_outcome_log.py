"""One log line per login result, readable by any log tool. PBI-1 login, platform monitoring.

/auth/callback always answers 302, also when the login fails: the error code goes to
/auth/done?error=CODE (router.py). So an uptime check and 5xx error tracking both see "fine"
while nobody can log in. The only place a failed login is visible is a line written here.

Veritask asked (24 Nov) that no third-party monitoring be installed; this is plain Python
logging to the container's stdout, which Veritask's own log tooling can collect.

Label: Positif = normal path, Negatif = a failure that must be visible, Edge = why the boundary
matters.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app.main import app
from app.modules.auth.oidc import get_oidc_client, oidc_client_for
from app.modules.auth.seeds import AUTHOR_SUB, DEACTIVATED_SUB, UNKNOWN_SUB, seed_users
from app.shared.config import get_settings
from app.shared.exceptions import OidcExchangeFailedError
from tests.login import complete_login

LOGGER = "app.auth.login"
ROOT = Path(__file__).resolve().parents[3]


def _login_lines(caplog) -> list[tuple[str, str]]:
    return [(r.levelname, r.getMessage()) for r in caplog.records if r.name == LOGGER]


@pytest.fixture
def log(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    return caplog


def test_login_berhasil_ditulis_satu_baris_ok(client, db_session, log):
    """Positif: login yang berhasil juga dicatat, supaya tingkat keberhasilan bisa dihitung
    (gagal 5 dari 6 jauh berbeda dengan gagal 5 dari 500)."""
    # Act
    callback = complete_login(client, db_session, AUTHOR_SUB)

    # Assert
    assert callback.status_code == 302
    assert _login_lines(log) == [("INFO", "login outcome=ok")]


@pytest.mark.parametrize(
    ("sub", "baris"),
    [
        (UNKNOWN_SUB, "login outcome=USER_NOT_REGISTERED kind=account"),
        (DEACTIVATED_SUB, "login outcome=USER_DEACTIVATED kind=account"),
    ],
    ids=["belum-terdaftar", "nonaktif"],
)
def test_akun_yang_ditolak_ditulis_sebagai_masalah_akun(client, db_session, log, sub, baris):
    """Negatif: akun belum terdaftar atau dinonaktifkan. Yang perlu bertindak Admin, bukan
    tim pengembang, jadi levelnya INFO dan jenisnya account."""
    # Act
    complete_login(client, db_session, sub)

    # Assert
    assert _login_lines(log) == [("INFO", baris)]


def test_state_hilang_ditulis_sebagai_warning_tanpa_kode_rahasia(client, log):
    """Edge: state login hilang paling sering terjadi setelah deploy, karena API yang restart
    membuang login yang belum selesai (pending.py menyimpannya di memori). URL callback membawa
    `code` sekali pakai; kalau baris log memuatnya, log menjadi tempat bocor kode itu."""
    # Act
    client.get(
        "/auth/callback",
        params={"code": "kode-rahasia-sekali-pakai", "state": "state-tidak-dikenal"},
        follow_redirects=False,
    )

    # Assert
    assert _login_lines(log) == [("WARNING", "login outcome=INVALID_OIDC_STATE kind=flow")]
    semua = "\n".join(r.getMessage() for r in log.records)
    assert "kode-rahasia-sekali-pakai" not in semua
    assert "state-tidak-dikenal" not in semua


class _ZitadelGagal:
    """Stub: the login starts normally, then the token exchange with Zitadel fails."""

    def __init__(self, asli):
        self._asli = asli

    def authorization_url(self, **kwargs):
        return self._asli.authorization_url(**kwargs)

    def end_session_url(self, **kwargs):
        return self._asli.end_session_url(**kwargs)

    def exchange_code(self, **_kwargs):
        raise OidcExchangeFailedError("token exchange failed")


def test_pertukaran_token_gagal_ditulis_sebagai_error_sistem(client, db_session, log):
    """Negatif: Zitadel mati, salah konfigurasi, atau jam server meleset. Tidak bisa diperbaiki
    pengguna dan semua orang ikut tidak bisa login, jadi ini satu-satunya kegagalan login
    berlevel ERROR (yang biasanya dipakai sebagai pemicu alert)."""
    # Arrange: mulai login seperti biasa sampai IdP mengirim balik code dan state.
    seed_users(db_session)
    login = client.get("/auth/login", params={"sub": AUTHOR_SUB}, follow_redirects=False)
    authorize = client.get(login.headers["location"], follow_redirects=False)
    kembali = parse_qs(urlparse(authorize.headers["location"]).query)
    app.dependency_overrides[get_oidc_client] = lambda: _ZitadelGagal(
        oidc_client_for(get_settings())
    )
    try:
        # Act
        client.get(
            "/auth/callback",
            params={"code": kembali["code"][0], "state": kembali["state"][0]},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(get_oidc_client, None)

    # Assert
    assert _login_lines(log) == [("ERROR", "login outcome=OIDC_EXCHANGE_FAILED kind=system")]


def test_baris_login_benar_benar_sampai_ke_stdout_container(tmp_path):
    """Edge: di bawah uvicorn, logger app.* tidak punya handler. Baris WARNING ke atas masih
    keluar lewat handler cadangan Python, tetapi baris INFO (login berhasil, akun ditolak)
    hilang begitu saja. Docker hanya mengumpulkan stdout/stderr, jadi kalau baris ini tidak
    sampai, tidak ada tool log mana pun yang bisa membacanya."""
    # Arrange
    program = (
        "import logging, app.main\nlogging.getLogger('app.auth.login').info('login outcome=ok')\n"
    )
    env = {**os.environ, "DATABASE_URL": "sqlite://", "AUTH_OIDC_MODE": "fake", "APP_ENV": "local"}

    # Act
    hasil = subprocess.run(
        [sys.executable, "-c", program], cwd=ROOT, env=env, capture_output=True, text=True
    )

    # Assert
    assert hasil.returncode == 0, hasil.stderr
    assert "login outcome=ok" in hasil.stdout
