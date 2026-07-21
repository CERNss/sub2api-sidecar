from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from app.clients.sub2api import Sub2APIClient, Sub2APIError
from app.errors import ProvisioningError
from app.models.operational_data import OperationalMetricSample
from app.models.proxy_health import (
    ProxyAccountMove,
    ProxyAccountPin,
    ProxyHealthRun,
    ProxyHealthRuntimeSettings,
    ProxyHealthState,
    ProxyParkedAccount,
    ProxyProbeResult,
)
from app.stores.postgres import PostgresFlowStore

logger = logging.getLogger(__name__)

PROXY_UNREACHABLE_METRIC_KEY = "proxy_unreachable"
PROXY_ALL_DOWN_METRIC_KEY = "proxy_all_down"
PROBE_FAN_OUT_WIDTH = 8


class ProxyHealthError(ProvisioningError):
    """Raised when a proxy-health operation cannot proceed."""


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


class ProxyHealthService:
    """Reconciler-style proxy management: scheduled liveness/quality probing of
    upstream proxies, automatic eviction of accounts off dead proxies, even
    redistribution across the survivors, and a direct-connection fallback (with
    parking) when every proxy is down.

    The health verdict is stricter than the upstream's own connectivity status:
    a proxy only counts as healthy when its connectivity test succeeds, its probe
    latency is under the configured bound, AND every configured critical quality
    target (default: openai) passes. Accounts that never had a proxy are never
    touched; accounts force-parked to direct during a total outage are remembered
    and re-proxied once a proxy recovers.
    """

    def __init__(
        self,
        *,
        client: Sub2APIClient,
        store: PostgresFlowStore,
        settings_provider: Callable[[], ProxyHealthRuntimeSettings],
    ) -> None:
        self.client = client
        self.store = store
        self.settings_provider = settings_provider
        # probe_once and rebalance are reachable from both the scheduler thread and
        # the manual API endpoints; overlapping runs would race on health counters
        # and could double-move accounts. Reentrant because probe_once triggers
        # rebalance while holding the lock.
        self._operation_lock = threading.RLock()

    # ---------- panel reads ----------

    def list_proxies_with_health(self) -> list[dict[str, Any]]:
        proxies = self.client.list_proxies()
        states = {state.proxy_id: state for state in self.store.list_proxy_health_states()}
        settings = self.settings_provider()
        items: list[dict[str, Any]] = []
        for proxy in proxies:
            state = states.get(str(proxy["id"]))
            item = dict(proxy)
            item["health"] = state.health if state else "unknown"
            item["health_detail"] = state.model_dump(mode="json") if state else None
            item["alert_whitelisted"] = self._is_alert_whitelisted(
                proxy_id=str(proxy["id"]),
                proxy_name=str(proxy.get("name") or ""),
                settings=settings,
            )
            items.append(item)
        return items

    def list_accounts_with_assignment(self) -> list[dict[str, Any]]:
        """Accounts with their current proxy and whether an operator pinned it."""
        accounts = self.client.list_openai_accounts()
        pins = {pin.account_id: pin for pin in self.store.list_proxy_account_pins()}
        parked = {p.account_id: p for p in self.store.list_proxy_parked_accounts()}
        items: list[dict[str, Any]] = []
        for account in accounts:
            raw = account.get("raw") if isinstance(account.get("raw"), dict) else account
            proxy_id = raw.get("proxy_id") if isinstance(raw, dict) else None
            account_id = str(account.get("id"))
            pin = pins.get(account_id)
            items.append(
                {
                    "account_id": account_id,
                    "account_name": str(account.get("name") or ""),
                    "proxy_id": None if proxy_id in (None, "", 0) else str(proxy_id),
                    "pinned_proxy_id": pin.proxy_id if pin else None,
                    "pinned_at": pin.pinned_at.isoformat() if pin else None,
                    "parked": account_id in parked,
                }
            )
        return items

    def pin_account(self, *, account_id: str, proxy_id: str) -> ProxyAccountPin:
        """Bind an account to one proxy and move it there immediately."""
        with self._operation_lock:
            proxies = self.client.list_proxies()
            if not any(str(proxy["id"]) == str(proxy_id) for proxy in proxies):
                raise ProxyHealthError(f"No proxy found with id={proxy_id}")
            accounts = self.client.list_openai_accounts()
            account = next(
                (item for item in accounts if str(item.get("id")) == str(account_id)),
                None,
            )
            if account is None:
                raise ProxyHealthError(f"No account found with id={account_id}")
            self.client.set_account_proxy(account=account, proxy_id=proxy_id)
            # Pinning is an explicit placement, so any parked record (which exists
            # only to remember an involuntary move to direct) no longer applies.
            self.store.delete_proxy_parked_account(str(account_id))
            return self.store.upsert_proxy_account_pin(
                ProxyAccountPin(
                    account_id=str(account_id),
                    account_name=str(account.get("name") or ""),
                    proxy_id=str(proxy_id),
                )
            )

    def unpin_account(self, account_id: str) -> None:
        """Release an account back to automatic balancing; it stays put for now."""
        with self._operation_lock:
            self.store.delete_proxy_account_pin(str(account_id))

    def clear_proxy_state(self, proxy_id: str) -> None:
        """Drop a proxy's health state and emit a clearing sample so its scoped
        alert recovers immediately — required when the proxy is deleted through
        the panel, because the probe-tick cleanup only handles states it can
        still see, and works even while probing is disabled."""
        with self._operation_lock:
            states = {
                state.proxy_id: state
                for state in self.store.list_proxy_health_states()
            }
            state = states.get(str(proxy_id))
            self.store.delete_proxy_health_state(str(proxy_id))
            moment = datetime.now(timezone.utc)
            try:
                self.store.save_operational_metric_samples(
                    [
                        OperationalMetricSample(
                            metric_key=PROXY_UNREACHABLE_METRIC_KEY,
                            value=0.0,
                            scope_key=f"proxy:{proxy_id}",
                            scope_label=(state.proxy_name if state else "")
                            or str(proxy_id),
                            observed_at=moment,
                            collected_at=moment,
                            snapshot={"proxy_id": str(proxy_id), "removed": True},
                        )
                    ]
                )
            except Exception:
                logger.exception(
                    "Proxy state clearing sample failed | proxy=%s", proxy_id
                )

    # ---------- probing ----------

    def probe_once(
        self, *, now: datetime | None = None, force_quality: bool = False
    ) -> ProxyProbeResult:
        with self._operation_lock:
            return self._probe_once_locked(now=now, force_quality=force_quality)

    def try_probe_once(
        self, *, now: datetime | None = None, force_quality: bool = False
    ) -> ProxyProbeResult | None:
        """Non-blocking variant for the manual endpoints: returns None instead of
        queuing behind a scheduler tick that may hold the lock for minutes."""
        if not self._operation_lock.acquire(blocking=False):
            return None
        try:
            return self._probe_once_locked(now=now, force_quality=force_quality)
        finally:
            self._operation_lock.release()

    def try_rebalance(
        self, *, trigger: str, dry_run: bool = False
    ) -> ProxyHealthRun | None:
        if not self._operation_lock.acquire(blocking=False):
            return None
        try:
            return self._rebalance_locked(trigger=trigger, dry_run=dry_run, now=None)
        finally:
            self._operation_lock.release()

    def _probe_once_locked(
        self, *, now: datetime | None, force_quality: bool
    ) -> ProxyProbeResult:
        settings = self.settings_provider()
        moment = now or datetime.now(timezone.utc)
        result = ProxyProbeResult()
        try:
            proxies = self.client.list_proxies()
        except Sub2APIError as exc:
            result.errors.append(f"list_proxies failed: {exc}")
            return result
        states = {state.proxy_id: state for state in self.store.list_proxy_health_states()}

        def probe(proxy: dict[str, Any]) -> tuple[ProxyHealthState, bool, str]:
            proxy_id = str(proxy["id"])
            state = states.get(proxy_id) or ProxyHealthState(proxy_id=proxy_id)
            state.proxy_name = str(proxy.get("name") or "")
            try:
                test = self.client.test_proxy(proxy_id)
                probe_ok = bool(test.get("success"))
                state.last_probe_latency_ms = _optional_float(test.get("latency_ms"))
                state.last_probe_error = (
                    None if probe_ok else str(test.get("message") or "proxy test failed")
                )
            except Exception as exc:
                probe_ok = False
                state.last_probe_latency_ms = None
                state.last_probe_error = str(exc)
            state.last_probe_at = moment
            state.last_probe_success = probe_ok

            quality_checked = False
            quality_due = (
                force_quality
                or state.last_quality_at is None
                or (moment - state.last_quality_at).total_seconds()
                >= settings.quality_check_interval_seconds
            )
            if probe_ok and quality_due:
                try:
                    quality = self.client.quality_check_proxy(proxy_id)
                    quality_checked = True
                    state.last_quality_at = moment
                    state.last_quality_score = _optional_float(quality.get("score"))
                    state.last_quality_grade = str(quality.get("grade") or "") or None
                    state.last_quality_summary = str(quality.get("summary") or "") or None
                    failing: list[str] = []
                    entries = quality.get("items")
                    if isinstance(entries, list):
                        for entry in entries:
                            if not isinstance(entry, dict):
                                continue
                            target = str(entry.get("target") or "")
                            if (
                                target in settings.critical_targets
                                and str(entry.get("status") or "") != "pass"
                            ):
                                failing.append(target)
                    state.failing_critical_targets = failing
                except Exception as exc:
                    # A failed quality *request* is upstream-checker trouble, not
                    # proof the proxy is bad: keep the last known quality verdict
                    # and let the connectivity test govern this round.
                    logger.warning(
                        "Proxy quality check failed | proxy=%s error=%s", proxy_id, exc
                    )

            latency_ok = True
            if (
                probe_ok
                and settings.latency_threshold_ms is not None
                and state.last_probe_latency_ms is not None
                and state.last_probe_latency_ms > settings.latency_threshold_ms
            ):
                latency_ok = False
                state.last_probe_error = (
                    f"latency {state.last_probe_latency_ms:.0f}ms exceeds threshold "
                    f"{settings.latency_threshold_ms}ms"
                )
            healthy_now = probe_ok and latency_ok and not state.failing_critical_targets
            if healthy_now:
                state.consecutive_successes += 1
                state.consecutive_failures = 0
            else:
                state.consecutive_failures += 1
                state.consecutive_successes = 0

            previous = state.health
            transition = ""
            if previous == "dead":
                if state.consecutive_successes >= settings.recovery_threshold:
                    state.health = "healthy"
                    transition = "recovered"
            elif previous == "healthy":
                if state.consecutive_failures >= settings.failure_threshold:
                    state.health = "dead"
                    transition = "died"
            else:  # unknown: first success settles immediately, death needs the threshold
                if healthy_now:
                    state.health = "healthy"
                elif state.consecutive_failures >= settings.failure_threshold:
                    state.health = "dead"
                    transition = "died"
            if transition:
                state.last_transition_at = moment
            state.updated_at = moment
            return state, quality_checked, transition

        workers = min(PROBE_FAN_OUT_WIDTH, max(1, len(proxies)))
        if proxies:
            if workers > 1:
                with ThreadPoolExecutor(
                    max_workers=workers, thread_name_prefix="proxy-health-probe"
                ) as executor:
                    probe_outcomes = list(executor.map(probe, proxies))
            else:
                probe_outcomes = [probe(proxy) for proxy in proxies]
        else:
            probe_outcomes = []

        samples: list[OperationalMetricSample] = []
        died = False
        recovered = False
        for state, quality_checked, transition in probe_outcomes:
            self.store.upsert_proxy_health_state(state)
            result.probed_count += 1
            if quality_checked:
                result.quality_checked_count += 1
            if state.health == "dead":
                result.dead_count += 1
            if transition == "died":
                died = True
                result.transitions.append(f"{state.proxy_name or state.proxy_id}: died")
            elif transition == "recovered":
                recovered = True
                result.transitions.append(
                    f"{state.proxy_name or state.proxy_id}: recovered"
                )
            samples.append(
                self._sample_for_state(
                    state,
                    moment,
                    whitelisted=self._is_alert_whitelisted(
                        proxy_id=state.proxy_id,
                        proxy_name=state.proxy_name,
                        settings=settings,
                    ),
                )
            )

        # States for proxies that no longer exist upstream: clear them and emit a
        # final recovery sample so scoped alert rules stop firing for them.
        current_ids = {str(proxy["id"]) for proxy in proxies}
        for proxy_id, state in states.items():
            if proxy_id in current_ids:
                continue
            self.store.delete_proxy_health_state(proxy_id)
            samples.append(
                OperationalMetricSample(
                    metric_key=PROXY_UNREACHABLE_METRIC_KEY,
                    value=0.0,
                    scope_key=f"proxy:{proxy_id}",
                    scope_label=state.proxy_name or proxy_id,
                    observed_at=moment,
                    collected_at=moment,
                    snapshot={"proxy_id": proxy_id, "removed": True},
                )
            )

        # Aggregate "every proxy is down" signal: keeps firing (value 1) on every
        # tick while the outage lasts, so alert rules re-notify per their cooldown.
        all_down = bool(probe_outcomes) and all(
            state.health == "dead" for state, _, _ in probe_outcomes
        )
        result.all_proxies_down = all_down
        samples.append(
            OperationalMetricSample(
                metric_key=PROXY_ALL_DOWN_METRIC_KEY,
                value=1.0 if all_down else 0.0,
                observed_at=moment,
                collected_at=moment,
                snapshot={
                    "proxy_count": len(probe_outcomes),
                    "dead_count": result.dead_count,
                },
            )
        )

        if samples:
            try:
                self.store.save_operational_metric_samples(samples)
            except Exception as exc:
                result.errors.append(f"save samples failed: {exc}")
                logger.exception("Proxy health sample persistence failed")

        if settings.auto_move_enabled:
            trigger: str | None = None
            # This tick's edge events label the run; the level conditions below
            # only decide that a reconcile is due. Any dead proxy re-attempts the
            # eviction every tick, so a rebalance that failed (upstream hiccup,
            # partial move failure) retries instead of stranding accounts until
            # the next transition. Steady state stays quiet because automatic
            # noop runs are not persisted.
            if died:
                trigger = "proxy_dead"
            elif recovered:
                trigger = "proxy_recovered"
            elif result.dead_count > 0 or all_down:
                trigger = "proxy_dead"
            elif self._has_recoverable_parked_accounts(probe_outcomes):
                trigger = "proxy_recovered"
            if trigger is not None:
                try:
                    run = self.rebalance(trigger=trigger, now=moment)
                    if run.status != "noop":
                        result.runs.append(run)
                except Exception as exc:
                    result.errors.append(f"auto rebalance failed: {exc}")
                    logger.exception("Proxy health auto rebalance failed")
        return result

    def _has_recoverable_parked_accounts(
        self, probe_outcomes: list[tuple[ProxyHealthState, bool, str]]
    ) -> bool:
        if not any(state.health == "healthy" for state, _, _ in probe_outcomes):
            return False
        try:
            return bool(self.store.list_proxy_parked_accounts())
        except Exception:
            logger.exception("Proxy parked account lookup failed")
            return False

    def _is_alert_whitelisted(
        self, *, proxy_id: str, proxy_name: str, settings: ProxyHealthRuntimeSettings
    ) -> bool:
        entries = {entry.strip() for entry in settings.alert_whitelist if entry.strip()}
        return bool(entries) and (proxy_id in entries or (proxy_name and proxy_name in entries))

    def _sample_for_state(
        self, state: ProxyHealthState, moment: datetime, *, whitelisted: bool
    ) -> OperationalMetricSample:
        # Whitelisted proxies never fire: they are still probed and still evicted,
        # only the alarm is muted by reporting a permanently-clear sample.
        value = 0.0 if whitelisted else (1.0 if state.health == "dead" else 0.0)
        return OperationalMetricSample(
            metric_key=PROXY_UNREACHABLE_METRIC_KEY,
            value=value,
            scope_key=f"proxy:{state.proxy_id}",
            scope_label=state.proxy_name or state.proxy_id,
            observed_at=moment,
            collected_at=moment,
            snapshot={
                "proxy_id": state.proxy_id,
                "proxy_name": state.proxy_name,
                "health": state.health,
                "alert_whitelisted": whitelisted,
                "consecutive_failures": state.consecutive_failures,
                "last_probe_error": state.last_probe_error,
                "failing_critical_targets": state.failing_critical_targets,
                "last_quality_score": state.last_quality_score,
                "last_quality_grade": state.last_quality_grade,
            },
        )

    # ---------- rebalancing ----------

    def rebalance(
        self,
        *,
        trigger: str,
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> ProxyHealthRun:
        with self._operation_lock:
            return self._rebalance_locked(trigger=trigger, dry_run=dry_run, now=now)

    def _rebalance_locked(
        self, *, trigger: str, dry_run: bool, now: datetime | None
    ) -> ProxyHealthRun:
        moment = now or datetime.now(timezone.utc)
        run = ProxyHealthRun(
            run_id=uuid.uuid4().hex,
            trigger=trigger,
            dry_run=dry_run,
            created_at=moment,
        )
        try:
            proxies = self.client.list_proxies()
            accounts = self.client.list_openai_accounts()
        except Sub2APIError as exc:
            run.status = "failed"
            run.reason = f"upstream read failed: {exc}"
            return self.store.save_proxy_health_run(run)
        states = {state.proxy_id: state for state in self.store.list_proxy_health_states()}
        parked = {p.account_id: p for p in self.store.list_proxy_parked_accounts()}
        pins = {p.account_id: p for p in self.store.list_proxy_account_pins()}

        def eligible(proxy: dict[str, Any]) -> bool:
            state = states.get(str(proxy["id"]))
            health = state.health if state else "unknown"
            status = str(proxy.get("status") or "active").strip().lower()
            return health != "dead" and status == "active"

        eligible_ids = [str(proxy["id"]) for proxy in proxies if eligible(proxy)]
        run.eligible_proxy_ids = eligible_ids
        run.dead_proxy_ids = [
            str(proxy["id"]) for proxy in proxies if str(proxy["id"]) not in eligible_ids
        ]

        # Accounts on some proxy participate; genuinely-direct accounts never do.
        # Parked accounts (forced to direct during a total outage) rejoin the pool
        # as soon as an eligible proxy exists.
        account_by_id: dict[str, dict[str, Any]] = {}
        proxied: list[tuple[str, str]] = []
        parked_direct: list[str] = []
        for account in accounts:
            raw = account.get("raw") if isinstance(account.get("raw"), dict) else account
            proxy_id = raw.get("proxy_id") if isinstance(raw, dict) else None
            account_id = str(account.get("id"))
            if proxy_id in (None, "", 0):
                if account_id in parked:
                    account_by_id[account_id] = account
                    parked_direct.append(account_id)
                continue
            account_by_id[account_id] = account
            proxied.append((account_id, str(proxy_id)))
            if account_id in parked and not dry_run:
                # Someone already put it back on a proxy; parking no longer applies.
                self.store.delete_proxy_parked_account(account_id)

        # Parked/pin records for accounts that vanished upstream are stale, as are
        # pins onto a proxy that no longer exists; drop them.
        if not dry_run:
            known_ids = {str(account.get("id")) for account in accounts}
            for parked_id in list(parked):
                if parked_id not in known_ids:
                    self.store.delete_proxy_parked_account(parked_id)
            known_proxy_ids = {str(proxy["id"]) for proxy in proxies}
            for pinned_id, pin in list(pins.items()):
                if pinned_id not in known_ids or pin.proxy_id not in known_proxy_ids:
                    self.store.delete_proxy_account_pin(pinned_id)
                    pins.pop(pinned_id, None)

        # Accounts pinned onto an eligible proxy are placed by operator intent, not
        # by the even split. A pin whose proxy is currently dead is deliberately
        # absent here: the account rejoins the pool so traffic keeps flowing, and
        # the surviving pin record sends it home once the proxy recovers.
        pin_target: dict[str, str] = {
            account_id: pin.proxy_id
            for account_id, pin in pins.items()
            if account_id in account_by_id and pin.proxy_id in eligible_ids
        }

        if not proxied and not parked_direct:
            run.status = "noop"
            run.reason = "no proxied accounts"
            return self._finish_run(run, persist=trigger == "manual" or dry_run)

        moves: list[ProxyAccountMove] = []
        if not eligible_ids:
            # Total outage: fall back to direct connection so traffic keeps flowing,
            # and park the accounts so recovery puts them back on proxies.
            run.fallback_direct = True
            run.reason = "no eligible proxies; falling back to direct connection"
            for account_id, from_proxy_id in proxied:
                moves.append(
                    ProxyAccountMove(
                        account_id=account_id,
                        account_name=str(account_by_id[account_id].get("name") or ""),
                        from_proxy_id=from_proxy_id,
                        to_proxy_id=None,
                        pinned=account_id in pins,
                    )
                )
            if not moves:
                run.status = "noop"
                run.reason = "all proxies down; proxied accounts already parked"
                return self._finish_run(run, persist=trigger == "manual" or dry_run)
        else:
            total_to_place = len(proxied) + len(parked_direct)
            where: dict[str, str | None] = {
                account_id: None for account_id in parked_direct
            }
            where.update({account_id: proxy_id for account_id, proxy_id in proxied})

            # Pinned accounts claim their target's slots first and are returned to
            # it whenever they have drifted away (rescued during an outage of that
            # proxy, or moved by an earlier even split).
            pinned_by_proxy: dict[str, list[str]] = {
                proxy_id: [] for proxy_id in eligible_ids
            }
            for account_id, target in pin_target.items():
                pinned_by_proxy[target].append(account_id)
                if where.get(account_id) != target:
                    moves.append(
                        ProxyAccountMove(
                            account_id=account_id,
                            account_name=str(
                                account_by_id[account_id].get("name") or ""
                            ),
                            from_proxy_id=where.get(account_id),
                            to_proxy_id=target,
                            pinned=True,
                        )
                    )

            current: dict[str, list[str]] = {proxy_id: [] for proxy_id in eligible_ids}
            homeless: list[tuple[str, str | None]] = []
            for account_id in parked_direct:
                if account_id not in pin_target:
                    homeless.append((account_id, None))
            for account_id, proxy_id in proxied:
                if account_id in pin_target:
                    continue
                if proxy_id in current:
                    current[proxy_id].append(account_id)
                else:
                    homeless.append((account_id, proxy_id))

            # Even split with minimal movement: hand the +1 caps to the proxies that
            # already hold the most accounts, keep every account below its proxy's
            # cap, and only relocate the surplus plus stranded/parked accounts.
            # Pinned accounts count against their proxy's cap (so a proxy loaded
            # with pins receives fewer free accounts) and can never be evicted by
            # it, hence the floor at the pinned count.
            base, remainder = divmod(total_to_place, len(eligible_ids))
            cap_order = sorted(
                eligible_ids,
                key=lambda pid: len(current[pid]) + len(pinned_by_proxy[pid]),
                reverse=True,
            )
            caps = {
                proxy_id: max(
                    base + (1 if index < remainder else 0),
                    len(pinned_by_proxy[proxy_id]),
                )
                for index, proxy_id in enumerate(cap_order)
            }
            free_caps = {
                proxy_id: caps[proxy_id] - len(pinned_by_proxy[proxy_id])
                for proxy_id in eligible_ids
            }
            pool: list[tuple[str, str | None]] = list(homeless)
            kept: dict[str, int] = {}
            for proxy_id in eligible_ids:
                kept[proxy_id] = min(len(current[proxy_id]), free_caps[proxy_id])
                for account_id in current[proxy_id][free_caps[proxy_id]:]:
                    pool.append((account_id, proxy_id))

            for proxy_id in eligible_ids:
                deficit = free_caps[proxy_id] - kept[proxy_id]
                for _ in range(deficit):
                    if not pool:
                        break
                    account_id, from_proxy_id = pool.pop(0)
                    moves.append(
                        ProxyAccountMove(
                            account_id=account_id,
                            account_name=str(
                                account_by_id[account_id].get("name") or ""
                            ),
                            from_proxy_id=from_proxy_id,
                            to_proxy_id=proxy_id,
                        )
                    )

        if not moves:
            run.status = "noop"
            run.reason = "already balanced"
            return self._finish_run(run, persist=trigger == "manual" or dry_run)

        for move in moves:
            if dry_run:
                continue
            if move.from_proxy_id == move.to_proxy_id:
                move.status = "skipped"
                move.reason = "already on target proxy"
                continue
            try:
                if move.to_proxy_id is None:
                    # Park BEFORE the upstream move: if the move then fails the
                    # account is still on its proxy and the stale parked record is
                    # cleaned up by the next rebalance. The reverse order has an
                    # unrecoverable crash window that would strand a moved account
                    # as "genuinely direct" forever.
                    self.store.upsert_proxy_parked_account(
                        ProxyParkedAccount(
                            account_id=move.account_id,
                            account_name=move.account_name,
                            parked_from_proxy_id=move.from_proxy_id,
                            parked_at=moment,
                        )
                    )
                self.client.set_account_proxy(
                    account=account_by_id[move.account_id],
                    proxy_id=move.to_proxy_id,
                )
                move.status = "moved"
                if move.to_proxy_id is not None and move.account_id in parked:
                    self.store.delete_proxy_parked_account(move.account_id)
            except Sub2APIError as exc:
                move.status = "failed"
                move.reason = str(exc)
                logger.exception(
                    "Proxy account move failed | account=%s from=%s to=%s",
                    move.account_id,
                    move.from_proxy_id,
                    move.to_proxy_id,
                )
            except Exception as exc:
                # Storage or other unexpected errors must not abort the whole
                # batch; record them like upstream failures.
                move.status = "failed"
                move.reason = f"unexpected error: {exc}"
                logger.exception(
                    "Proxy account move failed unexpectedly | account=%s from=%s to=%s",
                    move.account_id,
                    move.from_proxy_id,
                    move.to_proxy_id,
                )

        run.moves = moves
        run.moved_count = sum(1 for move in moves if move.status == "moved")
        run.skipped_count = sum(1 for move in moves if move.status == "skipped")
        run.failed_count = sum(1 for move in moves if move.status == "failed")
        if dry_run:
            run.status = "completed"
        elif run.failed_count and not run.moved_count:
            run.status = "failed"
        elif run.failed_count:
            run.status = "partial_failed"
        else:
            run.status = "completed"
        return self._finish_run(run, persist=True)

    def _finish_run(self, run: ProxyHealthRun, *, persist: bool) -> ProxyHealthRun:
        # Automatic reconcile ticks would otherwise flood the runs table with noop
        # records (e.g. one per minute during a total outage that is already
        # evacuated); only manual/dry-run noops are worth keeping as feedback.
        if persist:
            return self.store.save_proxy_health_run(run)
        return run
