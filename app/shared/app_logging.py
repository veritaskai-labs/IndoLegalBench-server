"""Make the app's own log lines reach the container's stdout.

Under uvicorn the `app.*` loggers have no handler: WARNING and above still come out through
Python's last-resort handler, but INFO lines are dropped, so `docker logs` (and any log tool
that collects it) never sees them. The workers do have output, because they call
logging.basicConfig() on the root logger.

The handler below writes only while the root logger has no handler of its own. So the API gets
its lines, the workers (root configured) do not print every line twice, and pytest (root has
its capture handlers) keeps seeing records through caplog.
"""

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class _StdoutUnlessRootHandles(logging.StreamHandler):
    def __init__(self) -> None:
        super().__init__(sys.stdout)
        self.setFormatter(logging.Formatter(_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        if logging.getLogger().handlers:
            return
        super().emit(record)


def send_app_logs_to_stdout() -> None:
    """Install the handler on the `app` logger once, at INFO. Safe to call again."""
    app_logger = logging.getLogger("app")
    if not any(isinstance(h, _StdoutUnlessRootHandles) for h in app_logger.handlers):
        app_logger.addHandler(_StdoutUnlessRootHandles())
    if app_logger.level == logging.NOTSET:
        app_logger.setLevel(logging.INFO)
