"""Worker lifecycle and safe logging without real signals, sleep, or network."""

import logging
import signal
import threading
from contextlib import nullcontext
from unittest.mock import Mock

from app import measurement_worker


def test_idle_worker_waits_then_stops():
    stop = threading.Event()
    stop.wait = Mock(side_effect=lambda _: stop.set())
    dispatch = Mock(return_value=False)
    measurement_worker.serve(stop, factory=lambda: nullcontext(object()), dispatch=dispatch)
    stop.wait.assert_called_once_with(5)
    assert dispatch.call_count == 1


def test_dispatch_error_does_not_leak_credentials(caplog):
    stop = threading.Event()
    stop.wait = Mock(side_effect=lambda _: stop.set())
    dispatch = Mock(side_effect=RuntimeError("secret-token https://private.example"))
    with caplog.at_level(logging.ERROR):
        measurement_worker.serve(stop, factory=lambda: nullcontext(object()), dispatch=dispatch)
    assert "RuntimeError" in caplog.text
    assert "secret-token" not in caplog.text and "private.example" not in caplog.text


def test_busy_worker_continues_without_idle_wait():
    stop = threading.Event()
    stop.wait = Mock()

    def dispatch(*_, **kwargs):
        stop.set()
        assert kwargs["stopping"]()
        return True

    measurement_worker.serve(stop, factory=lambda: nullcontext(object()), dispatch=dispatch)
    stop.wait.assert_not_called()


def test_main_registers_graceful_shutdown(monkeypatch):
    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda key, value: handlers.update({key: value}))

    def serve(stop, **kwargs):
        assert kwargs["startup"] is measurement_worker.wait_for_recovery
        handlers[signal.SIGINT]()
        assert stop.is_set()
        stop.clear()
        handlers[signal.SIGTERM]()
        assert stop.is_set()

    monkeypatch.setattr(measurement_worker, "serve", serve)
    measurement_worker.main()


def test_startup_hook_runs_before_dispatch():
    stop = threading.Event()
    startup = Mock(side_effect=lambda event: event.set())
    dispatch = Mock()
    measurement_worker.serve(
        stop, factory=lambda: nullcontext(object()), dispatch=dispatch, startup=startup
    )
    startup.assert_called_once_with(stop)
    dispatch.assert_not_called()


def test_module_entrypoint_exits_without_dispatch_when_already_stopping(monkeypatch):
    import runpy
    import sys

    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(threading, "Event", lambda: stop)
    monkeypatch.setattr(signal, "signal", lambda *_: None)
    monkeypatch.delitem(sys.modules, "app.measurement_worker")
    runpy.run_module("app.measurement_worker", run_name="__main__")
