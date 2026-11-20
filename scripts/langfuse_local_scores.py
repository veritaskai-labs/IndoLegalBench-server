"""Buat dasbor Pengukuran eksekusi di Langfuse lokal.

Rute dicek terhadap definisi Fern di langfuse/langfuse main:

- POST /api/public/score-configs
- POST /api/public/unstable/dashboard-widgets
- POST /api/public/unstable/dashboards

Ubin biaya membaca totalCost, yaitu USD yang sudah tersimpan di generasi.
Jangan arahkan skrip ini ke host *.langfuse.com. Kunci hanya dari environment.

    LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=... \\
        LANGFUSE_BASE_URL=http://127.0.0.1:3000 \\
        python scripts/langfuse_local_scores.py
"""

import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

DASHBOARD_NAME = "Pengukuran eksekusi"
FAILURE_LABELS = (
    "ok",
    "truncated",
    "provider_error",
    "key_rejected",
    "model_not_found",
    "no_response",
    "empty",
    "blocked",
    "refused",
)


def cloud_host(base_url: str) -> bool:
    """True bila host adalah Langfuse Cloud."""
    host = (urlparse(base_url.strip()).hostname or "").lower().rstrip(".")
    return host == "langfuse.com" or host.endswith(".langfuse.com")


def _name_filter(name: str) -> dict:
    return {"column": "name", "operator": "=", "type": "string", "value": name}


def widgets() -> list[dict]:
    """Lima ubin: tingkat berhasil, campuran kegagalan, p95, volume, dan biaya USD."""
    return [
        {
            "name": "Tingkat berhasil",
            "description": "Rata-rata skor attempt_ok. 1 berarti panggilan berhasil.",
            "view": "scores-boolean",
            "dimensions": [],
            "metrics": [{"measure": "value", "agg": "avg"}],
            "filters": [_name_filter("attempt_ok")],
            "chartType": "NUMBER",
        },
        {
            "name": "Campuran kegagalan",
            "description": "Jumlah skor failure_kind per label.",
            "view": "scores-categorical",
            "dimensions": [{"field": "stringValue"}],
            "metrics": [{"measure": "count", "agg": "count"}],
            "filters": [_name_filter("failure_kind")],
            "chartType": "PIE",
        },
        {
            "name": "Latensi p95",
            "description": "p95 latensi generasi ask-provider, dalam milidetik.",
            "view": "observations",
            "dimensions": [],
            "metrics": [{"measure": "latency", "agg": "p95"}],
            "filters": [_name_filter("ask-provider")],
            "chartType": "NUMBER",
        },
        {
            "name": "Volume percobaan",
            "description": "Jumlah span score-case.",
            "view": "observations",
            "dimensions": [],
            "metrics": [{"measure": "count", "agg": "count"}],
            "filters": [_name_filter("score-case")],
            "chartType": "BAR_TIME_SERIES",
        },
        {
            "name": "Biaya USD",
            "description": "Jumlah USD yang sudah tersimpan di generasi, dari harga penyedia.",
            "view": "observations",
            "dimensions": [],
            "metrics": [{"measure": "totalCost", "agg": "sum"}],
            "filters": [_name_filter("ask-provider")],
            "chartType": "NUMBER",
        },
    ]


class LocalApi:
    def __init__(self, base_url: str, public_key: str, secret_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        token = _basic(public_key, secret_key)
        self._headers = {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def get(self, path: str) -> dict:
        return self._send("GET", path, None)

    def post(self, path: str, body: dict) -> dict:
        return self._send("POST", path, body)

    def _send(self, method: str, path: str, body: dict | None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=self._headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read().decode()
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise SystemExit(f"{method} {path} -> {error.code} {detail}") from None
        if not raw:
            return {}
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise SystemExit(f"{method} {path} did not return an object")
        return parsed


def _basic(public_key: str, secret_key: str) -> str:
    import base64

    return base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()


def _by_name(rows: list[dict]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for row in rows:
        name = row.get("name")
        if isinstance(name, str):
            found[name] = row
    return found


def _rows(payload: dict) -> list[dict]:
    data = payload.get("data", [])
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def ensure_scores(api: LocalApi) -> None:
    existing = _by_name(_rows(api.get("/api/public/score-configs?page=1&limit=100")))
    if "attempt_ok" not in existing:
        api.post(
            "/api/public/score-configs",
            {
                "name": "attempt_ok",
                "dataType": "BOOLEAN",
                "description": "1 bila panggilan tidak punya failure_kind.",
            },
        )
    if "failure_kind" not in existing:
        api.post(
            "/api/public/score-configs",
            {
                "name": "failure_kind",
                "dataType": "CATEGORICAL",
                "description": "Jenis kegagalan teknis dari adapter. Bukan nilai hukum.",
                "categories": [
                    {"label": label, "value": index} for index, label in enumerate(FAILURE_LABELS)
                ],
            },
        )


def ensure_widgets(api: LocalApi) -> dict[str, str]:
    existing = _by_name(_rows(api.get("/api/public/unstable/dashboard-widgets?page=1&limit=100")))
    ids: dict[str, str] = {}
    for body in widgets():
        row = existing.get(body["name"])
        if row is None:
            row = api.post("/api/public/unstable/dashboard-widgets", body)
        widget_id = row.get("id")
        if not isinstance(widget_id, str) or not widget_id:
            raise SystemExit(f"widget {body['name']} has no id")
        ids[body["name"]] = widget_id
    return ids


def ensure_dashboard(api: LocalApi, widget_ids: dict[str, str]) -> dict:
    existing = _by_name(_rows(api.get("/api/public/unstable/dashboards?page=1&limit=100")))
    if DASHBOARD_NAME in existing:
        return existing[DASHBOARD_NAME]
    placements = []
    layout = (
        ("Tingkat berhasil", 0, 0, 6, 4),
        ("Campuran kegagalan", 6, 0, 6, 4),
        ("Latensi p95", 0, 4, 4, 4),
        ("Volume percobaan", 4, 4, 4, 4),
        ("Biaya USD", 8, 4, 4, 4),
    )
    for name, x, y, width, height in layout:
        placements.append(
            {
                "type": "widget",
                "id": name.lower().replace(" ", "-"),
                "widgetId": widget_ids[name],
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            }
        )
    return api.post(
        "/api/public/unstable/dashboards",
        {
            "name": DASHBOARD_NAME,
            "description": "Tingkat berhasil, campuran kegagalan, latensi, volume, dan biaya USD.",
            "definition": {"widgets": placements},
        },
    )


def project_id(api: LocalApi) -> str | None:
    rows = _rows(api.get("/api/public/projects"))
    if not rows:
        return None
    found = rows[0].get("id")
    return found if isinstance(found, str) else None


def main() -> int:
    base = os.environ.get("LANGFUSE_BASE_URL", "").strip()
    public = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    if not base or cloud_host(base) or not public or not secret:
        print(
            "Set LANGFUSE_BASE_URL to a local Langfuse. Empty and *.langfuse.com are refused.",
            file=sys.stderr,
        )
        return 2
    api = LocalApi(base, public, secret)
    ensure_scores(api)
    widget_ids = ensure_widgets(api)
    dashboard = ensure_dashboard(api, widget_ids)
    project = project_id(api)
    dashboard_id = dashboard.get("id", "")
    if project and dashboard_id:
        print(f"{api.base_url}/project/{project}/dashboards/{dashboard_id}")
    else:
        print(f"{api.base_url}/dashboards/{dashboard_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
