from __future__ import annotations

import logging
from threading import Event, Thread

from app.services.notification import NotificationService

logger = logging.getLogger(__name__)


class NotificationScheduler:
    def __init__(self, notification_service: NotificationService, tick_seconds: int) -> None:
        self.notification_service = notification_service
        self.tick_seconds = tick_seconds
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self.tick_seconds <= 0 or self._thread is not None:
            return
        self._thread = Thread(
            target=self._run, name="notification-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self.tick_seconds):
            try:
                self.notification_service.tick()
            except Exception:
                logger.exception("Notification scheduler tick failed")
