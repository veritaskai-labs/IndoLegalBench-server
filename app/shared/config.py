"""Konfigurasi aplikasi, dibaca dari environment variable.

Semua setelan aplikasi masuk ke sini. Jangan membaca os.environ langsung
dari dalam modul, selalu lewat get_settings() supaya sumbernya satu.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Aplikasi
    app_name: str = "IndoLegalBench API"
    app_env: str = "local"  # local | staging | production
    debug: bool = True

    # Database
    database_url: str = (
        "postgresql+psycopg://indolegalbench:indolegalbench@localhost:5432/indolegalbench"
    )

    # CORS, diisi dengan origin frontend
    cors_origins: list[str] = ["http://localhost:3000"]

    # fake is local/CI only; staging/production must use zitadel (enforced at boot)
    auth_oidc_mode: str = "fake"  # fake | zitadel
    public_base_url: str = "http://localhost:8000"
    zitadel_issuer: str = ""
    zitadel_client_id: str = "fake-client"
    zitadel_audience: str = ""
    zitadel_client_secret: str = ""
    zitadel_redirect_uri: str = ""
    # TODO(SCRUM-89): drop; maps personal Zitadel User ID onto the author seed
    dev_zitadel_sub: str = ""
    fake_oidc_issuer: str = "http://fake-oidc"
    fake_oidc_signing_secret: str = "fake-oidc-hs256-secret-not-for-prod"  # TODO: never use in prod

    # Sesi
    idle_timeout_minutes: int = (
        30  # TODO(SCRUM-91): enforce idle from last_activity_at, not login time
    )
    # Absolute cap set once at login. 12 hours is a proposal pending client confirmation.
    absolute_session_lifetime_minutes: int = 720
    session_cookie_name: str = "veritask_session"
    cookie_secure: bool = False  # TODO: set true behind HTTPS (staging/prod)
    # Local smoke without a frontend. Empty = {frontend_origin}/auth/done (PBI default).
    auth_done_url_override: str = ""

    # Kunci enkripsi kredensial provider, dipakai PBI-10
    # Jangan pernah di-commit. Isi lewat .env atau secret manager.
    credential_encryption_key: str = ""

    @property
    def frontend_origin(self) -> str:
        if self.cors_origins:
            return self.cors_origins[0].rstrip("/")
        return "http://localhost:3000"

    @property
    def redirect_uri(self) -> str:
        if self.zitadel_redirect_uri:
            return self.zitadel_redirect_uri.rstrip("/")
        return f"{self.public_base_url.rstrip('/')}/auth/callback"

    @property
    def post_logout_redirect_uri(self) -> str:
        return f"{self.frontend_origin}/login"

    @property
    def auth_done_url(self) -> str:
        if self.auth_done_url_override:
            return self.auth_done_url_override.rstrip("/")
        return f"{self.frontend_origin}/auth/done"


@lru_cache
def get_settings() -> Settings:
    """Dipakai sebagai dependency FastAPI, hasilnya di-cache."""
    return Settings()
