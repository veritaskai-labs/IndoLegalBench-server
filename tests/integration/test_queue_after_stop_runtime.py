"""Antrean tidak macet setelah jeda atau batal. SCRUM-77 SCRUM-267.

PBI-12 AC7 (Dihentikan dan Dibatalkan melepas slot), AC8 (Lanjutkan saat slot terpakai
kembali ke antrean dengan status Menunggu), PBI-11 AC3 (hanya satu pengukuran berjalan).
Worker nyata, database uji, dan endpoint kontrol HTTP; hanya provider AI yang diganti
provider terkontrol. Berjalan di SQLite dan di PostgreSQL yang sudah dimigrasi.

Label: Positif = alur normal, Negatif = harus ditolak, Edge = batas perilaku.
"""

import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.modules.runs.models import (
    Attempt,
    AttemptStatus,
    CaseSnapshot,
    MeasurementSession,
    SessionStatus,
    SessionType,
)
from app.shared.security import Role
from tests.modules.conftest import as_role, buat_pengguna
from tests.modules.runs.test_execution_runtime import (
    Provider,
    db_session,
    jenis_peraturan,
    runner,
    start,
)
from tests.modules.runs.test_start_endpoint import siap
from tests.modules.runs.test_summary_endpoint import _tambah_produk

__all__ = ["as_role", "buat_pengguna", "db_session", "jenis_peraturan", "siap"]

URL = "/measurements"
TOTAL = 9  # 3 kasus x 3 pengulangan
STOPPED = {"pause": "paused", "cancel": "cancelled"}


def _row(siap, session_id: uuid.UUID) -> MeasurementSession:
    siap.db.expire_all()
    return siap.db.get(MeasurementSession, session_id)


def _running_ids(siap) -> list[uuid.UUID]:
    siap.db.expire_all()
    return list(
        siap.db.scalars(
            select(MeasurementSession.id).where(
                MeasurementSession.status == SessionStatus.RUNNING,
                MeasurementSession.session_type == SessionType.AUTOMATIC,
            )
        )
    )


def _snapshots(siap, session_id: uuid.UUID) -> int:
    return siap.db.scalar(
        select(func.count()).select_from(CaseSnapshot).where(CaseSnapshot.session_id == session_id)
    )


def _attempts(siap, session_id: uuid.UUID, status: AttemptStatus) -> int:
    return siap.db.scalar(
        select(func.count())
        .select_from(Attempt)
        .where(Attempt.session_id == session_id, Attempt.status == status)
    )


def _press_during_next_call(siap, provider, action: str) -> None:
    """Tekan Jeda/Batalkan lewat HTTP pada sesi yang sedang dipanggil provider, sekali saja."""

    def press():
        provider.on_call = None
        (running,) = _running_ids(siap)
        response = siap.sebagai(Role.ADMIN).post(f"{URL}/{running}/{action}")
        assert response.status_code == 202

    provider.on_call = press


def _resume(siap, session_id: uuid.UUID):
    return siap.sebagai(Role.ADMIN).post(f"{URL}/{session_id}/resume")


def _other_product(siap):
    return _tambah_produk(
        siap.db,
        name="Produk Lain",
        price_input_usd_per_mtok=Decimal("10"),
        price_output_usd_per_mtok=Decimal("10"),
    )


@pytest.fixture(params=["same", "different"])
def three_sessions(request, siap):
    """Tiga sesi Menunggu; sesi 3 memakai produk yang sama atau produk lain."""
    first, second = start(siap, label="Sesi 1"), start(siap, label="Sesi 2")
    if request.param == "different":
        third = start(siap, label="Sesi 3", product_id=str(_other_product(siap).id))
    else:
        third = start(siap, label="Sesi 3")
    return first, second, third


