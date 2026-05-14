from __future__ import annotations

import logging
from threading import Event, Thread

from app.services.credit_control import CreditControlService

logger = logging.getLogger(__name__)


class CreditControlScheduler:
    def __init__(self, credit_service: CreditControlService, tick_seconds: int) -> None:
        self.credit_service = credit_service
        self.tick_seconds = tick_seconds
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self.tick_seconds <= 0 or self._thread is not None:
            return
        self._thread = Thread(
            target=self._run,
            name="credit-control-scheduler",
            daemon=True,
        )
        self._thread.start()
        try:
            self.credit_service.tick()
        except Exception:
            logger.exception("Credit control startup catch-up failed")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self.tick_seconds):
            try:
                self.credit_service.tick()
            except Exception:
                logger.exception("Credit control scheduler tick failed")
