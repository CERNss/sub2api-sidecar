from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.clients.sub2api import Sub2APIError
from app.models.proxy_health import ProxyHealthRuntimeSettings
from app.services.proxy_health import PROXY_UNREACHABLE_METRIC_KEY, ProxyHealthService
from app.stores.postgres import PostgresFlowStore


class FakeProxyClient:
    """In-memory upstream double: proxies, accounts and per-proxy probe scripts."""

    def __init__(self) -> None:
        self.proxies: list[dict] = []
        self.accounts: list[dict] = []
        # proxy_id -> list of test results consumed one per call (last repeats)
        self.test_scripts: dict[str, list[dict]] = {}
        self.quality_scripts: dict[str, list[dict]] = {}
        self.moves: list[tuple[str, object]] = []
        self.fail_moves_for: set[str] = set()

    def list_proxies(self) -> list[dict]:
        return [dict(proxy) for proxy in self.proxies]

    def list_openai_accounts(self) -> list[dict]:
        return [dict(account) for account in self.accounts]

    def _next(self, scripts: dict[str, list[dict]], proxy_id: str) -> dict:
        queue = scripts.get(str(proxy_id)) or [{"success": True, "latency_ms": 100}]
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]

    def test_proxy(self, proxy_id) -> dict:
        return self._next(self.test_scripts, str(proxy_id))

    def quality_check_proxy(self, proxy_id) -> dict:
        return self._next(self.quality_scripts, str(proxy_id))

    def set_account_proxy(self, *, account: dict, proxy_id) -> dict:
        account_id = str(account.get("id"))
        if account_id in self.fail_moves_for:
            raise Sub2APIError("upstream rejected move")
        self.moves.append((account_id, str(proxy_id) if proxy_id is not None else None))
        for item in self.accounts:
            if str(item.get("id")) == account_id:
                raw = {**item["raw"]}
                if proxy_id is None:
                    raw.pop("proxy_id", None)
                else:
                    raw["proxy_id"] = int(proxy_id)
                item["raw"] = raw
        return {"account_id": account_id, "proxy_id": proxy_id, "raw": {}}


def _proxy(proxy_id: int, name: str = "", status: str = "active") -> dict:
    return {"id": proxy_id, "name": name or f"proxy-{proxy_id}", "status": status}


def _account(account_id: int, proxy_id: int | None) -> dict:
    raw = {"id": account_id, "name": f"acct-{account_id}"}
    if proxy_id is not None:
        raw["proxy_id"] = proxy_id
    return {"id": account_id, "name": f"acct-{account_id}", "raw": raw}


def _service(
    app_env: dict[str, str],
    client: FakeProxyClient,
    settings: ProxyHealthRuntimeSettings,
) -> tuple[ProxyHealthService, PostgresFlowStore]:
    store = PostgresFlowStore(app_env["database_url"])
    service = ProxyHealthService(
        client=client, store=store, settings_provider=lambda: settings
    )
    return service, store


def _ok(latency: float = 100.0) -> dict:
    return {"success": True, "latency_ms": latency}


def _fail(message: str = "connect timeout") -> dict:
    return {"success": False, "message": message}


def _quality(passing: bool = True) -> dict:
    return {
        "score": 90 if passing else 40,
        "grade": "A" if passing else "D",
        "summary": "ok" if passing else "openai failed",
        "items": [
            {"target": "base_connectivity", "status": "pass"},
            {"target": "openai", "status": "pass" if passing else "fail"},
            {"target": "anthropic", "status": "pass"},
        ],
    }


def test_probe_marks_proxy_dead_after_threshold_and_evacuates(app_env) -> None:
    fake = FakeProxyClient()
    fake.proxies = [_proxy(1), _proxy(2)]
    fake.accounts = [
        _account(11, 1),
        _account(12, 1),
        _account(13, 2),
        _account(14, None),  # direct account must never move
    ]
    fake.test_scripts = {"1": [_fail()], "2": [_ok()]}
    fake.quality_scripts = {"1": [_quality(True)], "2": [_quality(True)]}
    settings = ProxyHealthRuntimeSettings(failure_threshold=3, recovery_threshold=2)
    service, store = _service(app_env, fake, settings)

    service.probe_once()
    service.probe_once()
    states = {s.proxy_id: s for s in store.list_proxy_health_states()}
    assert states["1"].health == "unknown"
    assert states["1"].consecutive_failures == 2
    assert not fake.moves

    result = service.probe_once()
    states = {s.proxy_id: s for s in store.list_proxy_health_states()}
    assert states["1"].health == "dead"
    assert states["2"].health == "healthy"
    # Accounts 11/12 evacuated to proxy 2; the direct account stays direct.
    assert sorted(fake.moves) == [("11", "2"), ("12", "2")]
    assert result.runs and result.runs[0].trigger == "proxy_dead"
    assert result.runs[0].moved_count == 2

    sample = store.get_latest_operational_metric_sample(
        PROXY_UNREACHABLE_METRIC_KEY, scope_key="proxy:1"
    )
    assert sample is not None and sample.value == 1.0


