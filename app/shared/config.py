"""Konfigurasi aplikasi, dibaca dari environment variable.

Semua setelan aplikasi masuk ke sini. Jangan membaca os.environ langsung
dari dalam modul, selalu lewat get_settings() supaya sumbernya satu.
"""

from decimal import Decimal
from functools import lru_cache
from typing import Annotated

from pydantic import Field
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

    # Sesi: idle slides last_activity_at; absolute cap is set once at login.
    idle_timeout_minutes: int = 30
    # 12 hours is a proposal pending client confirmation.
    absolute_session_lifetime_minutes: int = 720
    session_cookie_name: str = "veritask_session"
    cookie_secure: bool = False  # TODO: set true behind HTTPS (staging/prod)
    # Local smoke without a frontend. Empty = {frontend_origin}/auth/done (PBI default).
    auth_done_url_override: str = ""

    # Kunci enkripsi kredensial provider, dipakai PBI-10
    # Jangan pernah di-commit. Isi lewat .env atau secret manager.
    credential_encryption_key: str = ""

    # PBI-18 AC7: koneksi role ilb_retention, khusus job retensi. Bukan DATABASE_URL:
    # role aplikasi tidak pernah boleh menghapus audit log.
    audit_retention_database_url: str = ""

    # PBI-11 AC5: perkiraan biaya pengukuran. Harga dan kurs sengaja tanpa nilai
    # bawaan supaya tidak ada angka tebakan; kosong berarti 500 saat dipakai.
    cost_safety_margin_percent: Decimal = Field(default=Decimal("20"), ge=0)
    # Kunci "provider_type:model_name", nilai USD per 1 juta token output.
    cost_model_prices: dict[str, Annotated[Decimal, Field(ge=0)]] = {}
    cost_usd_idr_fallback_rate: Decimal | None = Field(default=None, gt=0)

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
