"""Background worker loop. One sequential worker per process, as designed."""
from __future__ import annotations

import logging
import threading

from .service import OrchestrationService

logger = logging.getLogger(__name__)


class Worker:
    """Runs `OrchestrationService.run_cycle()` until stopped.

    Restart recovery relies on the service: RUNNING jobs whose heartbeat is
    older than 90 seconds are marked WORKER_LOST on the next cycle.
    """

    def __init__(self, service: OrchestrationService, interval: float = 2.0):
        self.service = service
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="orchestration-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.service.run_cycle()
            except Exception:  # never let the worker die on a single failure
                logger.exception("worker cycle failed")
            self._stop.wait(self.interval)
