"""Monitoring error, deploy, dan login ke Sentry.

Yang dipantau di sini sengaja sedikit (alasannya di docs/DEPLOY.md, bagian Monitoring):
- error server yang tidak tertangani (otomatis lewat integrasi FastAPI);
- setiap hasil /auth/callback. Callback selalu membalas 302, juga saat gagal, jadi
  kegagalan login tidak pernah terlihat sebagai 5xx. Tanpa record_login_outcome()
  tidak ada alat apa pun yang tahu bahwa orang tidak bisa login;
- release = commit yang di-deploy, supaya error baru bisa dikaitkan ke deploy-nya.

Panggilan AI dan worker dipantau terpisah (Langfuse), bukan di sini.

Privasi (UU PDP 27/2022, dan isi kasus hukum bersifat rahasia): tidak ada body request,
cookie, header Authorization, variabel lokal, maupun data pribadi yang dikirim. Kode
sekali pakai OIDC (`code`, `state`, dst.) dihapus dari semua URL dan breadcrumb.
"""

import re
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.logging import ignore_logger

from app.shared.config import Settings

# Parameter dan cookie yang tidak boleh keluar dari server, di mana pun muncul.
_SENSITIVE_PARAMS = (
    "code",
    "state",
    "session_state",
    "nonce",
    "code_verifier",
    "id_token",
    "access_token",
    "refresh_token",
    "id_token_hint",
)
_SENSITIVE_PATTERN = re.compile(
    r"\b(" + "|".join(_SENSITIVE_PARAMS) + r"|[a-z_]*session)=([^&\s;\"']+)",
    re.IGNORECASE,
)
FILTERED = "[Filtered]"

# Apa yang boleh dikumpulkan SDK secara otomatis. Setiap kategori ditulis eksplisit: kategori
# yang tidak disebut memakai bawaan SDK, dan bawaannya ikut mengirim body request (isi kasus,
# jawaban AI) serta query string (`code`, `state` tidak ada di denylist bawaan).
# Catatan: begitu opsi ini diisi, EventScrubber bawaan SDK tidak dipakai lagi; penggantinya
# adalah scrub_event() dan scrub_breadcrumb() di bawah.
DATA_COLLECTION: dict[str, Any] = {
    "user_info": False,
    "cookies": {"mode": "off"},
    "http_headers": {"request": {"mode": "denylist"}},
    "http_bodies": [],
    "url_query_params": {"mode": "off"},
    "graphql": {"document": False, "variables": False},
    "gen_ai": {"inputs": False, "outputs": False},
    "database_query_data": False,
    "queues": False,
    "stack_frame_variables": False,
}

# Request yang tidak ditrace: healthcheck Docker tiap 30 detik dan polling tiap 5 sampai
# 60 detik per tab. Isinya selalu sama dan hanya menenggelamkan trace yang berarti.
_UNTRACED_EXACT = frozenset({"/health"})
_UNTRACED_SUFFIXES = ("/progress", "/grading-progress", "/unread-count")
_ALWAYS_TRACED = frozenset({"/auth/callback", "/auth/login"})

# Kelompok kegagalan login. Menentukan siapa yang harus bertindak.
_LOGIN_FAILURE_KIND = {
    # Pertukaran token ke Zitadel gagal: Zitadel mati, salah konfigurasi, atau jam
    # server meleset. Tidak bisa diperbaiki pengguna; tim atau Mas Kholil harus turun.
    "OIDC_EXCHANGE_FAILED": ("system", "error"),
    # State login hilang: tab lama, klik dua kali, atau API restart (deploy) membuang
    # login yang belum selesai (pending.py menyimpannya di memori).
    "INVALID_OIDC_STATE": ("flow", "warning"),
    # Akun belum didaftarkan atau dinonaktifkan: Admin yang perlu bertindak.
    "USER_NOT_REGISTERED": ("account", "info"),
    "USER_DEACTIVATED": ("account", "info"),
}


def scrub_text(value: str) -> str:
    """Ganti nilai parameter sensitif di dalam teks (URL, pesan log) dengan [Filtered]."""
    return _SENSITIVE_PATTERN.sub(lambda match: f"{match.group(1)}={FILTERED}", value)


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def scrub_event(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """before_send: buang bagian request yang bisa membawa data rahasia, lalu saring teksnya.

    Penyaringan teks berjalan atas seluruh event, termasuk breadcrumb dan span, jadi tidak
    perlu hook before_breadcrumb terpisah.
    """
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "query_string", "env"):
            request.pop(key, None)
        if isinstance(request.get("url"), str):
            request["url"] = request["url"].split("?", 1)[0]
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                name: value
                for name, value in headers.items()
                if name.lower() not in {"cookie", "authorization", "set-cookie"}
            }
    return _scrub(event)


def traces_sampler(sampling_context: dict[str, Any], default_rate: float) -> float:
    scope = sampling_context.get("asgi_scope") or {}
    path = str(scope.get("path") or "")
    if path in _ALWAYS_TRACED:
        return 1.0
    if path in _UNTRACED_EXACT or path.endswith(_UNTRACED_SUFFIXES):
        return 0.0
    return default_rate


def init_monitoring(settings: Settings, **sdk_overrides: Any) -> bool:
    """Nyalakan Sentry bila SENTRY_DSN terisi. Mengembalikan True bila menyala.

    sdk_overrides hanya untuk test (transport palsu), supaya test memakai opsi yang sama
    persis dengan produksi, bukan salinannya.
    """
    if not settings.sentry_dsn:
        return False
    # Access log uvicorn memuat URL lengkap, termasuk ?code= dari callback, dan isinya
    # sudah ada di trace. Jangan jadikan breadcrumb.
    ignore_logger("uvicorn.access")
    rate = settings.sentry_traces_sample_rate
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        release=settings.sentry_release or None,
        data_collection=DATA_COLLECTION,
        max_request_body_size="never",
        traces_sampler=lambda context: traces_sampler(context, rate),
        before_send=scrub_event,
        before_send_transaction=scrub_event,
        **sdk_overrides,
    )
    return True


def record_login_outcome(error_code: str | None) -> None:
    """Catat hasil satu /auth/callback. None berarti login berhasil.

    Berhasil: hanya tag di trace callback (ditrace 100%), jadi tingkat keberhasilan login
    bisa dihitung. Gagal: satu event per kejadian, dikelompokkan per kode error, supaya
    lonjakan satu jenis kegagalan memicu alert sendiri. Tanpa email, sub, atau token.
    """
    sentry_sdk.set_tag("login.outcome", error_code or "ok")
    if error_code is None:
        return
    kind, level = _LOGIN_FAILURE_KIND.get(error_code, ("unknown", "error"))
    with sentry_sdk.new_scope() as scope:
        scope.fingerprint = ["login-failed", error_code]
        scope.set_tag("login.error_code", error_code)
        scope.set_tag("login.failure_kind", kind)
        scope.set_level(level)
        sentry_sdk.capture_message(f"Login gagal: {error_code}")
