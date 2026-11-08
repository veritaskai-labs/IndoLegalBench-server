"""SCRUM-249: runtime memakai queue/store/database asli, provider terisolasi."""

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.modules.notifications.models import Notification
from app.modules.providers.adapters.base import FailureKind, ProviderResponse
from app.modules.runs import dependencies
from app.modules.runs.exchange_rate import ConfiguredExchangeRate
from app.modules.runs.models import (
    Attempt,
    AttemptStatus,
    CallLog,
    CaseSnapshotItem,
    MeasurementSession,
    SessionProductCopy,
    SessionStatus,
)
from app.shared.security import Role


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def db_session(request, db_session):
    """Same HTTP/runtime assertions against SQLite and migrated PostgreSQL triggers."""
    if request.param == "sqlite":
        yield db_session
        return
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
            from app.modules.auth.models import User

            for suffix, role in (
                ("a000", Role.CONTRIBUTOR),
                ("b000", Role.CONTRIBUTOR),
                ("c000", Role.ADMIN),
            ):
                uid = uuid.UUID("00000000-0000-0000-0000-00000000" + suffix)
                db.add(
                    User(
                        id=uid,
                        email=f"runtime-{suffix}@veritask.test",
                        name="Runtime QA",
                        role=role,
                    )
                )
            # Some PG test files commit queued sessions. Hide them inside this rolled-back
            # transaction so the queue and resume rules only see this test's sessions (SCRUM-267).
            db.execute(
                text(
                    "UPDATE measurement_sessions SET archived_at = now(), archived_by = created_by "
                    "WHERE status = 'queued' AND archived_at IS NULL"
                )
            )
            db.commit()
            yield db
        transaction.rollback()
    engine.dispose()


@pytest.fixture
def jenis_peraturan(db_session):
    from app.modules.legal_refs import seed
    from app.modules.legal_refs.models import RegulationType

    if db_session.scalar(select(RegulationType.code).limit(1)) is None:
        db_session.add_all(
            RegulationType(
                code=item.code,
                label=item.label,
                singkatan=item.singkatan,
                urutan=item.urutan,
                is_active=item.is_active,
            )
            for item in seed.load_regulation_types()
        )
        db_session.commit()


class Provider:
    def __init__(self, behavior="ok", on_call=None):
        self.behavior = behavior
        self.calls = []
        self.on_call = on_call

    def ask(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.on_call:
            self.on_call()
        failed = self.behavior == "error" or (self.behavior == "mixed" and len(self.calls) == 1)
        return ProviderResponse(
            text="partial" if failed else f"answer:{prompt}",
            model_version="model-v1",
            latency_ms=5,
            raw={"ok": not failed},
            error="provider error" if failed else None,
            failure_kind=FailureKind.PROVIDER_ERROR if failed else None,
            input_tokens=10,
            output_tokens=20,
        )


def start(siap, **changes):
    body = {
        "suite_ids": [siap.suite_id],
        "product_id": str(siap.produk.id),
        "repeat_count": 3,
        "case_filter": "both",
        "label": "Runtime QA",
        "purpose": "trial",
        "judge_provider_id": str(siap.penilai.id),
    }
    body.update(changes)
    response = siap.sebagai(Role.ADMIN).post("/measurements", json=body)
    assert response.status_code == 201
    return uuid.UUID(response.json()["session_id"])


def runner(siap, provider):
    from app.modules.runs.execution import run_once

    siap.produk.price_input_usd_per_mtok = Decimal("10")
    siap.produk.price_output_usd_per_mtok = Decimal("10")
    siap.db.commit()
    return lambda: run_once(
        siap.db,
        adapter_factory=lambda *_: provider,
        rates=ConfiguredExchangeRate(Decimal("16000")),
        sleep=lambda _: None,
        clock=lambda: 999999999.0,
        rate_limit=False,
    )


@pytest.mark.parametrize(
    "behavior,status",
    [("ok", "finished"), ("mixed", "finished_with_failures"), ("error", "finished_with_failures")],
)
def test_start_to_answers_logs_and_one_starter_notification(siap, behavior, status):
    session_id = start(siap)
    provider = Provider(behavior)
    run = runner(siap, provider)
    assert run() is True
    siap.db.expire_all()
    assert siap.db.get(MeasurementSession, session_id).status == status
    attempts = siap.db.scalars(select(Attempt).where(Attempt.session_id == session_id)).all()
    logs = siap.db.scalars(select(CallLog)).all()
    assert len(attempts) == len(logs) == len(provider.calls) == 9
    assert {row.attempt_id for row in logs} == {row.id for row in attempts}
    for attempt in attempts:
        snapshot = siap.db.get(CaseSnapshotItem, attempt.snapshot_item_id)
        if attempt.status == AttemptStatus.SUCCEEDED:
            assert attempt.answer_text == "answer:" + snapshot.body["sections"]["identity.question"]
        else:
            assert (
                attempt.answer_text is None
            )  # partial answer remains in call evidence, not immutable success
    notifications = siap.db.scalars(
        select(Notification).where(Notification.link == f"/runs/{session_id}")
    ).all()
    assert len(notifications) == 1
    assert notifications[0].user_id == siap.db.get(MeasurementSession, session_id).created_by
    assert run() is False
    assert (
        siap.db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.link == f"/runs/{session_id}")
        )
        == 1
    )


