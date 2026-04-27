from __future__ import annotations

import logging
from threading import Event, Thread

from app.models.rotation import RotationTrigger
from app.services.rotation import RotationService

logger = logging.getLogger(__name__)


class AutoRotationScheduler:
    def __init__(self, rotation_service: RotationService, interval_seconds: int) -> None:
        self.rotation_service = rotation_service
        self.interval_seconds = interval_seconds
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self.interval_seconds <= 0 or self._thread is not None:
            return
        self._thread = Thread(target=self._run, name="auto-rotation-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            try:
                logger.info("Running interval-based automatic rotation")
                self.rotation_service.run_auto_rotation(
                    trigger_type=RotationTrigger.automatic_interval
                )
            except Exception:
                logger.exception("Interval-based automatic rotation failed")
