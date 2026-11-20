"""GET /ai-monitoring. Admin dan Developer AI. Host cloud tidak dihubungi."""

from types import SimpleNamespace

import httpx
import pytest

from app.main import app
from app.modules.monitoring.router import get_probe
from app.shared.config import get_settings
from app.shared.langfuse_tracing import client, reset_client
from app.shared.security import Role

URL = "/ai-monitoring"


def _settings(**ubah):
    dasar = get_settings().model_copy(update=ubah)
    return dasar


@pytest.mark.parametrize("role", [Role.ADMIN, Role.DEVELOPER_AI])
def test_admin_dan_developer_ai_boleh_membuka(as_role, role):
    """Positif: kedua peran yang membuka Eksekusi juga membuka pemantauan."""
    app.dependency_overrides[get_settings] = lambda: _settings(langfuse_base_url="")
    response = as_role(role).get(URL)
    assert response.status_code == 200
    assert response.json() == {"url": None, "reachable": False}


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.VIEWER])
def test_peran_lain_ditolak(as_role, role):
    """Negatif: Kontributor dan Viewer tidak membuka halaman ini."""
    response = as_role(role).get(URL)
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_tanpa_login_ditolak(client):
    """Negatif: tanpa sesi tidak ada URL yang keluar."""
    response = client.get(URL)
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_probe_gagal_tetap_200(as_role):
    """Edge: health yang gagal tidak membuat endpoint error. reachable false."""
    app.dependency_overrides[get_settings] = lambda: _settings(
        langfuse_base_url="http://127.0.0.1:3000"
    )
    app.dependency_overrides[get_probe] = lambda: lambda url: False
    response = as_role(Role.ADMIN).get(URL)
    assert response.status_code == 200
    assert response.json() == {"url": "http://127.0.0.1:3000", "reachable": False}


def test_host_cloud_tidak_dihubungi(as_role, monkeypatch):
    """Negatif: *.langfuse.com tidak menjadi URL dan tidak dipanggil."""
    called = {"n": 0}

    def boom(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("cloud host was contacted")

    monkeypatch.setattr(httpx, "get", boom)
    monkeypatch.setattr(httpx, "Client", boom)
    app.dependency_overrides[get_settings] = lambda: _settings(
        langfuse_base_url="https://jp.cloud.langfuse.com"
    )
    app.dependency_overrides[get_probe] = lambda: boom
    response = as_role(Role.ADMIN).get(URL)
    assert response.status_code == 200
    assert response.json() == {"url": None, "reachable": False}
    assert called["n"] == 0


def test_kunci_dan_url_kosong_tidak_membuat_klien(monkeypatch):
    """Negatif: URL kosong tidak membangun klien dan tidak membuka jaringan."""
    constructed = {"n": 0}

    class _Langfuse:
        def __init__(self, *args, **kwargs):
            constructed["n"] += 1
            raise AssertionError("client constructed")

    monkeypatch.setattr(
        "app.shared.langfuse_tracing.get_settings",
        lambda: SimpleNamespace(
            langfuse_public_key="ada",
            langfuse_secret_key="ada",  # pragma: allowlist secret
            langfuse_base_url="",
            app_env="test",
        ),
    )
    monkeypatch.setitem(__import__("sys").modules, "langfuse", SimpleNamespace(Langfuse=_Langfuse))
    reset_client()
    assert client() is None
    assert constructed["n"] == 0
    reset_client()


def test_host_cloud_tidak_membuat_klien(monkeypatch):
    """Negatif: host Langfuse Cloud tidak membangun klien."""
    constructed = {"n": 0}

    class _Langfuse:
        def __init__(self, *args, **kwargs):
            constructed["n"] += 1

    monkeypatch.setattr(
        "app.shared.langfuse_tracing.get_settings",
        lambda: SimpleNamespace(
            langfuse_public_key="ada",
            langfuse_secret_key="ada",  # pragma: allowlist secret
            langfuse_base_url="https://cloud.langfuse.com",
            app_env="test",
        ),
    )
    monkeypatch.setitem(__import__("sys").modules, "langfuse", SimpleNamespace(Langfuse=_Langfuse))
    reset_client()
    assert client() is None
    assert constructed["n"] == 0
    reset_client()