def test_fifo_second_session_waits_until_first_finishes(siap):
    first, second = start(siap), start(siap)
    run = runner(siap, Provider())
    assert run()
    siap.db.expire_all()
    assert siap.db.get(MeasurementSession, first).status == "finished"
    assert siap.db.get(MeasurementSession, second).status == "queued"
    assert run()
    siap.db.expire_all()
    assert siap.db.get(MeasurementSession, second).status == "finished"


def test_execution_uses_frozen_parameters_and_stops_on_budget(siap):
    session_id = start(siap)
    run = runner(siap, Provider())
    siap.produk.monthly_budget_idr = Decimal("0.01")
    siap.produk.model_params = {"temperature": 0.3}
    siap.db.commit()
    dependencies.build_session_queue(siap.db).take_next()
    siap.produk.model_params = {"temperature": 0.9}
    siap.db.commit()
    provider = Provider()
    run = runner(siap, provider)
    assert run()
    siap.db.expire_all()
    row = siap.db.get(MeasurementSession, session_id)
    assert row.status == "paused" and row.stop_reason == "budget"
    assert len(provider.calls) == 1
    assert provider.calls[0][1]["parameters"] == {"temperature": 0.3}
    assert (
        siap.db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.link == f"/runs/{session_id}")
        )
        == 0
    )


def test_persistence_error_does_not_automatically_repeat_paid_call(siap, monkeypatch, caplog):
    from app.modules.runs.execution_store import TableSessionStore

    session_id = start(siap)
    provider = Provider()
    run = runner(siap, provider)

    caplog.set_level("ERROR")

    def broken(*_):
        raise RuntimeError("secret-token https://private.example")

    monkeypatch.setattr(TableSessionStore, "save_attempt", broken)
    with pytest.raises(RuntimeError):
        run()
    siap.db.rollback()
    assert len(provider.calls) == 1
    # An uncertain running attempt requires recovery, never a blind paid re-send.
    with pytest.raises(RuntimeError, match="recovery"):
        run()
    assert len(provider.calls) == 1
    assert siap.db.get(MeasurementSession, session_id).status == "running"
    failures = [record for record in caplog.records if str(session_id) in record.getMessage()]
    assert len(failures) == 2
    assert all(record.levelname == "ERROR" for record in failures)
    assert "RuntimeError" in caplog.text
    assert "secret-token" not in caplog.text and "private.example" not in caplog.text


def test_control_change_after_call_prevents_next_dispatch(siap):
    session_id = start(siap)

    def pause():
        from datetime import UTC, datetime

        from app.modules.runs.repository import SessionStatusRepository

        SessionStatusRepository(siap.db).change_status(
            session_id, SessionStatus.PAUSED, at=datetime.now(UTC), stop_reason="user"
        )

    provider = Provider(on_call=pause)
    assert runner(siap, provider)()
    assert len(provider.calls) == 1
    siap.db.expire_all()
    assert siap.db.get(MeasurementSession, session_id).status == "paused"
    assert siap.db.scalar(select(func.count()).select_from(CallLog)) == 1


