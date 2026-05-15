from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Thread
from typing import Callable

from app.services.credit_control import CreditControlService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreditControlSchedulerSnapshot:
    enabled: bool
    running: bool
    cadence_seconds: int
    tick_count: int
    last_tick_started_at: datetime | None = None
    last_tick_finished_at: datetime | None = None
    last_tick_error: str | None = None
    last_run_count: int = 0
    last_affected_count: int = 0
    last_failure_count: int = 0


class CreditControlScheduler:
    def __init__(
        self,
        credit_service: CreditControlService,
        cadence_seconds: int,
        enabled_provider: Callable[[], bool] | None = None,
    ) -> None:
        self.credit_service = credit_service
        self.cadence_seconds = cadence_seconds
        self.enabled_provider = enabled_provider or (lambda: True)
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._tick_count = 0
        self._last_tick_started_at: datetime | None = None
        self._last_tick_finished_at: datetime | None = None
        self._last_tick_error: str | None = None
        self._last_run_count = 0
        self._last_affected_count = 0
        self._last_failure_count = 0

    def start(self) -> None:
        if self.cadence_seconds <= 0:
            logger.info(
                "Credit control scheduler disabled | cadence_seconds=%s",
                self.cadence_seconds,
            )
            return
        if self._thread is not None:
            return
        self._thread = Thread(
            target=self._run,
            name="credit-control-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Credit control scheduler started | cadence_seconds=%s",
            self.cadence_seconds,
        )
        self._tick_once()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
            logger.info("Credit control scheduler stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> CreditControlSchedulerSnapshot:
        return CreditControlSchedulerSnapshot(
            enabled=self._enabled(),
            running=self.is_running,
            cadence_seconds=self.cadence_seconds,
            tick_count=self._tick_count,
            last_tick_started_at=self._last_tick_started_at,
            last_tick_finished_at=self._last_tick_finished_at,
            last_tick_error=self._last_tick_error,
            last_run_count=self._last_run_count,
            last_affected_count=self._last_affected_count,
            last_failure_count=self._last_failure_count,
        )

    def _run(self) -> None:
        while not self._stop_event.wait(self.cadence_seconds):
            self._tick_once()

    def _tick_once(self) -> None:
        try:
            self._last_tick_started_at = datetime.now(timezone.utc)
            if not self._enabled():
                self._last_run_count = 0
                self._last_affected_count = 0
                self._last_failure_count = 0
                self._last_tick_error = None
                logger.info("Credit control scheduler tick skipped | enabled=false")
                return
            records = self.credit_service.tick()
            self._last_run_count = len(records)
            self._last_affected_count = sum(record.success_count for record in records)
            self._last_failure_count = sum(record.failure_count for record in records)
            self._last_tick_error = None
            self._tick_count += 1
            logger.info(
                "Credit control scheduler tick completed | runs=%s affected=%s failures=%s",
                self._last_run_count,
                self._last_affected_count,
                self._last_failure_count,
            )
        except Exception as exc:
            self._last_tick_error = str(exc)
            logger.exception("Credit control scheduler tick failed")
        finally:
            self._last_tick_finished_at = datetime.now(timezone.utc)

    def _enabled(self) -> bool:
        try:
            return bool(self.enabled_provider())
        except Exception:
            logger.exception("Credit control scheduler enabled check failed")
            return False
