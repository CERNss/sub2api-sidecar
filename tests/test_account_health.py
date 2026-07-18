from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.clients.sub2api import Sub2APIError
from app.models.account_health import AccountHealthRuntimeSettings
from app.services.account_health import (
    ACCOUNT_EVICTED_METRIC_KEY,
    AccountHealthError,
    AccountHealthService,
)
from app.stores.postgres import PostgresFlowStore


class FakeAccountClient:
    def __init__(self) -> None:
        self.accounts: list[dict] = []
        self.schedulable_calls: list[tuple[str, bool]] = []
        self.test_calls: list[str] = []
        self.fail_toggle_for: set[str] = set()

    def list_openai_accounts(self) -> list[dict]:
        return [dict(account) for account in self.accounts]

    def set_account_schedulable(self, account_id, schedulable: bool) -> dict:
        key = str(account_id)
        if key in self.fail_toggle_for:
            raise Sub2APIError("toggle rejected")
        self.schedulable_calls.append((key, schedulable))
        for account in self.accounts:
            if str(account.get("id")) == key:
                account["schedulable"] = schedulable
        return {"account_id": key, "schedulable": schedulable}

    def test_account(self, account_id) -> dict:
        self.test_calls.append(str(account_id))
        return {"success": True}


def _acct(
    account_id: int,
    availability: str = "available",
    *,
    schedulable: bool = True,
    status: str = "active",
) -> dict:
    return {
        "id": account_id,
        "name": f"acct-{account_id}",
        "status": status,
        "schedulable": schedulable,
        "availability_status": availability,
        "last_error": "boom" if availability not in ("available", "unknown") else None,
    }


def _service(app_env, client, settings) -> tuple[AccountHealthService, PostgresFlowStore]:
    store = PostgresFlowStore(app_env["database_url"])
    return (
        AccountHealthService(
            client=client, store=store, settings_provider=lambda: settings
        ),
        store,
    )


def test_parser_marks_schedulable_only_unavailability(app_env) -> None:
    from app.clients.sub2api import Sub2APIClient
    from app.config import Sub2APIProvisioningDefaults

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )
    # Unscheduled with no fault signal: the reconciler must be able to tell this
    # (possibly our own eviction) apart from a genuine fault.
    parsed = client._extract_account_availability({"schedulable": False})
    assert parsed["availability_status"] == "unavailable"
    assert parsed["availability_from_schedulable_only"] is True

    parsed = client._extract_account_availability(
        {"schedulable": False, "is_banned": True}
    )
    assert parsed["availability_status"] == "banned"
    assert parsed["availability_from_schedulable_only"] is False


def test_evicted_account_rejoins_despite_schedulable_only_unavailability(app_env) -> None:
    # Regression for the structural deadlock: after our eviction the parser
    # reports "unavailable" purely because schedulable=false; the reconciler must
    # see through its own taint and rejoin once the real fault is gone.
    fake = FakeAccountClient()
    fake.accounts = [_acct(1, "rate_limited")]
    settings = AccountHealthRuntimeSettings(failure_threshold=2, recovery_threshold=2)
    service, store = _service(app_env, fake, settings)

    service.reconcile_once()
    service.reconcile_once()
    assert ("1", False) in fake.schedulable_calls

    # The rate limit clears; from now on the account looks like what the real
    # parser produces for an evicted-but-healthy account.
    fake.accounts[0].update(
        {
            "availability_status": "unavailable",
            "availability_from_schedulable_only": True,
            "last_error": None,
        }
    )
    service.reconcile_once()
    service.reconcile_once()

    assert ("1", True) in fake.schedulable_calls
    states = {s.account_id: s for s in store.list_account_health_states()}
    assert states["1"].evicted is False


