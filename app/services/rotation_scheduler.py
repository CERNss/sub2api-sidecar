from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Thread
from typing import Callable

from app.models.rotation import RotationTrigger
from app.services.rotation import RotationService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutoRotationSchedulerSnapshot:
    enabled: bool
    running: bool
    cadence_seconds: int
    tick_count: int
    last_tick_started_at: datetime | None = None
    last_tick_finished_at: datetime | None = None
    last_tick_error: str | None = None
    last_run_id: str | None = None
    last_status: str | None = None
    last_moved_count: int = 0
    last_planned_count: int = 0
    last_skipped_count: int = 0
    last_failed_count: int = 0


class AutoRotationScheduler:
    def __init__(
        self,
        rotation_service: RotationService,
        cadence_seconds: int,
        enabled_provider: Callable[[], bool] | None = None,
    ) -> None:
        self.rotation_service = rotation_service
        self.cadence_seconds = cadence_seconds
        self.enabled_provider = enabled_provider or (lambda: True)
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._tick_count = 0
        self._last_tick_started_at: datetime | None = None
        self._last_tick_finished_at: datetime | None = None
        self._last_tick_error: str | None = None
        self._last_run_id: str | None = None
        self._last_status: str | None = None
        self._last_moved_count = 0
        self._last_planned_count = 0
        self._last_skipped_count = 0
        self._last_failed_count = 0

    def start(self) -> None:
        if self.cadence_seconds <= 0:
            logger.info(
                "Auto-rotation scheduler disabled | cadence_seconds=%s",
                self.cadence_seconds,
            )
            return
        if self._thread is not None:
            return
        self._thread = Thread(target=self._run, name="auto-rotation-scheduler", daemon=True)
        self._thread.start()
        logger.info(
            "Auto-rotation scheduler started | cadence_seconds=%s",
            self.cadence_seconds,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
            logger.info("Auto-rotation scheduler stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> AutoRotationSchedulerSnapshot:
        return AutoRotationSchedulerSnapshot(
            enabled=self._enabled(),
            running=self.is_running,
            cadence_seconds=self.cadence_seconds,
            tick_count=self._tick_count,
            last_tick_started_at=self._last_tick_started_at,
            last_tick_finished_at=self._last_tick_finished_at,
            last_tick_error=self._last_tick_error,
            last_run_id=self._last_run_id,
            last_status=self._last_status,
            last_moved_count=self._last_moved_count,
            last_planned_count=self._last_planned_count,
            last_skipped_count=self._last_skipped_count,
            last_failed_count=self._last_failed_count,
        )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._tick_once()
            if self._stop_event.wait(self.cadence_seconds):
                break

    def _tick_once(self) -> None:
        try:
            self._last_tick_started_at = datetime.now(timezone.utc)
            if not self._enabled():
                self._last_tick_error = None
                logger.info("Auto-rotation scheduler tick skipped | enabled=false")
                return
            logger.info("Running interval-based automatic rotation")
            record = self.rotation_service.run_auto_rotation(
                trigger_type=RotationTrigger.automatic_interval
            )
            self._last_run_id = record.run_id
            self._last_status = record.status
            self._last_moved_count = len(record.moved)
            self._last_planned_count = len(record.planned)
            self._last_skipped_count = len(record.skipped)
            self._last_failed_count = len(record.failed)
            self._last_tick_error = None
            self._tick_count += 1
            logger.info(
                "Auto-rotation scheduler tick completed | run_id=%s status=%s moved=%s planned=%s skipped=%s failed=%s",
                self._last_run_id,
                self._last_status,
                self._last_moved_count,
                self._last_planned_count,
                self._last_skipped_count,
                self._last_failed_count,
            )
        except Exception as exc:
            self._last_tick_error = str(exc)
            logger.exception("Interval-based automatic rotation failed")
        finally:
            self._last_tick_finished_at = datetime.now(timezone.utc)

    def _enabled(self) -> bool:
        try:
            return bool(self.enabled_provider())
        except Exception:
            logger.exception("Auto-rotation scheduler enabled check failed")
            return False
