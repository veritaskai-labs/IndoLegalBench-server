"""Factory guards: fake IdP is local/CI only."""

import pytest

from app.modules.auth.oidc import ensure_fake_oidc_allowed, oidc_client_for
from app.modules.auth.oidc_fake import FakeOidcClient
from app.modules.auth.oidc_zitadel import ZitadelOidcClient
from app.shared.config import Settings


def test_fake_oidc_allowed_in_local():
    settings = Settings(app_env="local", auth_oidc_mode="fake")
    ensure_fake_oidc_allowed(settings)
    assert isinstance(oidc_client_for(settings), FakeOidcClient)


def test_fake_oidc_refused_in_staging():
    settings = Settings(app_env="staging", auth_oidc_mode="fake")
    with pytest.raises(RuntimeError, match="fake is not allowed"):
        ensure_fake_oidc_allowed(settings)
    with pytest.raises(RuntimeError, match="fake is not allowed"):
        oidc_client_for(settings)


def test_fake_oidc_refused_in_production():
    settings = Settings(app_env="production", auth_oidc_mode="fake")
    with pytest.raises(RuntimeError, match="fake is not allowed"):
        oidc_client_for(settings)


def test_zitadel_allowed_in_staging():
    settings = Settings(
        app_env="staging",
        auth_oidc_mode="zitadel",
        zitadel_issuer="https://example.zitadel.cloud",
        zitadel_client_id="client",
    )
    ensure_fake_oidc_allowed(settings)
    assert isinstance(oidc_client_for(settings), ZitadelOidcClient)
