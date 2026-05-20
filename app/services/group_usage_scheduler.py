from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Thread
from typing import Callable

from app.services.group_usage import GroupUsageService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GroupUsageSchedulerSnapshot:
    enabled: bool
    running: bool
    cadence_seconds: int
    tick_count: int
    last_tick_started_at: datetime | None = None
    last_tick_finished_at: datetime | None = None
    last_tick_error: str | None = None
    last_refreshed_count: int = 0
    last_window_counts: dict[str, int] | None = None


class GroupUsageScheduler:
    def __init__(
        self,
        group_usage_service: GroupUsageService,
        cadence_seconds: int,
        enabled_provider: Callable[[], bool] | None = None,
    ) -> None:
        self.group_usage_service = group_usage_service
        self.cadence_seconds = cadence_seconds
        self.enabled_provider = enabled_provider or (lambda: True)
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._tick_count = 0
        self._last_tick_started_at: datetime | None = None
        self._last_tick_finished_at: datetime | None = None
        self._last_tick_error: str | None = None
        self._last_refreshed_count = 0
        self._last_window_counts: dict[str, int] = {}

    def start(self) -> None:
        if self.cadence_seconds <= 0:
            logger.info(
                "Group usage scheduler disabled | cadence_seconds=%s",
                self.cadence_seconds,
            )
            return
        if self._thread is not None:
            return
        self._thread = Thread(
            target=self._run,
            name="group-usage-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Group usage scheduler started | cadence_seconds=%s",
            self.cadence_seconds,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
            logger.info("Group usage scheduler stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> GroupUsageSchedulerSnapshot:
        return GroupUsageSchedulerSnapshot(
            enabled=self._enabled(),
            running=self.is_running,
            cadence_seconds=self.cadence_seconds,
            tick_count=self._tick_count,
            last_tick_started_at=self._last_tick_started_at,
            last_tick_finished_at=self._last_tick_finished_at,
            last_tick_error=self._last_tick_error,
            last_refreshed_count=self._last_refreshed_count,
            last_window_counts=self._last_window_counts,
        )

    def _run(self) -> None:
        while not self._stop_event.wait(self.cadence_seconds):
            self._tick_once()

    def _tick_once(self) -> None:
        try:
            self._last_tick_started_at = datetime.now(timezone.utc)
            if not self._enabled():
                self._last_refreshed_count = 0
                self._last_window_counts = {}
                self._last_tick_error = None
                logger.info("Group usage scheduler tick skipped | enabled=false")
                return
            result = self.group_usage_service.refresh(now=self._last_tick_started_at)
            self._last_refreshed_count = result.group_count
            self._last_window_counts = result.window_counts
            self._last_tick_error = None
            self._tick_count += 1
            logger.info(
                "Group usage scheduler tick completed | groups=%s windows=%s",
                self._last_refreshed_count,
                self._last_window_counts,
            )
        except Exception as exc:
            self._last_tick_error = str(exc)
            logger.exception("Group usage scheduler tick failed")
        finally:
            self._last_tick_finished_at = datetime.now(timezone.utc)

    def _enabled(self) -> bool:
        try:
            return bool(self.enabled_provider())
        except Exception:
            logger.exception("Group usage scheduler enabled check failed")
            return False


__all__ = ["GroupUsageScheduler", "GroupUsageSchedulerSnapshot"]
