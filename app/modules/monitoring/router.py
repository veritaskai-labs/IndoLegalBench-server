"""GET /ai-monitoring. Admin dan Developer AI. Tidak menghubungi Langfuse Cloud."""

from collections.abc import Callable

import httpx
from fastapi import APIRouter, Depends

from app.modules.monitoring.schemas import MonitoringLink
from app.shared.config import Settings, get_settings
from app.shared.langfuse_tracing import cloud_host
from app.shared.security import Role, require_roles

router = APIRouter(tags=["monitoring"])

_ALLOWED = require_roles(Role.ADMIN, Role.DEVELOPER_AI)
_HEALTH_PATH = "/api/public/health"
_PROBE_TIMEOUT_SECONDS = 2.0


def probe_health(url: str) -> bool:
    """True hanya bila GET {url}/api/public/health menjawab 200. Kegagalan tidak naik."""
    try:
        response = httpx.get(f"{url}{_HEALTH_PATH}", timeout=_PROBE_TIMEOUT_SECONDS)
    except Exception:
        return False
    return response.status_code == 200


def get_probe() -> Callable[[str], bool]:
    return probe_health


def local_base_url(settings: Settings) -> str | None:
    """URL yang dikonfigurasi, atau None bila kosong atau host-nya Langfuse Cloud."""
    base_url = settings.langfuse_base_url.strip()
    if not base_url or cloud_host(base_url):
        return None
    return base_url.rstrip("/")


@router.get(
    "/ai-monitoring",
    response_model=MonitoringLink,
    summary="Tautan antarmuka Langfuse lokal",
    dependencies=[Depends(_ALLOWED)],
)
def read_ai_monitoring(
    settings: Settings = Depends(get_settings),
    probe: Callable[[str], bool] = Depends(get_probe),
) -> MonitoringLink:
    url = local_base_url(settings)
    if url is None:
        return MonitoringLink(url=None, reachable=False)
    return MonitoringLink(url=url, reachable=bool(probe(url)))
