from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Thread

from app.services.notification import NotificationService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotificationSchedulerSnapshot:
    enabled: bool
    running: bool
    tick_seconds: int
    tick_count: int
    last_tick_started_at: datetime | None = None
    last_tick_finished_at: datetime | None = None
    last_tick_error: str | None = None
    last_outcome_count: int = 0
    last_delivery_count: int = 0


class NotificationScheduler:
    def __init__(self, notification_service: NotificationService, tick_seconds: int) -> None:
        self.notification_service = notification_service
        self.tick_seconds = tick_seconds
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._tick_count = 0
        self._last_tick_started_at: datetime | None = None
        self._last_tick_finished_at: datetime | None = None
        self._last_tick_error: str | None = None
        self._last_outcome_count = 0
        self._last_delivery_count = 0

    def start(self) -> None:
        if self.tick_seconds <= 0:
            logger.info("Notification scheduler disabled | tick_seconds=%s", self.tick_seconds)
            return
        if self._thread is not None:
            return
        self._thread = Thread(
            target=self._run, name="notification-scheduler", daemon=True
        )
        self._thread.start()
        logger.info("Notification scheduler started | tick_seconds=%s", self.tick_seconds)
        self._tick_once()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
            logger.info("Notification scheduler stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> NotificationSchedulerSnapshot:
        return NotificationSchedulerSnapshot(
            enabled=self.tick_seconds > 0,
            running=self.is_running,
            tick_seconds=self.tick_seconds,
            tick_count=self._tick_count,
            last_tick_started_at=self._last_tick_started_at,
            last_tick_finished_at=self._last_tick_finished_at,
            last_tick_error=self._last_tick_error,
            last_outcome_count=self._last_outcome_count,
            last_delivery_count=self._last_delivery_count,
        )

    def _run(self) -> None:
        while not self._stop_event.wait(self.tick_seconds):
            self._tick_once()

    def _tick_once(self) -> None:
        try:
            self._last_tick_started_at = datetime.now(timezone.utc)
            outcomes = self.notification_service.tick(now=self._last_tick_started_at)
            self._last_outcome_count = len(outcomes)
            self._last_delivery_count = sum(len(outcome.deliveries) for outcome in outcomes)
            self._last_tick_error = None
            self._tick_count += 1
            logger.info(
                "Notification scheduler tick completed | rules_evaluated=%s deliveries=%s",
                self._last_outcome_count,
                self._last_delivery_count,
            )
        except Exception as exc:
            self._last_tick_error = str(exc)
            logger.exception("Notification scheduler tick failed")
        finally:
            self._last_tick_finished_at = datetime.now(timezone.utc)
