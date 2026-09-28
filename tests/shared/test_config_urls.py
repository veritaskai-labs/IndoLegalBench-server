"""URL turunan di config dan pilihan connect_args engine.

PBI-1 (SCRUM-68), sub task QA (SCRUM-96). Nilai-nilai ini menentukan ke
mana pengguna dilempar setelah login dan setelah logout, jadi salah satu
huruf saja membuat Zitadel menolak seluruh alur login.
"""

import pytest
from sqlalchemy import create_engine as create_engine_asli

from app.shared import database
from app.shared.config import Settings


def test_frontend_origin_diambil_dari_cors_pertama():
    settings = Settings(cors_origins=["https://app.veritask.ai", "http://localhost:3000"])

    assert settings.frontend_origin == "https://app.veritask.ai"


def test_frontend_origin_punya_nilai_cadangan_kalau_cors_kosong():
    """Tanpa cadangan, post_logout_redirect_uri jadi string kosong dan Zitadel menolaknya."""
    assert Settings(cors_origins=[]).frontend_origin == "http://localhost:3000"


def test_redirect_uri_dihitung_dari_public_base_url_kalau_tidak_diisi():
    settings = Settings(zitadel_redirect_uri="", public_base_url="http://localhost:8000/")

    assert settings.redirect_uri == "http://localhost:8000/auth/callback"


def test_redirect_uri_yang_diisi_manual_dipakai_apa_adanya():
    settings = Settings(zitadel_redirect_uri="https://api.veritask.ai/auth/callback/")

    assert settings.redirect_uri == "https://api.veritask.ai/auth/callback"


def test_post_logout_selalu_ke_halaman_login_frontend():
    settings = Settings(cors_origins=["https://app.veritask.ai"])

    assert settings.post_logout_redirect_uri == "https://app.veritask.ai/login"


def test_auth_done_url_bawaan_mengikuti_frontend():
    settings = Settings(cors_origins=["https://app.veritask.ai"], auth_done_url_override="")

    assert settings.auth_done_url == "https://app.veritask.ai/auth/done"


def test_auth_done_url_bisa_ditimpa_untuk_uji_coba_tanpa_frontend():
    settings = Settings(auth_done_url_override="http://localhost:8000/auth/done/")

    assert settings.auth_done_url == "http://localhost:8000/auth/done"


@pytest.fixture
def engine_terpantau(monkeypatch):
    """Tangkap argumen create_engine tanpa benar-benar menghubungi database."""
    dipakai: dict = {}

    def create_engine_palsu(url, **kwargs):
        dipakai["url"] = url
        dipakai.update(kwargs)
        return create_engine_asli("sqlite://")

    monkeypatch.setattr(database, "create_engine", create_engine_palsu)
    database.get_engine.cache_clear()
    yield dipakai
    database.get_engine.cache_clear()


def test_postgres_diberi_batas_waktu_koneksi(engine_terpantau, monkeypatch):
    """Tanpa connect_timeout, /health menggantung menit-menitan saat database mati."""
    monkeypatch.setattr(
        database,
        "get_settings",
        lambda: Settings(database_url="postgresql+psycopg://u:p@localhost:5432/db"),
    )

    database.get_engine()

    assert engine_terpantau["connect_args"] == {"connect_timeout": 3}


def test_sqlite_diberi_check_same_thread_bukan_connect_timeout(engine_terpantau, monkeypatch):
    """connect_timeout hanya dikenal driver PostgreSQL; SQLite butuh yang lain."""
    monkeypatch.setattr(
        database, "get_settings", lambda: Settings(database_url="sqlite:///./uji.db")
    )

    database.get_engine()

    assert engine_terpantau["connect_args"] == {"check_same_thread": False}