def test_resume_skips_successes_and_preserves_first_model_version(siap):
    from datetime import UTC, datetime

    from app.modules.runs.repository import SessionStatusRepository

    session_id = start(siap)
    provider = Provider(
        on_call=lambda: SessionStatusRepository(siap.db).change_status(
            session_id, SessionStatus.PAUSED, at=datetime.now(UTC), stop_reason="user"
        )
    )
    assert runner(siap, provider)()
    assert len(provider.calls) == 1
    SessionStatusRepository(siap.db).change_status(
        session_id, SessionStatus.RUNNING, at=datetime.now(UTC)
    )
    provider.on_call = None
    assert runner(siap, provider)()
    assert len(provider.calls) == 9
    assert (
        siap.db.scalar(
            select(SessionProductCopy.model_version_at_start).where(
                SessionProductCopy.session_id == session_id
            )
        )
        == "model-v1"
    )


def test_notifier_failure_rolls_back_final_status_without_repeating_calls(siap, monkeypatch):
    from app.modules.notifications.service import TransactionNotifier

    session_id = start(siap)
    provider = Provider()
    run = runner(siap, provider)
    original = TransactionNotifier.notify

    def broken(*_):
        raise RuntimeError("notification write failed")

    monkeypatch.setattr(TransactionNotifier, "notify", broken)
    with pytest.raises(RuntimeError):
        run()
    siap.db.expire_all()
    assert siap.db.get(MeasurementSession, session_id).status == "running"
    assert len(provider.calls) == 9
    monkeypatch.setattr(TransactionNotifier, "notify", original)
    assert run()
    assert len(provider.calls) == 9
    assert siap.db.get(MeasurementSession, session_id).status == "finished"


def test_shutdown_pauses_after_saving_active_call(siap):
    from app.modules.runs.execution import run_once

    stop = []
    session_id = start(siap)
    provider = Provider(on_call=lambda: stop.append(True))
    runner(siap, provider)  # configure actual product prices before queue freezes it
    assert run_once(
        siap.db,
        adapter_factory=lambda *_: provider,
        rates=ConfiguredExchangeRate(Decimal("16000")),
        rate_limit=False,
        stopping=lambda: bool(stop),
    )
    siap.db.expire_all()
    row = siap.db.get(MeasurementSession, session_id)
    assert row.status == "paused" and row.stop_reason == "restart"
    assert len(provider.calls) == 1
    assert siap.db.scalar(select(func.count()).select_from(CallLog)) == 1


def test_missing_exchange_rate_is_rejected_before_paid_call(siap):
    from app.modules.runs.execution import run_once

    session_id = start(siap)
    provider = Provider()
    runner(siap, provider)
    assert run_once(
        siap.db,
        adapter_factory=lambda *_: provider,
        rates=ConfiguredExchangeRate(None),
        rate_limit=False,
    )
    assert provider.calls == []
    assert siap.db.get(MeasurementSession, session_id).status == "failed"


def test_missing_price_is_rejected_before_paid_call(siap):
    from app.modules.runs.execution import run_once

    session_id = start(siap)
    provider = Provider()
    assert run_once(
        siap.db,
        adapter_factory=lambda *_: provider,
        rates=ConfiguredExchangeRate(Decimal("16000")),
        rate_limit=False,
    )
    assert provider.calls == []
    assert siap.db.get(MeasurementSession, session_id).status == "failed"


def test_second_postgres_dispatcher_cannot_take_active_lease(siap):
    from app.modules.runs.execution import worker_lease

    if siap.db.get_bind().dialect.name != "postgresql":
        pytest.skip("PostgreSQL lease assertion")
    session_id = start(siap)
    provider = Provider()
    run = runner(siap, provider)
    with worker_lease(siap.db.get_bind()) as acquired:
        assert acquired
        assert run() is False
        import threading
        from contextlib import nullcontext

        from app.measurement_worker import wait_for_recovery

        wait_for_recovery(threading.Event(), factory=lambda: nullcontext(siap.db))
    assert provider.calls == []
    assert siap.db.get(MeasurementSession, session_id).status == "queued"
    assert run()