def test_probe_recovers_proxy_and_rebalances_back(app_env) -> None:
    fake = FakeProxyClient()
    fake.proxies = [_proxy(1), _proxy(2)]
    fake.accounts = [_account(11, 2), _account(12, 2), _account(13, 2), _account(14, 2)]
    fake.test_scripts = {"1": [_fail(), _fail(), _fail(), _ok()], "2": [_ok()]}
    fake.quality_scripts = {"1": [_quality(True)], "2": [_quality(True)]}
    settings = ProxyHealthRuntimeSettings(failure_threshold=3, recovery_threshold=2)
    service, store = _service(app_env, fake, settings)

    for _ in range(3):
        service.probe_once()
    states = {s.proxy_id: s for s in store.list_proxy_health_states()}
    assert states["1"].health == "dead"
    assert not fake.moves  # nothing was on proxy 1

    # Two consecutive healthy rounds -> recovered -> rebalance splits 4 accounts 2/2.
    service.probe_once()
    result = service.probe_once()
    states = {s.proxy_id: s for s in store.list_proxy_health_states()}
    assert states["1"].health == "healthy"
    assert result.runs and result.runs[0].trigger == "proxy_recovered"
    assert len(fake.moves) == 2
    assert all(target == "1" for _, target in fake.moves)

    sample = store.get_latest_operational_metric_sample(
        PROXY_UNREACHABLE_METRIC_KEY, scope_key="proxy:1"
    )
    assert sample is not None and sample.value == 0.0


def test_probe_kills_proxy_on_latency_threshold(app_env) -> None:
    fake = FakeProxyClient()
    fake.proxies = [_proxy(1)]
    fake.test_scripts = {"1": [_ok(latency=25_000)]}
    fake.quality_scripts = {"1": [_quality(True)]}
    settings = ProxyHealthRuntimeSettings(
        failure_threshold=2, latency_threshold_ms=10_000, auto_move_enabled=False
    )
    service, store = _service(app_env, fake, settings)

    service.probe_once()
    service.probe_once()
    states = {s.proxy_id: s for s in store.list_proxy_health_states()}
    assert states["1"].health == "dead"
    assert "exceeds threshold" in (states["1"].last_probe_error or "")


def test_probe_kills_proxy_when_critical_target_fails(app_env) -> None:
    # Connectivity fine, but the openai quality item fails: stricter-than-upstream
    # verdict must count the round as unhealthy.
    fake = FakeProxyClient()
    fake.proxies = [_proxy(1)]
    fake.test_scripts = {"1": [_ok()]}
    fake.quality_scripts = {"1": [_quality(False)]}
    settings = ProxyHealthRuntimeSettings(failure_threshold=2, auto_move_enabled=False)
    service, store = _service(app_env, fake, settings)

    service.probe_once()
    service.probe_once()
    states = {s.proxy_id: s for s in store.list_proxy_health_states()}
    assert states["1"].health == "dead"
    assert states["1"].failing_critical_targets == ["openai"]


def test_rebalance_evenly_splits_with_minimal_moves(app_env) -> None:
    fake = FakeProxyClient()
    fake.proxies = [_proxy(1), _proxy(2), _proxy(3)]
    fake.accounts = [
        _account(11, 1),
        _account(12, 1),
        _account(13, 1),
        _account(14, 1),
        _account(15, 1),
        _account(16, 2),
        _account(17, None),
    ]
    settings = ProxyHealthRuntimeSettings()
    service, store = _service(app_env, fake, settings)

    run = service.rebalance(trigger="manual")

    # 6 proxied accounts over 3 proxies -> 2 each; proxy 1 keeps 2, sheds 3.
    assert run.status == "completed"
    assert run.moved_count == 3
    counts: dict[str, int] = {}
    for account in fake.accounts:
        proxy_id = account["raw"].get("proxy_id")
        if proxy_id is not None:
            counts[str(proxy_id)] = counts.get(str(proxy_id), 0) + 1
    assert counts == {"1": 2, "2": 2, "3": 2}
    # The direct account still has no proxy.
    assert "proxy_id" not in fake.accounts[6]["raw"]


