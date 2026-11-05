"""Worker process: python -m app.measurement_worker. SCRUM-76 SCRUM-249."""

import logging
import signal
import threading

from sqlalchemy import select

from app import main as application  # noqa: F401 - register all ORM modules before queries
from app.modules.runs.execution import run_once, worker_lease
from app.modules.runs.models import MeasurementSession, SessionStatus, SessionType
from app.shared.database import get_session_factory

logger = logging.getLogger(__name__)


def wait_for_recovery(stop: threading.Event, *, factory=None) -> None:
    """Boot must not auto-resume an abandoned session before SCRUM-217 recovery."""
    sessions = factory or get_session_factory()
    while not stop.is_set():
        with sessions() as db, worker_lease(db.get_bind()) as acquired:
            if not acquired:
                # A second process must not take over a session after its owner crashes.
                # The supervisor may restart it; startup recovery is checked again then.
                stop.set()
                return
            running = db.scalar(
                select(MeasurementSession.id).where(
                    MeasurementSession.status == SessionStatus.RUNNING,
                    MeasurementSession.session_type == SessionType.AUTOMATIC,
                )
            )
            if running is None:
                return
        logger.error("Measurement worker waiting for startup recovery (SCRUM-217)")
        stop.wait(5)


def serve(stop: threading.Event, *, factory=None, dispatch=run_once, startup=None) -> None:
    if startup is not None:
        startup(stop)
    sessions = factory or get_session_factory()
    while not stop.is_set():
        try:
            with sessions() as db:
                busy = dispatch(db, stopping=stop.is_set)
        except Exception as exc:
            # Do not log exception text/tracebacks: provider/connection errors may contain secrets.
            logger.error("Measurement dispatcher stopped safely (%s)", type(exc).__name__)
            busy = False
        if not busy:
            stop.wait(5)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    serve(stop, startup=wait_for_recovery)


if __name__ == "__main__":
    main()