def test_ten_distinct_frozen_questions_map_to_thirty_attempts(siap, monkeypatch):
    import copy
    from dataclasses import replace

    from app.modules.runs.adapters import SnapshotSourceAdapter
    from tests.modules.cases.test_cases import _buat, _setujui

    client = siap.sebagai(Role.CONTRIBUTOR)
    for number in range(4, 11):
        response = _buat(client, siap.suite_id, case_code=f"QA-{number:03}", split_tag="dev")
        assert response.status_code == 201
        _setujui(siap.db, response.json()["id"])
    original = SnapshotSourceAdapter.approved_items

    def marked(source, suite_ids):
        result = []
        for item in reversed(original(source, suite_ids)):
            body = copy.deepcopy(item.content)
            body["sections"]["identity.question"] = f"QA marker {item.case_id}"
            result.append(replace(item, content=body))
        return result

    monkeypatch.setattr(SnapshotSourceAdapter, "approved_items", marked)
    session_id = start(siap)
    provider = Provider()
    assert runner(siap, provider)()
    attempts = siap.db.scalars(select(Attempt).where(Attempt.session_id == session_id)).all()
    assert len(provider.calls) == len(attempts) == 30
    assert len({prompt for prompt, _ in provider.calls}) == 10
    for row in attempts:
        item = siap.db.get(CaseSnapshotItem, row.snapshot_item_id)
        assert row.answer_text == f"answer:QA marker {item.case_id}"


def test_observed_model_version_and_other_copy_fields_stay_immutable(siap):
    from sqlalchemy import update
    from sqlalchemy.exc import DBAPIError

    if siap.db.get_bind().dialect.name != "postgresql":
        pytest.skip("PostgreSQL immutability trigger")
    session_id = start(siap)
    run = runner(siap, Provider())
    dependencies.build_session_queue(siap.db).take_next()
    with pytest.raises(DBAPIError) as error, siap.db.begin_nested():
        siap.db.execute(
            update(SessionProductCopy)
            .where(SessionProductCopy.session_id == session_id)
            .values(model_version_at_start="forged")
        )
    assert error.value.orig.sqlstate == "42501"  # no observed attempt backs this version
    assert run()
    for changes in ({"model_version_at_start": "changed"}, {"name": "changed"}):
        with pytest.raises(DBAPIError) as error, siap.db.begin_nested():
            siap.db.execute(
                update(SessionProductCopy)
                .where(SessionProductCopy.session_id == session_id)
                .values(**changes)
            )
        assert error.value.orig.sqlstate == "42501"


def test_process_boot_waits_for_recovery_instead_of_auto_resuming(siap, caplog):
    import threading
    from contextlib import nullcontext
    from unittest.mock import Mock

    from app.measurement_worker import wait_for_recovery

    session_id = start(siap)
    dependencies.build_session_queue(siap.db).take_next()
    stop = threading.Event()
    stop.wait = Mock(side_effect=lambda _: stop.set())
    wait_for_recovery(stop, factory=lambda: nullcontext(siap.db))
    stop.wait.assert_called_once_with(5)
    assert any(
        record.levelname == "ERROR" and str(session_id) in record.getMessage()
        for record in caplog.records
    )
    assert siap.db.get(MeasurementSession, session_id).status == "running"
    assert siap.db.scalar(select(func.count()).select_from(CallLog)) == 0


def test_process_boot_with_no_abandoned_session_is_ready(siap):
    import threading
    from contextlib import nullcontext

    from app.measurement_worker import wait_for_recovery

    stop = threading.Event()
    wait_for_recovery(stop, factory=lambda: nullcontext(siap.db))
    assert not stop.is_set()


