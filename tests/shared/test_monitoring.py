"""Monitoring Sentry: penyaringan data rahasia, sampling, dan hasil login.

Test integrasi memakai transport palsu, jadi tidak ada yang keluar ke sentry.io.
Login berjalan lewat OIDC palsu yang sama dengan test auth lain.
"""

import json
import logging

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from app.modules.auth.seeds import AUTHOR_SUB, DEACTIVATED_SUB, UNKNOWN_SUB
from app.shared.config import Settings
from app.shared.monitoring import (
    FILTERED,
    init_monitoring,
    scrub_event,
    scrub_text,
    traces_sampler,
)
from tests.login import complete_login

FAKE_DSN = "https://public@sentry.invalid/1"


class _CaptureTransport(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)

    def items(self, item_type):
        return [
            item.payload.json
            for envelope in self.envelopes
            for item in envelope.items
            if item.type == item_type
        ]


@pytest.fixture
def sentry_events():
    transport = _CaptureTransport()
    started = init_monitoring(
        Settings(sentry_dsn=FAKE_DSN, app_env="test", sentry_release="server@test"),
        transport=transport,
    )
    assert started
    # Breadcrumb hidup di scope global/isolasi; jangan bawa sisa test sebelumnya.
    sentry_sdk.get_isolation_scope().clear_breadcrumbs()
    sentry_sdk.get_current_scope().clear_breadcrumbs()
    yield transport
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)  # kembali ke klien yang tidak mengirim


def _flush():
    sentry_sdk.get_client().flush()


# --- penyaringan ---------------------------------------------------------------


def test_scrub_text_hides_one_time_oidc_values_and_keeps_the_rest():
    url = "/auth/callback?code=abc123&state=s-9&page=2"
    assert scrub_text(url) == f"/auth/callback?code={FILTERED}&state={FILTERED}&page=2"


def test_scrub_text_hides_the_session_cookie():
    """Edge: cookie sesi muncul sebagai teks `veritask_session=<uuid>` di header atau log,
    bukan sebagai parameter URL. Kalau lolos, siapa pun yang membaca Sentry bisa memakai
    sesi itu sampai kedaluwarsa (12 jam)."""
    assert scrub_text("veritask_session=1b2c; Path=/") == f"veritask_session={FILTERED}; Path=/"


def test_scrub_text_does_not_touch_words_that_only_end_in_code():
    """Edge: `postcode=` atau `error_code=` berakhiran `code` tapi bukan kode OIDC. Kalau
    ikut disaring, kode error login (yang justru ingin kita lihat) hilang dari event."""
    assert scrub_text("error_code=INVALID_OIDC_STATE") == "error_code=INVALID_OIDC_STATE"


def test_scrub_event_drops_body_cookies_query_and_secret_headers():
    event = {
        "request": {
            "url": "https://devapi.example/auth/callback?code=abc&state=xyz",
            "query_string": "code=abc&state=xyz",
            "data": {"content": "isi kasus hukum rahasia"},
            "cookies": {"veritask_session": "1b2c"},
            "headers": {
                "Cookie": "veritask_session=1b2c",
                "Authorization": "Bearer t",
                "Accept": "*/*",
            },
        },
        "breadcrumbs": {"values": [{"message": "GET /auth/callback?code=abc HTTP/1.1"}]},
    }

    scrubbed = scrub_event(event)

    request = scrubbed["request"]
    assert request["url"] == "https://devapi.example/auth/callback"
    assert "query_string" not in request
    assert "data" not in request
    assert "cookies" not in request
    assert request["headers"] == {"Accept": "*/*"}
    assert "abc" not in json.dumps(scrubbed)


def test_logged_error_with_a_callback_url_reaches_sentry_without_the_code(sentry_events):
    """Edge: SDK sudah membuang query string dari data request, tapi tidak menyentuh teks
    pesan log. Setiap logger.error() menjadi event Sentry, jadi log yang menyebut URL
    callback (misalnya saat debugging Zitadel) akan membawa `code` dan `state` keluar.
    Hanya scrub_event() yang menangkap jalur ini."""
    logging.getLogger("app.modules.auth").error(
        "callback gagal: /auth/callback?code=kode-dari-log&state=state-dari-log"
    )
    _flush()

    (event,) = sentry_events.items("event")
    sent = json.dumps(event)
    assert "callback gagal" in sent
    assert "kode-dari-log" not in sent
    assert "state-dari-log" not in sent


def test_access_log_lines_never_become_breadcrumbs(sentry_events, caplog):
    """Edge: uvicorn menulis satu baris access log per request, lengkap dengan query string,
    jadi breadcrumb setiap event berikutnya akan membawa URL callback terakhir. Logger itu
    diabaikan, dan logger lain tetap tersaring."""
    # Di produksi keduanya INFO. Tanpa ini baris INFO tidak pernah dipancarkan dan test
    # lulus tanpa menguji apa-apa.
    caplog.set_level(logging.INFO, logger="uvicorn.access")
    caplog.set_level(logging.INFO, logger="app.modules.auth")
    logging.getLogger("uvicorn.access").info(
        '"GET /auth/callback?code=dari-access-log HTTP/1.1" 302'
    )
    logging.getLogger("app.modules.auth").info("redirect ke /auth/done?state=dari-info-log")
    sentry_sdk.capture_message("pemicu")
    _flush()

    (event,) = sentry_events.items("event")
    crumbs = event["breadcrumbs"]["values"]
    assert all(crumb.get("category") != "uvicorn.access" for crumb in crumbs)
    sent = json.dumps(crumbs)
    assert "dari-access-log" not in sent
    assert "dari-info-log" not in sent
    assert "redirect ke /auth/done" in sent


