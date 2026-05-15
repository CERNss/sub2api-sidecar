from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Thread
from typing import Callable

from app.models.operational_data import OperationalDataSourceStatus
from app.services.notification import NotificationService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotificationSchedulerSnapshot:
    enabled: bool
    running: bool
    cadence_seconds: int
    tick_count: int
    last_tick_started_at: datetime | None = None
    last_tick_finished_at: datetime | None = None
    last_tick_error: str | None = None
    last_outcome_count: int = 0
    last_delivery_count: int = 0
    last_sampling_started_at: datetime | None = None
    last_sampling_finished_at: datetime | None = None
    last_sampling_error: str | None = None
    sampled_signal_count: int = 0
    source_statuses: list[OperationalDataSourceStatus] | None = None


class NotificationScheduler:
    def __init__(
        self,
        notification_service: NotificationService,
        cadence_seconds: int,
        enabled_provider: Callable[[], bool] | None = None,
        cadence_provider: Callable[[], int] | None = None,
    ) -> None:
        self.notification_service = notification_service
        self.cadence_seconds = cadence_seconds
        self.enabled_provider = enabled_provider or (lambda: True)
        self.cadence_provider = cadence_provider
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._tick_count = 0
        self._last_tick_started_at: datetime | None = None
        self._last_tick_finished_at: datetime | None = None
        self._last_tick_error: str | None = None
        self._last_outcome_count = 0
        self._last_delivery_count = 0

    def start(self) -> None:
        if self.cadence_seconds <= 0:
            logger.info(
                "Notification scheduler disabled | cadence_seconds=%s",
                self.cadence_seconds,
            )
            return
        if self._thread is not None:
            return
        self._thread = Thread(
            target=self._run, name="notification-scheduler", daemon=True
        )
        self._thread.start()
        logger.info(
            "Notification scheduler started | cadence_seconds=%s",
            self.cadence_seconds,
        )
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
        collection = getattr(self.notification_service, "last_collection_result", None)
        store = getattr(self.notification_service, "store", None)
        source_statuses = collection.source_statuses if collection else None
        if source_statuses is None and store is not None:
            source_statuses = store.list_operational_data_source_statuses()
        cadence_seconds = self._cadence_seconds()
        return NotificationSchedulerSnapshot(
            enabled=self._enabled(),
            running=self.is_running,
            cadence_seconds=cadence_seconds,
            tick_count=self._tick_count,
            last_tick_started_at=self._last_tick_started_at,
            last_tick_finished_at=self._last_tick_finished_at,
            last_tick_error=self._last_tick_error,
            last_outcome_count=self._last_outcome_count,
            last_delivery_count=self._last_delivery_count,
            last_sampling_started_at=collection.started_at if collection else None,
            last_sampling_finished_at=collection.finished_at if collection else None,
            last_sampling_error=collection.error_message if collection else None,
            sampled_signal_count=collection.sampled_signal_count if collection else 0,
            source_statuses=source_statuses or [],
        )

    def _run(self) -> None:
        last_tick_at = time.monotonic()
        while not self._stop_event.is_set():
            next_tick_at = last_tick_at + self._cadence_seconds()
            wait_seconds = min(1.0, max(0.0, next_tick_at - time.monotonic()))
            if wait_seconds > 0:
                if self._stop_event.wait(wait_seconds):
                    break
                continue
            self._tick_once()
            last_tick_at = time.monotonic()

    def _tick_once(self) -> None:
        try:
            self._last_tick_started_at = datetime.now(timezone.utc)
            if not self._enabled():
                self._last_outcome_count = 0
                self._last_delivery_count = 0
                self._last_tick_error = None
                logger.info("Notification scheduler tick skipped | enabled=false")
                return
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

    def _enabled(self) -> bool:
        try:
            return bool(self.enabled_provider())
        except Exception:
            logger.exception("Notification scheduler enabled check failed")
            return False

    def _cadence_seconds(self) -> int:
        if self.cadence_provider is None:
            return self.cadence_seconds
        try:
            cadence = int(self.cadence_provider())
        except Exception:
            logger.exception("Notification scheduler cadence check failed")
            return self.cadence_seconds
        return max(5, cadence)