@pytest.mark.parametrize(
    "first_action,second_action",
    [("pause", "pause"), ("cancel", "cancel"), ("pause", "cancel"), ("cancel", "pause")],
)
def test_session_three_runs_after_sessions_one_and_two_stop(
    siap, three_sessions, first_action, second_action
):
    """Positif: AC7, jeda/batal sesi 1 dan 2 melepas slot sehingga sesi 3 selesai berjalan."""
    # Arrange
    first, second, third = three_sessions
    provider = Provider()
    run = runner(siap, provider)

    # Act
    _press_during_next_call(siap, provider, first_action)
    assert run()
    _press_during_next_call(siap, provider, second_action)
    assert run()
    assert run()

    # Assert
    assert _row(siap, first).status == STOPPED[first_action]
    assert _row(siap, second).status == STOPPED[second_action]
    assert _row(siap, third).status == "finished"
    assert len(provider.calls) == 1 + 1 + TOTAL
    assert _running_ids(siap) == []


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_resume_while_another_session_runs_waits_in_queue(siap, three_sessions, action):
    """Positif: AC8, Lanjutkan saat slot terpakai = Menunggu, lalu jalan pada gilirannya."""
    # Arrange
    first, second, third = three_sessions
    provider = Provider()
    run = runner(siap, provider)
    _press_during_next_call(siap, provider, action)
    assert run()  # sesi 1 berhenti setelah panggilan pertama
    running_seen: list[list[uuid.UUID]] = []
    resumed = []

    def resume_first_while_second_runs():
        # Galat di sini akan ditelan worker sebagai galat provider; simpan, cek di luar.
        provider.on_call = lambda: running_seen.append(_running_ids(siap))
        resumed.append((_resume(siap, first), _row(siap, first).status))

    provider.on_call = resume_first_while_second_runs

    # Act
    assert run()  # sesi 2 berjalan sampai selesai, sesi 1 menunggu
    response, status_while_waiting = resumed[0]
    assert response.status_code == 202, response.json()
    assert run()  # sesi 3 lebih dulu mengantre daripada sesi 1 yang dilanjutkan
    third_done = _row(siap, third).status
    assert run()  # baru sesi 1 dilanjutkan

    # Assert
    assert response.json()["status"] == "queued" and status_while_waiting == "queued"
    assert _row(siap, second).status == "finished"
    assert third_done == "finished"
    assert _row(siap, first).status == "finished"
    assert len(provider.calls) == 1 + TOTAL + TOTAL + (TOTAL - 1)
    assert all(len(ids) == 1 for ids in running_seen)  # PBI-11 AC3
    assert _snapshots(siap, first) == 1  # snapshot dan salinan produk tidak dibuat ulang
    assert _attempts(siap, first, AttemptStatus.SUCCEEDED) == TOTAL


def test_resume_goes_behind_waiting_sessions_even_when_slot_is_free(siap):
    """Edge: slot kosong tetapi ada sesi Menunggu; yang dilanjutkan masuk belakang antrean."""
    # Arrange
    first, second = start(siap, label="Sesi 1"), start(siap, label="Sesi 2")
    provider = Provider()
    run = runner(siap, provider)
    _press_during_next_call(siap, provider, "pause")
    assert run()
    assert _running_ids(siap) == []  # slot kosong, sesi 2 belum diambil worker

    # Act
    response = _resume(siap, first)
    assert run()
    second_done, first_waiting = _row(siap, second).status, _row(siap, first).status
    assert run()

    # Assert
    assert response.status_code == 202 and response.json()["status"] == "queued"
    assert second_done == "finished" and first_waiting == "queued"
    assert _row(siap, first).status == "finished"
    assert _row(siap, first).queued_at > _row(siap, second).queued_at


def test_resume_with_free_slot_and_empty_queue_runs_at_once(siap):
    """Positif: tidak ada yang berjalan atau menunggu, jadi Lanjutkan langsung Berjalan."""
    # Arrange
    only = start(siap)
    provider = Provider()
    run = runner(siap, provider)
    _press_during_next_call(siap, provider, "pause")
    assert run()

    # Act
    response = _resume(siap, only)

    # Assert
    assert response.status_code == 202 and response.json()["status"] == "running"
    assert run()
    assert _row(siap, only).status == "finished"
    assert len(provider.calls) == TOTAL


def test_waiting_resumed_session_can_be_cancelled_and_resumed_again(siap):
    """Edge: sesi yang menunggu setelah Lanjutkan bisa dibatalkan, lalu dilanjutkan lagi."""
    # Arrange
    first, second = start(siap, label="Sesi 1"), start(siap, label="Sesi 2")
    provider = Provider()
    run = runner(siap, provider)
    _press_during_next_call(siap, provider, "pause")
    assert run()
    assert _resume(siap, first).json()["status"] == "queued"

    # Act
    cancelled = siap.sebagai(Role.ADMIN).post(f"{URL}/{first}/cancel")
    assert run()  # sesi 2 berjalan sampai selesai
    resumed = _resume(siap, first)
    assert run()

    # Assert
    assert cancelled.status_code == 202 and cancelled.json()["status"] == "cancelled"
    assert resumed.status_code == 202 and resumed.json()["status"] == "running"
    assert _row(siap, second).status == "finished"
    assert _row(siap, first).status == "finished"
    assert _snapshots(siap, first) == 1


