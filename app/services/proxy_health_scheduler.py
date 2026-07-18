from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Thread
from typing import Callable

from app.services.proxy_health import ProxyHealthService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProxyHealthSchedulerSnapshot:
    enabled: bool
    running: bool
    cadence_seconds: int
    tick_count: int
    last_tick_started_at: datetime | None = None
    last_tick_finished_at: datetime | None = None
    last_tick_error: str | None = None
    last_probe_count: int = 0
    last_dead_count: int = 0


class ProxyHealthScheduler:
    def __init__(
        self,
        proxy_health_service: ProxyHealthService,
        cadence_seconds: int,
        enabled_provider: Callable[[], bool] | None = None,
        cadence_provider: Callable[[], int] | None = None,
    ) -> None:
        self.proxy_health_service = proxy_health_service
        self.cadence_seconds = cadence_seconds
        self.enabled_provider = enabled_provider or (lambda: True)
        self.cadence_provider = cadence_provider
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._tick_count = 0
        self._last_tick_started_at: datetime | None = None
        self._last_tick_finished_at: datetime | None = None
        self._last_tick_error: str | None = None
        self._last_probe_count = 0
        self._last_dead_count = 0

    def start(self) -> None:
        if self.cadence_seconds <= 0:
            logger.info(
                "Proxy health scheduler disabled | cadence_seconds=%s",
                self.cadence_seconds,
            )
            return
        if self._thread is not None:
            return
        self._thread = Thread(
            target=self._run, name="proxy-health-scheduler", daemon=True
        )
        self._thread.start()
        logger.info(
            "Proxy health scheduler started | cadence_seconds=%s",
            self.cadence_seconds,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
            logger.info("Proxy health scheduler stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> ProxyHealthSchedulerSnapshot:
        return ProxyHealthSchedulerSnapshot(
            enabled=self._enabled(),
            running=self.is_running,
            cadence_seconds=self._cadence_seconds(),
            tick_count=self._tick_count,
            last_tick_started_at=self._last_tick_started_at,
            last_tick_finished_at=self._last_tick_finished_at,
            last_tick_error=self._last_tick_error,
            last_probe_count=self._last_probe_count,
            last_dead_count=self._last_dead_count,
        )

    def _run(self) -> None:
        last_tick_at = 0.0
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
                self._last_tick_error = None
                logger.info("Proxy health scheduler tick skipped | enabled=false")
                return
            result = self.proxy_health_service.probe_once(
                now=self._last_tick_started_at
            )
            self._last_probe_count = result.probed_count
            self._last_dead_count = result.dead_count
            self._last_tick_error = "; ".join(result.errors) or None
            self._tick_count += 1
            if result.transitions or result.runs:
                logger.info(
                    "Proxy health tick transitions | transitions=%s runs=%s",
                    result.transitions,
                    [run.run_id for run in result.runs],
                )
        except Exception as exc:
            self._last_tick_error = str(exc)
            logger.exception("Proxy health scheduler tick failed")
        finally:
            self._last_tick_finished_at = datetime.now(timezone.utc)

    def _enabled(self) -> bool:
        try:
            return bool(self.enabled_provider())
        except Exception:
            logger.exception("Proxy health scheduler enabled check failed")
            return False

    def _cadence_seconds(self) -> int:
        if self.cadence_provider is None:
            return self.cadence_seconds
        try:
            cadence = int(self.cadence_provider())
        except Exception:
            logger.exception("Proxy health scheduler cadence check failed")
            return self.cadence_seconds
        return max(5, cadence)
