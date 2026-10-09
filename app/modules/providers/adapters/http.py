"""Satu POST untuk uji koneksi. Tanpa retry."""

import time

import httpx

# Connect dan read 15 detik. write/pool ikut 15 supaya tidak menggantung di default pendek.
TIMEOUT = httpx.Timeout(connect=15.0, read=15.0, write=15.0, pool=15.0)
MESSAGE_LIMIT = 200


def clip(text: str, secret: str) -> str:
    """Buang kredensial, lalu potong. Kredensial harus hilang sebelum dipotong."""
    cleaned = text.replace(secret, "") if secret else text
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return "connection test failed"
    if len(cleaned) > MESSAGE_LIMIT:
        return cleaned[:MESSAGE_LIMIT].rstrip()
    return cleaned


def post_json(
    url: str, *, headers: dict[str, str], body: dict
) -> tuple[httpx.Response | None, str | None, int]:
    """Kembalikan (response, pesan gagal, latency_ms).

    pesan gagal terisi hanya kalau transport gagal. Jangan log header atau body:
    header bisa berisi kredensial.
    """
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        return None, "connection timed out", 0
    except httpx.ConnectError:
        return None, "could not connect", 0
    except httpx.HTTPError:
        return None, "connection failed", 0
    latency_ms = int((time.perf_counter() - started) * 1000)
    return response, None, latency_ms


def json_object(response: httpx.Response) -> dict | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def http_failure(response: httpx.Response, secret: str) -> str:
    detail = response.text or ""
    return clip(f"provider returned HTTP {response.status_code} {detail}", secret)


def invalid_body(response: httpx.Response, secret: str) -> str:
    detail = response.text or ""
    return clip(f"provider response was not a valid success body {detail}", secret)


def classify_error(
    status_code: int | None = None,
    transport_error: str | None = None,
) -> str:
    """Petakan kegagalan HTTP/transport ke kategori yang mudah dipahami Admin.

    SRP: satu-satunya tempat yang memetakan kode status dan pesan transport
    ke kategori. Adapter membaca hasilnya tanpa menduplikasi logika ini.
    """
    if transport_error is not None:
        lower = transport_error.lower()
        if "timed out" in lower:
            return "timeout"
        if "could not connect" in lower:
            return "unreachable"
        return "unknown"
    if status_code in (401, 403):
        return "access_denied"
    if status_code == 404:
        return "model_not_found"
    return "unknown"