def test_rebalance_dry_run_moves_nothing(app_env) -> None:
    fake = FakeProxyClient()
    fake.proxies = [_proxy(1), _proxy(2)]
    fake.accounts = [_account(11, 1), _account(12, 1)]
    settings = ProxyHealthRuntimeSettings()
    service, store = _service(app_env, fake, settings)

    run = service.rebalance(trigger="manual", dry_run=True)

    assert run.dry_run is True
    assert not fake.moves
    assert all(move.status == "planned" for move in run.moves)
    assert store.get_proxy_health_run(run.run_id) is not None


def test_rebalance_records_partial_failures(app_env) -> None:
    fake = FakeProxyClient()
    fake.proxies = [_proxy(1), _proxy(2)]
    fake.accounts = [
        _account(11, 1),
        _account(12, 1),
        _account(13, 1),
        _account(14, 1),
    ]
    fake.fail_moves_for = {"13"}
    settings = ProxyHealthRuntimeSettings()
    service, store = _service(app_env, fake, settings)

    run = service.rebalance(trigger="manual")

    assert run.status == "partial_failed"
    assert run.failed_count == 1
    failed = [move for move in run.moves if move.status == "failed"]
    assert failed and failed[0].account_id == "13"


def test_rebalance_with_no_eligible_proxies_falls_back_to_direct(app_env) -> None:
    fake = FakeProxyClient()
    fake.proxies = [_proxy(1, status="disabled")]
    fake.accounts = [_account(11, 1)]
    settings = ProxyHealthRuntimeSettings()
    service, store = _service(app_env, fake, settings)

    run = service.rebalance(trigger="manual")

    # No eligible proxy left: the account falls back to direct connection and is
    # parked so a later recovery re-proxies it.
    assert run.status == "completed"
    assert run.fallback_direct is True
    assert fake.moves == [("11", None)]
    assert {p.account_id for p in store.list_proxy_parked_accounts()} == {"11"}


def test_probe_cleans_up_states_for_removed_proxies(app_env) -> None:
    fake = FakeProxyClient()
    fake.proxies = [_proxy(1)]
    fake.test_scripts = {"1": [_ok()]}
    fake.quality_scripts = {"1": [_quality(True)]}
    settings = ProxyHealthRuntimeSettings(auto_move_enabled=False)
    service, store = _service(app_env, fake, settings)

    service.probe_once()
    assert {s.proxy_id for s in store.list_proxy_health_states()} == {"1"}

    fake.proxies = []
    service.probe_once()
    assert store.list_proxy_health_states() == []
    sample = store.get_latest_operational_metric_sample(
        PROXY_UNREACHABLE_METRIC_KEY, scope_key="proxy:1"
    )
    assert sample is not None and sample.value == 0.0


def test_all_proxies_down_falls_back_to_direct_then_recovers(app_env) -> None:
    from app.services.proxy_health import PROXY_ALL_DOWN_METRIC_KEY

    fake = FakeProxyClient()
    fake.proxies = [_proxy(1), _proxy(2)]
    fake.accounts = [_account(11, 1), _account(12, 2), _account(13, None)]
    fake.test_scripts = {"1": [_fail(), _fail(), _fail(), _ok()], "2": [_fail()]}
    fake.quality_scripts = {"1": [_quality(True)], "2": [_quality(True)]}
    settings = ProxyHealthRuntimeSettings(failure_threshold=3, recovery_threshold=1)
    service, store = _service(app_env, fake, settings)

    for _ in range(3):
        service.probe_once()

    # Total outage: both proxied accounts fell back to direct and got parked; the
    # genuinely-direct account 13 was never touched.
    states = {s.proxy_id: s for s in store.list_proxy_health_states()}
    assert states["1"].health == "dead" and states["2"].health == "dead"
    parked_ids = {p.account_id for p in store.list_proxy_parked_accounts()}
    assert parked_ids == {"11", "12"}
    assert "proxy_id" not in fake.accounts[0]["raw"]
    assert "proxy_id" not in fake.accounts[1]["raw"]
    assert ("13", "1") not in fake.moves and ("13", "2") not in fake.moves
    all_down = store.get_latest_operational_metric_sample(PROXY_ALL_DOWN_METRIC_KEY)
    assert all_down is not None and all_down.value == 1.0

    # Proxy 1 comes back: parked accounts are re-proxied and unparked; account 13
    # stays direct.
    result = service.probe_once()
    states = {s.proxy_id: s for s in store.list_proxy_health_states()}
    assert states["1"].health == "healthy"
    assert result.runs and result.runs[0].trigger == "proxy_recovered"
    assert store.list_proxy_parked_accounts() == []
    assert fake.accounts[0]["raw"].get("proxy_id") == 1
    assert fake.accounts[1]["raw"].get("proxy_id") == 1
    assert "proxy_id" not in fake.accounts[2]["raw"]
    all_down = store.get_latest_operational_metric_sample(PROXY_ALL_DOWN_METRIC_KEY)
    assert all_down is not None and all_down.value == 0.0