def test_manual_eviction_is_not_auto_rejoined(app_env) -> None:
    fake = FakeAccountClient()
    fake.accounts = [_acct(1, "available")]
    settings = AccountHealthRuntimeSettings(recovery_threshold=1)
    service, store = _service(app_env, fake, settings)

    service.evict_account("1")
    fake.accounts[0].update(
        {
            "schedulable": False,
            "availability_status": "unavailable",
            "availability_from_schedulable_only": True,
        }
    )
    service.reconcile_once()
    service.reconcile_once()

    # Healthy but manually evicted: stays evicted until a manual rejoin.
    assert ("1", True) not in fake.schedulable_calls
    states = {s.account_id: s for s in store.list_account_health_states()}
    assert states["1"].evicted is True and states["1"].evicted_by == "manual"

    service.rejoin_account("1")
    states = {s.account_id: s for s in store.list_account_health_states()}
    assert states["1"].evicted is False


def test_half_executed_eviction_is_adopted_not_treated_as_admin_pause(app_env) -> None:
    fake = FakeAccountClient()
    fake.accounts = [_acct(1, "banned")]
    fake.fail_toggle_for = {"1"}
    settings = AccountHealthRuntimeSettings(failure_threshold=1)
    service, store = _service(app_env, fake, settings)

    service.reconcile_once()  # toggle raises client-side
    states = {s.account_id: s for s in store.list_account_health_states()}
    assert states["1"].evicted is False
    assert states["1"].evict_attempted_at is not None

    # ...but the upstream actually applied it before the response was lost.
    fake.accounts[0]["schedulable"] = False
    service.reconcile_once()

    states = {s.account_id: s for s in store.list_account_health_states()}
    # Adopted as our eviction (kept under management), not misread as admin pause.
    assert states["1"].evicted is True
    assert fake.schedulable_calls == []


def test_persistent_bad_account_is_evicted_after_threshold(app_env) -> None:
    fake = FakeAccountClient()
    fake.accounts = [_acct(1, "needs_reauth"), _acct(2, "available")]
    settings = AccountHealthRuntimeSettings(failure_threshold=3)
    service, store = _service(app_env, fake, settings)

    service.reconcile_once()
    service.reconcile_once()
    assert fake.schedulable_calls == []

    result = service.reconcile_once()
    assert fake.schedulable_calls == [("1", False)]
    states = {s.account_id: s for s in store.list_account_health_states()}
    assert states["1"].evicted is True
    assert states["1"].classification == "persistent"
    assert states["2"].health == "healthy" and states["2"].evicted is False
    assert result.run is not None and result.run.evicted_count == 1
    sample = store.get_latest_operational_metric_sample(
        ACCOUNT_EVICTED_METRIC_KEY, scope_key="account:1"
    )
    assert sample is not None and sample.value == 1.0


def test_transient_bad_account_auto_rejoins_after_recovery(app_env) -> None:
    fake = FakeAccountClient()
    fake.accounts = [_acct(1, "rate_limited")]
    settings = AccountHealthRuntimeSettings(failure_threshold=2, recovery_threshold=2)
    service, store = _service(app_env, fake, settings)

    service.reconcile_once()
    service.reconcile_once()
    assert ("1", False) in fake.schedulable_calls

    fake.accounts[0]["availability_status"] = "available"
    fake.accounts[0]["last_error"] = None
    service.reconcile_once()
    service.reconcile_once()

    assert ("1", True) in fake.schedulable_calls
    states = {s.account_id: s for s in store.list_account_health_states()}
    assert states["1"].evicted is False and states["1"].health == "healthy"
    sample = store.get_latest_operational_metric_sample(
        ACCOUNT_EVICTED_METRIC_KEY, scope_key="account:1"
    )
    assert sample is not None and sample.value == 0.0


def test_evicted_persistent_account_gets_recovery_tests(app_env) -> None:
    fake = FakeAccountClient()
    fake.accounts = [_acct(1, "needs_reauth")]
    settings = AccountHealthRuntimeSettings(
        failure_threshold=1, recovery_test_interval_seconds=300
    )
    service, store = _service(app_env, fake, settings)

    start = datetime.now(timezone.utc)
    service.reconcile_once(now=start)  # evicts
    assert fake.schedulable_calls == [("1", False)]
    service.reconcile_once(now=start + timedelta(seconds=60))
    assert fake.test_calls == ["1"]
    # Within the interval no further test is issued.
    service.reconcile_once(now=start + timedelta(seconds=120))
    assert fake.test_calls == ["1"]
    service.reconcile_once(now=start + timedelta(seconds=400))
    assert fake.test_calls == ["1", "1"]