def test_waiting_resumed_session_cannot_be_resumed_or_paused_again(siap):
    """Negatif: sesi yang sudah Menunggu tidak bisa dilanjutkan atau dijeda lagi."""
    # Arrange
    first, _ = start(siap, label="Sesi 1"), start(siap, label="Sesi 2")
    provider = Provider()
    run = runner(siap, provider)
    _press_during_next_call(siap, provider, "pause")
    assert run()
    assert _resume(siap, first).json()["status"] == "queued"
    queued_at: datetime = _row(siap, first).queued_at

    # Act
    again = _resume(siap, first)
    pause = siap.sebagai(Role.ADMIN).post(f"{URL}/{first}/pause")

    # Assert
    assert again.status_code == 409 and again.json()["code"] == "SESSION_NOT_RESUMABLE"
    assert pause.status_code == 409 and pause.json()["code"] == "INVALID_TRANSITION"
    assert _row(siap, first).queued_at == queued_at


def test_model_version_guard_still_rejects_while_slot_is_taken(siap):
    """Negatif: guard versi model SCRUM-216 tetap berlaku sebelum sesi masuk antrean."""
    # Arrange
    first, second = start(siap, label="Sesi 1"), start(siap, label="Sesi 2")
    provider = Provider()
    run = runner(siap, provider)
    _press_during_next_call(siap, provider, "pause")
    assert run()
    original = provider.ask
    outcome = []

    def changed_version(*args, **kwargs):
        response = original(*args, **kwargs)
        response.model_version = "model-v2"
        # Panggilan ke-3: jawaban sesi 2 dengan versi baru sudah tersimpan sebagai bukti.
        if len(provider.calls) == 3:
            outcome.append(_resume(siap, first))
        return response

    provider.ask = changed_version

    # Act
    assert run()

    # Assert
    assert outcome[0].status_code == 409
    assert outcome[0].json()["code"] == "MODEL_VERSION_CHANGED"
    assert _row(siap, first).status == "paused"
    assert _row(siap, second).status == "finished"


# K4 (UAT): session 3 started only after a few minutes. Part of that wait was the rate-limit
# spacing sleep, which ran before the worker checked whether the session had been stopped.
SPACING_LIMIT = 1  # 1 call per minute = 60 s between calls


class _FakeTime:
    """Fake clock and sleep, so a 60 s spacing wait takes no real time."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _spaced_runner(siap, provider, time: _FakeTime, stopping=lambda: False):
    from app.modules.runs.exchange_rate import ConfiguredExchangeRate
    from app.modules.runs.execution import run_once

    siap.produk.price_input_usd_per_mtok = Decimal("10")
    siap.produk.price_output_usd_per_mtok = Decimal("10")
    siap.produk.rate_limit_per_minute = SPACING_LIMIT
    siap.db.commit()
    return lambda: run_once(
        siap.db,
        adapter_factory=lambda *_: provider,
        rates=ConfiguredExchangeRate(Decimal("16000")),
        sleep=time.sleep,
        clock=time.clock,
        stopping=stopping,
    )


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_stop_is_noticed_without_waiting_for_the_rate_limit_spacing(siap, action):
    """Positif: K4, Jeda/Batalkan terlihat dalam <= 1 detik, sesi Menunggu berikutnya langsung jalan."""
    # Arrange
    first, second = start(siap, label="Sesi 1"), start(siap, label="Sesi 2")
    provider, time = Provider(), _FakeTime()
    run = _spaced_runner(siap, provider, time)
    _press_during_next_call(siap, provider, action)

    # Act
    assert run()
    waited_after_stop = sum(time.sleeps)
    assert run()

    # Assert
    assert waited_after_stop <= 1.0
    assert _row(siap, first).status == STOPPED[action]
    assert _row(siap, second).status == "finished"


def test_spacing_is_still_respected_without_a_stop(siap):
    """Negatif: tanpa Jeda, jarak 60 detik antar panggilan tetap dipatuhi (rate limit produk)."""
    # Arrange
    only = start(siap)
    provider, time = Provider(), _FakeTime()
    run = _spaced_runner(siap, provider, time)

    # Act
    assert run()

    # Assert
    assert len(provider.calls) == TOTAL
    assert sum(time.sleeps) == 60.0 * (TOTAL - 1)
    assert max(time.sleeps) <= 1.0  # slept in short slices so a stop is seen quickly
    assert _row(siap, only).status == "finished"


def test_shutdown_during_spacing_wait_pauses_with_restart_reason(siap):
    """Edge: worker diminta berhenti (SIGTERM) saat menunggu jarak rate limit."""
    # Arrange
    only = start(siap)
    provider, time = Provider(), _FakeTime()
    shutdown = []
    provider.on_call = lambda: shutdown.append(True)
    run = _spaced_runner(siap, provider, time, stopping=lambda: bool(shutdown))

    # Act
    assert run()

    # Assert
    assert len(provider.calls) == 1
    assert sum(time.sleeps) <= 1.0
    row = _row(siap, only)
    assert row.status == "paused" and row.stop_reason == "restart"