def test_store_refuses_cross_session_writes_and_unclaimed_answers(siap):
    from types import SimpleNamespace

    from app.modules.runs.execution_store import TableSessionStore

    session_id = start(siap)
    dependencies.build_session_queue(siap.db).take_next()
    store = TableSessionStore(siap.db, session_id)
    other = uuid.uuid4()
    with pytest.raises(ValueError, match="wrong session"):
        store.list_cases(other)
    with pytest.raises(ValueError, match="wrong session"):
        store.update_progress(other, completed=0, failed=0)
    with pytest.raises(ValueError, match="wrong session"):
        store.mark_status(other, SessionStatus.FINISHED)
    with pytest.raises(ValueError, match="not claimed"):
        store.save_attempt(SimpleNamespace(session_id=other, attempt_id=None))
    with pytest.raises(ValueError, match="not claimed"):
        store.save_attempt(SimpleNamespace(session_id=session_id, attempt_id=None))
    with pytest.raises(RuntimeError, match="unfinished"):
        store.mark_status(session_id, SessionStatus.FINISHED)
    assert store.prepare_attempt(0, 0) is not None
    with pytest.raises(RuntimeError, match="recovery"):
        store.prepare_attempt(0, 0)


def test_lost_attempt_claim_does_not_dispatch(siap, monkeypatch):
    from types import SimpleNamespace

    from sqlalchemy.sql.dml import Update

    from app.modules.runs.execution_store import ExecutionInterrupted, TableSessionStore

    session_id = start(siap)
    dependencies.build_session_queue(siap.db).take_next()
    store = TableSessionStore(siap.db, session_id)
    original = siap.db.execute

    def lose_claim(statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name == "attempts":
            return SimpleNamespace(rowcount=0)
        return original(statement, *args, **kwargs)

    monkeypatch.setattr(siap.db, "execute", lose_claim)
    with pytest.raises(ExecutionInterrupted):
        store.prepare_attempt(0, 0)
    assert siap.db.scalar(select(func.count()).select_from(CallLog)) == 0


def test_output_price_catalog_fallback_is_used_when_copy_has_no_price(siap, monkeypatch):
    from types import SimpleNamespace

    from app.modules.runs import execution

    session_id = start(siap)
    siap.produk.price_input_usd_per_mtok = Decimal("10")
    siap.db.commit()
    monkeypatch.setattr(
        execution,
        "get_settings",
        lambda: SimpleNamespace(
            cost_model_prices={"openai_compatible:deepseek-flash": Decimal("10")},
            cost_model_input_prices={},
        ),
    )
    provider = Provider()
    assert execution.run_once(
        siap.db,
        adapter_factory=lambda *_: provider,
        rates=ConfiguredExchangeRate(Decimal("16000")),
        rate_limit=False,
    )
    assert siap.db.get(MeasurementSession, session_id).status == "finished"
    copy = siap.db.scalar(
        select(SessionProductCopy).where(SessionProductCopy.session_id == session_id)
    )
    from app.modules.runs.adapters import ProductAdapter

    assert execution._FrozenProduct(copy, ProductAdapter(siap.db)).get(uuid.uuid4()) is None
    assert (
        execution._FrozenProduct(copy, SimpleNamespace(get=lambda _: None)).get(copy.product_id)
        is None
    )


def test_default_dispatch_dependencies_honor_operational_rate_limit(siap, monkeypatch):
    from app.modules.runs import execution

    session_id = start(siap)
    provider = Provider()
    runner(siap, provider)
    monkeypatch.setattr(
        execution.providers_service, "build_measurement_adapter", lambda *_: provider
    )
    monkeypatch.setattr(
        execution, "get_exchange_rate", lambda: ConfiguredExchangeRate(Decimal("16000"))
    )
    elapsed = [0.0]
    sleeps = []

    def advance(delay):
        sleeps.append(delay)
        elapsed[0] += delay

    assert execution.run_once(siap.db, sleep=advance, clock=lambda: elapsed[0])
    assert len(provider.calls) == 9
    # 30 calls/minute from the actual product, slept in slices of at most 1 s (SCRUM-267 K4).
    assert sum(sleeps) == 2.0 * 8 and max(sleeps) <= 1.0
    assert siap.db.get(MeasurementSession, session_id).status == "finished"


def test_cancel_during_last_call_is_not_overwritten_by_completion(siap):
    from datetime import UTC, datetime

    from app.modules.runs.repository import SessionStatusRepository

    session_id = start(siap, case_filter="test", repeat_count=1)
    provider = Provider(
        on_call=lambda: SessionStatusRepository(siap.db).change_status(
            session_id, SessionStatus.CANCELLED, at=datetime.now(UTC)
        )
    )
    assert runner(siap, provider)()
    assert len(provider.calls) == 1
    siap.db.expire_all()
    assert siap.db.get(MeasurementSession, session_id).status == "cancelled"
    assert (
        siap.db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.link == f"/runs/{session_id}")
        )
        == 0
    )


