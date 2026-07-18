from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from app.clients.sub2api import Sub2APIClient, Sub2APIError
from app.errors import ProvisioningError
from app.models.account_health import (
    AccountHealthAction,
    AccountHealthRun,
    AccountHealthRuntimeSettings,
    AccountHealthState,
    AccountReconcileResult,
)
from app.models.operational_data import OperationalMetricSample
from app.stores.postgres import PostgresFlowStore

logger = logging.getLogger(__name__)

ACCOUNT_EVICTED_METRIC_KEY = "account_evicted"


class AccountHealthError(ProvisioningError):
    """Raised when an account-health operation cannot proceed."""


class AccountHealthService:
    """Reconciler for account scheduling: taint unhealthy accounts, evict them by
    pausing upstream scheduling (schedulable=false), and rejoin them once they
    are detected healthy again.

    Graded recovery: transient issues (rate limits) clear on their own and the
    account rejoins automatically; persistent issues (re-auth, bans) keep the
    account evicted until the underlying signal actually clears after human
    intervention. Accounts paused by an admin directly upstream are never
    touched — only evictions performed by the sidecar are rejoined by it.
    """

    def __init__(
        self,
        *,
        client: Sub2APIClient,
        store: PostgresFlowStore,
        settings_provider: Callable[[], AccountHealthRuntimeSettings],
    ) -> None:
        self.client = client
        self.store = store
        self.settings_provider = settings_provider
        self._operation_lock = threading.RLock()

    # ---------- panel reads ----------

    def list_accounts_with_health(self) -> list[dict[str, Any]]:
        accounts = self.client.list_openai_accounts()
        states = {
            state.account_id: state for state in self.store.list_account_health_states()
        }
        settings = self.settings_provider()
        items: list[dict[str, Any]] = []
        for account in accounts:
            account_id = str(account.get("id"))
            state = states.get(account_id)
            item = {
                "id": account.get("id"),
                "name": account.get("name"),
                "status": account.get("status"),
                "schedulable": account.get("schedulable"),
                "availability_status": account.get("availability_status"),
                "last_error": account.get("last_error"),
                "health": state.health if state else "unknown",
                "classification": state.classification if state else None,
                "evicted": state.evicted if state else False,
                "health_detail": state.model_dump(mode="json") if state else None,
                "alert_whitelisted": self._is_alert_whitelisted(
                    account_id=account_id,
                    account_name=str(account.get("name") or ""),
                    settings=settings,
                ),
            }
            items.append(item)
        return items

    # ---------- reconcile ----------

    def reconcile_once(
        self, *, now: datetime | None = None, trigger: str = "auto"
    ) -> AccountReconcileResult:
        with self._operation_lock:
            return self._reconcile_locked(now=now, trigger=trigger)

    def _reconcile_locked(
        self, *, now: datetime | None, trigger: str
    ) -> AccountReconcileResult:
        settings = self.settings_provider()
        moment = now or datetime.now(timezone.utc)
        result = AccountReconcileResult()
        try:
            accounts = self.client.list_openai_accounts()
        except Sub2APIError as exc:
            result.errors.append(f"list_openai_accounts failed: {exc}")
            return result
        states = {
            state.account_id: state for state in self.store.list_account_health_states()
        }
        actions: list[AccountHealthAction] = []
        samples: list[OperationalMetricSample] = []
        bad_statuses = set(settings.transient_statuses) | set(
            settings.persistent_statuses
        )

        for account in accounts:
            account_id = str(account.get("id"))
            account_name = str(account.get("name") or "")
            state = states.pop(account_id, None) or AccountHealthState(
                account_id=account_id
            )
            state.account_name = account_name
            availability = str(account.get("availability_status") or "unknown")
            schedulable = account.get("schedulable")
            admin_disabled = str(account.get("status") or "").strip().lower() in {
                "disabled",
                "inactive",
            }
            # Adopt half-executed evictions: a toggle that errored client-side may
            # still have landed upstream; without this it would be misread as an
            # admin pause and silently dropped from management forever.
            if (
                schedulable is False
                and not state.evicted
                and state.evict_attempted_at is not None
            ):
                state.evicted = True
                state.evicted_by = state.evicted_by or "auto"
            if schedulable is True:
                state.evict_attempted_at = None
            # Our own eviction reads back from the parser as "unavailable"
            # (schedulable=false without any underlying fault signal). Treat that
            # as unknown so the account's actual recovery remains observable —
            # otherwise automatic rejoin would deadlock on our own taint.
            if (
                state.evicted
                and availability == "unavailable"
                and account.get("availability_from_schedulable_only")
            ):
                availability = "unknown"
            # An account paused upstream by hand (schedulable false, not our
            # eviction) or disabled outright belongs to the admin — observe only.
            externally_managed = (
                admin_disabled or (schedulable is False and not state.evicted)
            )

            bad_now = availability in bad_statuses
            state.last_availability_status = availability
            state.last_error = (
                str(account.get("last_error") or "") or None if bad_now else None
            )
            if bad_now:
                state.consecutive_failures += 1
                state.consecutive_successes = 0
                state.classification = (
                    "persistent"
                    if availability in settings.persistent_statuses
                    else "transient"
                )
                result.bad_count += 1
            else:
                state.consecutive_successes += 1
                state.consecutive_failures = 0

            previous = state.health
            if bad_now and state.consecutive_failures >= settings.failure_threshold:
                state.health = "bad"
            elif not bad_now and (
                previous == "unknown"
                or state.consecutive_successes >= settings.recovery_threshold
            ):
                state.health = "healthy"
            if state.health != previous:
                state.last_transition_at = moment

            if not externally_managed and settings.auto_evict_enabled:
                if state.health == "bad" and not state.evicted:
                    actions.append(
                        self._apply_schedulable(
                            state, account_name, schedulable=False, moment=moment
                        )
                    )
                    result.transitions.append(f"{account_name or account_id}: evicted")
                elif (
                    state.evicted
                    and state.health == "healthy"
                    and state.evicted_by != "manual"
                ):
                    actions.append(
                        self._apply_schedulable(
                            state, account_name, schedulable=True, moment=moment
                        )
                    )
                    result.transitions.append(f"{account_name or account_id}: rejoined")
                elif (
                    state.evicted
                    and state.health == "bad"
                    and (
                        state.last_recovery_test_at is None
                        or (moment - state.last_recovery_test_at).total_seconds()
                        >= settings.recovery_test_interval_seconds
                    )
                ):
                    # Nudge the upstream to re-test the evicted account so a manual
                    # fix (e.g. re-auth) is detected even if passive signals idle.
                    state.last_recovery_test_at = moment
                    try:
                        self.client.test_account(account_id)
                    except Exception as exc:
                        logger.info(
                            "Evicted account recovery test failed | account=%s error=%s",
                            account_id,
                            exc,
                        )

            state.updated_at = moment
            self.store.upsert_account_health_state(state)
            result.checked_count += 1
            if state.evicted:
                result.evicted_total += 1
            samples.append(
                self._sample_for_state(
                    state,
                    moment,
                    whitelisted=self._is_alert_whitelisted(
                        account_id=account_id,
                        account_name=account_name,
                        settings=settings,
                    ),
                )
            )

        # States for accounts that vanished upstream: clear them and let their
        # scoped alerts recover.
        for account_id, state in states.items():
            self.store.delete_account_health_state(account_id)
            samples.append(
                OperationalMetricSample(
                    metric_key=ACCOUNT_EVICTED_METRIC_KEY,
                    value=0.0,
                    scope_key=f"account:{account_id}",
                    scope_label=state.account_name or account_id,
                    observed_at=moment,
                    collected_at=moment,
                    snapshot={"account_id": account_id, "removed": True},
                )
            )

        if samples:
            try:
                self.store.save_operational_metric_samples(samples)
            except Exception as exc:
                result.errors.append(f"save samples failed: {exc}")
                logger.exception("Account health sample persistence failed")

        if actions or trigger == "manual":
            run = AccountHealthRun(
                run_id=uuid.uuid4().hex,
                trigger=trigger,
                actions=actions,
                evicted_count=sum(
                    1 for a in actions if a.action == "evict" and a.status == "done"
                ),
                rejoined_count=sum(
                    1 for a in actions if a.action == "rejoin" and a.status == "done"
                ),
                failed_count=sum(1 for a in actions if a.status == "failed"),
                created_at=moment,
            )
            result.run = self.store.save_account_health_run(run)
        return result

    def _apply_schedulable(
        self,
        state: AccountHealthState,
        account_name: str,
        *,
        schedulable: bool,
        moment: datetime,
        source: str = "auto",
    ) -> AccountHealthAction:
        action = AccountHealthAction(
            account_id=state.account_id,
            account_name=account_name,
            action="rejoin" if schedulable else "evict",
            classification=state.classification,
        )
        try:
            if not schedulable:
                # Record the attempt before the call so a lost response can be
                # adopted on the next tick instead of misread as an admin pause.
                state.evict_attempted_at = moment
            self.client.set_account_schedulable(state.account_id, schedulable)
            state.evicted = not schedulable
            if schedulable:
                state.evicted_by = None
                state.evict_attempted_at = None
                state.classification = None
            else:
                state.evicted_by = source
        except Exception as exc:
            action.status = "failed"
            action.reason = str(exc)
            logger.exception(
                "Account schedulable toggle failed | account=%s schedulable=%s",
                state.account_id,
                schedulable,
            )
        return action

    # ---------- manual actions ----------

    def evict_account(self, account_id: str) -> AccountHealthAction:
        return self._manual_toggle(account_id, schedulable=False)

    def rejoin_account(self, account_id: str) -> AccountHealthAction:
        return self._manual_toggle(account_id, schedulable=True)

    def _manual_toggle(self, account_id: str, *, schedulable: bool) -> AccountHealthAction:
        with self._operation_lock:
            moment = datetime.now(timezone.utc)
            states = {
                state.account_id: state
                for state in self.store.list_account_health_states()
            }
            state = states.get(str(account_id)) or AccountHealthState(
                account_id=str(account_id)
            )
            action = self._apply_schedulable(
                state,
                state.account_name,
                schedulable=schedulable,
                moment=moment,
                source="manual",
            )
            state.updated_at = moment
            self.store.upsert_account_health_state(state)
            run = AccountHealthRun(
                run_id=uuid.uuid4().hex,
                trigger="manual",
                actions=[action],
                evicted_count=1 if action.action == "evict" and action.status == "done" else 0,
                rejoined_count=1 if action.action == "rejoin" and action.status == "done" else 0,
                failed_count=1 if action.status == "failed" else 0,
                created_at=moment,
            )
            self.store.save_account_health_run(run)
            if action.status == "failed":
                raise AccountHealthError(action.reason or "schedulable toggle failed")
            return action

    def _is_alert_whitelisted(
        self,
        *,
        account_id: str,
        account_name: str,
        settings: AccountHealthRuntimeSettings,
    ) -> bool:
        entries = {entry.strip() for entry in settings.alert_whitelist if entry.strip()}
        return bool(entries) and (
            account_id in entries or (account_name and account_name in entries)
        )

    def _sample_for_state(
        self, state: AccountHealthState, moment: datetime, *, whitelisted: bool
    ) -> OperationalMetricSample:
        value = 0.0 if whitelisted else (1.0 if state.evicted else 0.0)
        return OperationalMetricSample(
            metric_key=ACCOUNT_EVICTED_METRIC_KEY,
            value=value,
            scope_key=f"account:{state.account_id}",
            scope_label=state.account_name or state.account_id,
            observed_at=moment,
            collected_at=moment,
            snapshot={
                "account_id": state.account_id,
                "account_name": state.account_name,
                "health": state.health,
                "classification": state.classification,
                "evicted": state.evicted,
                "alert_whitelisted": whitelisted,
                "availability_status": state.last_availability_status,
                "last_error": state.last_error,
                "consecutive_failures": state.consecutive_failures,
            },
        )