def test_span_with_a_token_in_its_url_is_scrubbed_in_the_trace(sentry_events):
    """Edge: span HTTP keluar (httpx) diberi nama dari URL-nya. Hari ini tidak ada panggilan
    keluar yang membawa token di query, tapi logout membangun URL Zitadel dengan
    `id_token_hint`, dan modul lain bisa menambah panggilan seperti itu kapan saja. Trace
    dikirim lewat jalur sendiri (before_send_transaction), jadi jalur itu juga harus tersaring."""
    with (
        sentry_sdk.start_transaction(name="uji", sampled=True),
        sentry_sdk.start_span(op="http.client", name="GET https://idp/end?id_token_hint=jwt-asli"),
    ):
        pass
    _flush()

    (transaction,) = sentry_events.items("transaction")
    sent = json.dumps(transaction)
    assert "id_token_hint=" in sent
    assert "jwt-asli" not in sent


# --- sampling -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "rate"),
    [
        ("/health", 0.0),
        ("/measurements/7/progress", 0.0),
        ("/runs/7/grading-progress", 0.0),
        ("/notifications/unread-count", 0.0),
        ("/auth/callback", 1.0),
        ("/auth/login", 1.0),
        ("/cases/12", 0.2),
    ],
)
def test_traces_sampler(path, rate):
    assert traces_sampler({"asgi_scope": {"path": path}}, 0.2) == rate


def test_traces_sampler_without_a_request_uses_the_default_rate():
    """Edge: transaksi di luar request HTTP (misalnya startup) tidak punya asgi_scope.
    Kalau sampler mengira path-nya ada, ia melempar KeyError dan SDK membuang trace itu."""
    assert traces_sampler({}, 0.2) == 0.2


# --- nyala / mati -------------------------------------------------------------


def test_monitoring_stays_off_without_a_dsn():
    assert init_monitoring(Settings(sentry_dsn="")) is False
    assert not sentry_sdk.get_client().is_active()


def test_production_options_never_send_personal_data_or_locals(sentry_events):
    options = sentry_sdk.get_client().options
    collected = options["data_collection"]
    assert collected["provided_by_user"] is True
    assert collected["user_info"] is False
    assert collected["cookies"] == {"mode": "off"}
    assert collected["url_query_params"] == {"mode": "off"}
    assert collected["http_bodies"] == []
    assert collected["stack_frame_variables"] is False
    assert collected["gen_ai"] == {"inputs": False, "outputs": False}
    assert options["max_request_body_size"] == "never"
    assert options["environment"] == "test"
    assert options["release"] == "server@test"


# --- hasil login --------------------------------------------------------------


def test_unregistered_login_becomes_one_info_event_grouped_by_code(
    client, db_session, sentry_events
):
    complete_login(client, db_session, UNKNOWN_SUB)
    _flush()

    events = [event for event in sentry_events.items("event") if "message" in event]
    assert len(events) == 1
    event = events[0]
    assert event["message"] == "Login gagal: USER_NOT_REGISTERED"
    assert event["level"] == "info"
    assert event["fingerprint"] == ["login-failed", "USER_NOT_REGISTERED"]
    assert event["tags"]["login.failure_kind"] == "account"
    assert event["environment"] == "test"


def test_deactivated_login_is_an_account_problem(client, db_session, sentry_events):
    complete_login(client, db_session, DEACTIVATED_SUB)
    _flush()

    (event,) = [event for event in sentry_events.items("event") if "message" in event]
    assert event["tags"]["login.error_code"] == "USER_DEACTIVATED"
    assert event["tags"]["login.failure_kind"] == "account"


def test_lost_login_state_is_a_warning_and_leaks_no_code(client, sentry_events):
    """Edge: state hilang adalah kegagalan yang paling mungkin terjadi setelah deploy,
    karena API yang restart membuang login yang belum selesai (pending.py, di memori). URL
    callback-nya membawa `code` sekali pakai dan ditrace 100%, jadi kalau penyaringan
    terlewat di sini, setiap login gagal mengirim kodenya ke Sentry. Lapisan pertama di
    sini adalah url_query_params "off" (dijaga test opsi di atas); scrub_event() adalah
    lapisan kedua, dijaga test log di atas."""
    client.get(
        "/auth/callback",
        params={"code": "kode-rahasia-sekali-pakai", "state": "state-tidak-dikenal"},
        follow_redirects=False,
    )
    _flush()

    (event,) = [event for event in sentry_events.items("event") if "message" in event]
    assert event["level"] == "warning"
    assert event["tags"]["login.failure_kind"] == "flow"
    everything_sent = json.dumps([e.items for e in sentry_events.envelopes], default=str)
    assert "kode-rahasia-sekali-pakai" not in everything_sent
    assert "state-tidak-dikenal" not in everything_sent


def test_successful_login_sends_no_error_and_tags_the_callback_trace(
    client, db_session, sentry_events
):
    callback = complete_login(client, db_session, AUTHOR_SUB)
    assert callback.status_code == 302
    _flush()

    assert [event for event in sentry_events.items("event") if "message" in event] == []
    callbacks = [
        tx for tx in sentry_events.items("transaction") if tx["transaction"] == "/auth/callback"
    ]
    assert len(callbacks) == 1
    assert callbacks[0]["tags"]["login.outcome"] == "ok"
    # Tidak ada email, sub, atau cookie pengguna di trace.
    sent = json.dumps(callbacks[0])
    assert "author@veritask.test" not in sent
    assert AUTHOR_SUB not in sent