def test_admin_paused_account_is_never_touched(app_env) -> None:
    fake = FakeAccountClient()
    fake.accounts = [_acct(1, "needs_reauth", schedulable=False)]
    settings = AccountHealthRuntimeSettings(failure_threshold=1)
    service, store = _service(app_env, fake, settings)

    service.reconcile_once()
    service.reconcile_once()

    # schedulable=false without a sidecar eviction record means the admin paused
    # it upstream: the reconciler must not evict or rejoin it.
    assert fake.schedulable_calls == []
    states = {s.account_id: s for s in store.list_account_health_states()}
    assert states["1"].evicted is False


def test_alert_whitelist_mutes_evicted_account_alarm(app_env) -> None:
    fake = FakeAccountClient()
    fake.accounts = [_acct(1, "banned")]
    settings = AccountHealthRuntimeSettings(
        failure_threshold=1, alert_whitelist=["acct-1"]
    )
    service, store = _service(app_env, fake, settings)

    service.reconcile_once()

    assert fake.schedulable_calls == [("1", False)]
    sample = store.get_latest_operational_metric_sample(
        ACCOUNT_EVICTED_METRIC_KEY, scope_key="account:1"
    )
    assert sample is not None and sample.value == 0.0
    assert sample.snapshot.get("alert_whitelisted") is True


def test_removed_account_state_is_cleaned_up(app_env) -> None:
    fake = FakeAccountClient()
    fake.accounts = [_acct(1, "banned")]
    settings = AccountHealthRuntimeSettings(failure_threshold=1)
    service, store = _service(app_env, fake, settings)

    service.reconcile_once()
    assert {s.account_id for s in store.list_account_health_states()} == {"1"}

    fake.accounts = []
    service.reconcile_once()
    assert store.list_account_health_states() == []
    sample = store.get_latest_operational_metric_sample(
        ACCOUNT_EVICTED_METRIC_KEY, scope_key="account:1"
    )
    assert sample is not None and sample.value == 0.0


def test_manual_evict_and_rejoin(app_env) -> None:
    fake = FakeAccountClient()
    fake.accounts = [_acct(1, "available")]
    settings = AccountHealthRuntimeSettings()
    service, store = _service(app_env, fake, settings)

    action = service.evict_account("1")
    assert action.action == "evict" and action.status == "done"
    states = {s.account_id: s for s in store.list_account_health_states()}
    assert states["1"].evicted is True

    action = service.rejoin_account("1")
    assert action.action == "rejoin" and action.status == "done"
    states = {s.account_id: s for s in store.list_account_health_states()}
    assert states["1"].evicted is False
    assert len(store.list_account_health_runs()) == 2


def test_manual_toggle_failure_raises(app_env) -> None:
    fake = FakeAccountClient()
    fake.accounts = [_acct(1, "available")]
    fake.fail_toggle_for = {"1"}
    settings = AccountHealthRuntimeSettings()
    service, store = _service(app_env, fake, settings)

    with pytest.raises(AccountHealthError):
        service.evict_account("1")


def test_failed_auto_eviction_retries_next_tick(app_env) -> None:
    fake = FakeAccountClient()
    fake.accounts = [_acct(1, "banned")]
    fake.fail_toggle_for = {"1"}
    settings = AccountHealthRuntimeSettings(failure_threshold=1)
    service, store = _service(app_env, fake, settings)

    result = service.reconcile_once()
    assert result.run is not None and result.run.failed_count == 1
    states = {s.account_id: s for s in store.list_account_health_states()}
    assert states["1"].evicted is False

    fake.fail_toggle_for = set()
    result = service.reconcile_once()
    assert result.run is not None and result.run.evicted_count == 1
    states = {s.account_id: s for s in store.list_account_health_states()}
    assert states["1"].evicted is True
