"""Proses worker penilaian: python -m app.grading_worker. SCRUM-78 SCRUM-284 (K-13).

Terpisah dari app.measurement_worker supaya penilaian yang lama tidak menahan sesi pengukuran,
dan sebaliknya. Tiap putaran: tandai run basi sebagai terputus, lalu nilai satu run antre.
"""

import logging
import signal
import threading
from datetime import UTC, datetime

from app import main as application  # noqa: F401 - register all ORM modules before queries
from app.modules.runs.dependencies import get_exchange_rate
from app.modules.runs.grading_worker import fail_stale_runs, run_once
from app.shared.database import get_session_factory

logger = logging.getLogger(__name__)


def serve(
    stop: threading.Event,
    *,
    factory=None,
    dispatch=run_once,
    recover=fail_stale_runs,
    rates=None,
    idle: float = 5,
) -> None:
    sessions = factory or get_session_factory()
    rates = rates or get_exchange_rate()
    while not stop.is_set():
        busy = False
        try:
            with sessions() as db:
                recover(db, now=datetime.now(UTC))
                busy = dispatch(db, now=None, rates=rates)
        except Exception as exc:
            # Tanpa isi galat: pesan provider atau koneksi bisa memuat rahasia.
            logger.error("Grading worker round stopped safely (%s)", type(exc).__name__)
        if not busy:
            stop.wait(idle)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    serve(stop)


if __name__ == "__main__":
    main()