def test_alert_whitelist_mutes_dead_proxy_alarm_but_still_evicts(app_env) -> None:
    fake = FakeProxyClient()
    fake.proxies = [_proxy(1), _proxy(2)]
    fake.accounts = [_account(11, 1)]
    fake.test_scripts = {"1": [_fail()], "2": [_ok()]}
    fake.quality_scripts = {"1": [_quality(True)], "2": [_quality(True)]}
    settings = ProxyHealthRuntimeSettings(
        failure_threshold=2, alert_whitelist=["proxy-1"]
    )
    service, store = _service(app_env, fake, settings)

    service.probe_once()
    service.probe_once()

    states = {s.proxy_id: s for s in store.list_proxy_health_states()}
    assert states["1"].health == "dead"
    # Eviction still happened...
    assert ("11", "2") in fake.moves
    # ...but the alarm sample stays clear for the whitelisted proxy.
    sample = store.get_latest_operational_metric_sample(
        PROXY_UNREACHABLE_METRIC_KEY, scope_key="proxy:1"
    )
    assert sample is not None and sample.value == 0.0
    assert sample.snapshot.get("alert_whitelisted") is True


def test_clear_proxy_state_recovers_firing_alert(app_env) -> None:
    # Deleting a dead proxy through the panel must emit a clearing sample, or the
    # scoped proxy_unreachable rule for it would re-fire on every cooldown forever.
    fake = FakeProxyClient()
    fake.proxies = [_proxy(1)]
    fake.test_scripts = {"1": [_fail()]}
    settings = ProxyHealthRuntimeSettings(failure_threshold=1, auto_move_enabled=False)
    service, store = _service(app_env, fake, settings)

    service.probe_once()
    sample = store.get_latest_operational_metric_sample(
        PROXY_UNREACHABLE_METRIC_KEY, scope_key="proxy:1"
    )
    assert sample is not None and sample.value == 1.0

    service.clear_proxy_state("1")

    assert store.list_proxy_health_states() == []
    sample = store.get_latest_operational_metric_sample(
        PROXY_UNREACHABLE_METRIC_KEY, scope_key="proxy:1"
    )
    assert sample is not None and sample.value == 0.0
    assert sample.snapshot.get("removed") is True


def test_failed_eviction_retries_on_next_tick(app_env) -> None:
    # Eviction is level-triggered: a move that failed while the proxy is dead is
    # retried on the next probe tick instead of stranding the account.
    fake = FakeProxyClient()
    fake.proxies = [_proxy(1), _proxy(2)]
    fake.accounts = [_account(11, 1)]
    fake.test_scripts = {"1": [_fail()], "2": [_ok()]}
    fake.quality_scripts = {"1": [_quality(True)], "2": [_quality(True)]}
    fake.fail_moves_for = {"11"}
    settings = ProxyHealthRuntimeSettings(failure_threshold=1)
    service, store = _service(app_env, fake, settings)

    result = service.probe_once()
    assert result.runs and result.runs[0].failed_count == 1
    assert fake.accounts[0]["raw"].get("proxy_id") == 1

    fake.fail_moves_for = set()
    result = service.probe_once()
    assert result.runs and result.runs[0].moved_count == 1
    assert fake.accounts[0]["raw"].get("proxy_id") == 2


def test_quality_check_runs_on_interval_not_every_probe(app_env) -> None:
    fake = FakeProxyClient()
    fake.proxies = [_proxy(1)]
    fake.test_scripts = {"1": [_ok()]}
    quality_calls = {"count": 0}

    def counting_quality(proxy_id):
        quality_calls["count"] += 1
        return _quality(True)

    fake.quality_check_proxy = counting_quality  # type: ignore[method-assign]
    settings = ProxyHealthRuntimeSettings(quality_check_interval_seconds=300)
    service, store = _service(app_env, fake, settings)

    start = datetime.now(timezone.utc)
    service.probe_once(now=start)
    service.probe_once(now=start + timedelta(seconds=60))
    assert quality_calls["count"] == 1
    service.probe_once(now=start + timedelta(seconds=301))
    assert quality_calls["count"] == 2
    # Manual "批量质量检测" forces a fresh quality pass regardless of interval.
    service.probe_once(now=start + timedelta(seconds=302), force_quality=True)
    assert quality_calls["count"] == 3