def test_missing_rate_after_partial_result_does_not_invent_fatal_midrun_status(siap, caplog):
    from datetime import UTC, datetime

    from app.modules.runs.execution import run_once
    from app.modules.runs.repository import SessionStatusRepository
    from app.shared.exceptions import InternalError

    session_id = start(siap)
    provider = Provider(
        on_call=lambda: SessionStatusRepository(siap.db).change_status(
            session_id, SessionStatus.PAUSED, at=datetime.now(UTC), stop_reason="user"
        )
    )
    assert runner(siap, provider)()
    SessionStatusRepository(siap.db).change_status(
        session_id, SessionStatus.RUNNING, at=datetime.now(UTC)
    )
    with pytest.raises(InternalError):
        run_once(
            siap.db,
            adapter_factory=lambda *_: provider,
            rates=ConfiguredExchangeRate(None),
            rate_limit=False,
        )
    assert len(provider.calls) == 1
    assert siap.db.get(MeasurementSession, session_id).status == "running"
    failures = [record for record in caplog.records if str(session_id) in record.getMessage()]
    assert len(failures) == 1
    assert failures[0].levelname == "ERROR"
    assert "InternalError" in failures[0].getMessage()


def test_budget_increase_allows_resume_without_changing_frozen_token_prices(siap):
    from datetime import UTC, datetime

    from app.modules.runs.repository import SessionStatusRepository

    session_id = start(siap)
    provider = Provider()
    run = runner(siap, provider)
    siap.produk.monthly_budget_idr = Decimal("0.01")
    siap.db.commit()
    assert run()
    assert siap.db.get(MeasurementSession, session_id).status == "paused"
    assert len(provider.calls) == 1
    siap.produk.monthly_budget_idr = Decimal("10000")
    siap.produk.price_input_usd_per_mtok = Decimal("1000")
    siap.produk.price_output_usd_per_mtok = Decimal("1000")
    siap.db.commit()
    SessionStatusRepository(siap.db).change_status(
        session_id, SessionStatus.RUNNING, at=datetime.now(UTC)
    )
    assert run()
    assert len(provider.calls) == 9
    assert siap.db.get(MeasurementSession, session_id).status == "finished"
    assert set(siap.db.scalars(select(CallLog.cost_idr))) == {Decimal("4.80")}


def test_copy_version_flush_has_persisted_attempt_evidence(siap):
    from sqlalchemy import event

    session_id = start(siap)
    previous = siap.db.autoflush
    siap.db.autoflush = False  # Match the production session factory.

    def check_evidence(db, *_):
        for row in db.dirty:
            if isinstance(row, SessionProductCopy) and row.model_version_at_start:
                evidence = db.connection().scalar(
                    select(Attempt.id).where(
                        Attempt.session_id == row.session_id,
                        Attempt.model_version == row.model_version_at_start,
                    )
                )
                assert evidence is not None, "copy version must have already-flushed evidence"

    event.listen(siap.db, "before_flush", check_evidence)
    try:
        assert runner(siap, Provider())()
        assert siap.db.get(MeasurementSession, session_id).status == "finished"
    finally:
        event.remove(siap.db, "before_flush", check_evidence)
        siap.db.autoflush = previous
