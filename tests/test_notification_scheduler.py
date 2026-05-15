from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import requests

import app.main as main
from app.models.notification import (
    CollectorSample,
    NotificationOperator,
    NotificationRule,
    NotificationRuleAction,
    NotificationRuleState,
    NotificationSettings,
    NotificationSeverity,
    NotificationWebhook,
    WebhookProvider,
)
from app.models.operational_data import (
    OperationalDataRuntimeSettings,
    OperationalDataSourceStatus,
    OperationalMetricSample,
)
from app.services.operational_data import (
    OperationalDataCollectionResult,
    OperationalDataCollector,
)
from app.services.notification_collector import (
    CollectorRegistry,
    KNOWN_SIGNAL_KEYS,
    Sub2APINotificationCollectors,
    default_registry,
    sub2api_registry,
)
from app.services.notification_evaluator import evaluate_rule
from app.services.notification_scheduler import NotificationScheduler


AUTH_PAYLOAD = {"username": "admin", "password": "test-admin-pass"}


def login(client) -> None:
    response = client.post("/auth/login", json=AUTH_PAYLOAD)
    assert response.status_code == 200


def _rule(
    *,
    rule_id: str = "r1",
    signal_key: str = "account_invalid",
    threshold: str = "10",
    operator: NotificationOperator = NotificationOperator.gte,
    for_minutes: int = 0,
    cooldown_minutes: int = 0,
    include_resolved: bool = True,
    target_ids: tuple[str, ...] = ("ops",),
    read_interval_minutes: int = 5,
) -> NotificationRule:
    return NotificationRule(
        id=rule_id,
        name=rule_id,
        signalKey=signal_key,
        severity=NotificationSeverity.warning,
        operator=operator,
        threshold=threshold,
        thresholdUnit="",
        readIntervalMinutes=read_interval_minutes,
        forMinutes=for_minutes,
        cooldownMinutes=cooldown_minutes,
        targetWebhookIds=list(target_ids),
        includeResolved=include_resolved,
        includeSnapshot=True,
    )


def _settings(rules: list[NotificationRule]) -> NotificationSettings:
    return NotificationSettings(
        webhooks=[
            NotificationWebhook(
                id="ops",
                name="Ops",
                enabled=True,
                provider=WebhookProvider.generic,
                url="https://hooks.example.com/incoming",
            )
        ],
        rules=rules,
    )


class _FakeOperationalDataCollector:
    def __init__(
        self,
        samples: list[OperationalMetricSample] | None = None,
    ) -> None:
        self.samples = samples or []
        self.calls = 0

    def collect(self, *, now=None) -> OperationalDataCollectionResult:
        self.calls += 1
        moment = now or datetime.now(timezone.utc)
        main.get_flow_store().save_operational_metric_samples(self.samples)
        status = OperationalDataSourceStatus(
            source_key="accounts",
            status="succeeded",
            started_at=moment,
            finished_at=moment,
            item_count=3,
            updated_at=moment,
        )
        main.get_flow_store().save_operational_data_source_status(status)
        return OperationalDataCollectionResult(
            samples=self.samples,
            source_statuses=[status],
            started_at=moment,
            finished_at=moment,
        )


def _install_fake_pipeline(
    samples: list[OperationalMetricSample] | None = None,
) -> _FakeOperationalDataCollector:
    existing = main.get_flow_store().get_operational_data_runtime_settings()
    main.get_flow_store().save_operational_data_runtime_settings(
        OperationalDataRuntimeSettings(
            enabled=True,
            collect_interval_seconds=existing.collect_interval_seconds if existing else 60,
            expiration=existing.expiration if existing else None,
        )
    )
    collector = _FakeOperationalDataCollector(samples)
    main.get_notification_service().operational_data_collector = collector
    return collector


def _metric_sample(
    metric_key: str = "account_invalid",
    value: float = 20,
    *,
    observed_at: datetime | None = None,
) -> OperationalMetricSample:
    moment = observed_at or datetime.now(timezone.utc)
    return OperationalMetricSample(
        metric_key=metric_key,
        value=value,
        observed_at=moment,
        collected_at=moment,
    )


class _FakeSub2APIClient:
    def list_openai_accounts(self):
        return [
            {
                "id": "acct-1",
                "name": "Key down",
                "account_type": "apikey",
                "availability_status": "disabled",
                "is_available": False,
                "current_concurrency": 4,
                "concurrency": 4,
                "quota_remaining": 40,
                "group_ids": ["g1"],
                "group_names": ["Group One"],
                "raw": {"type": "apikey", "expires_at": "2026-12-31"},
            },
            {
                "id": "acct-2",
                "name": "Rate limited",
                "account_type": "oauth",
                "availability_status": "rate_limited",
                "rate_limited": True,
                "current_concurrency": 1,
                "concurrency": 10,
                "quota_remaining": 12,
                "group_ids": ["g1"],
                "group_names": ["Group One"],
                "raw": {"type": "oauth"},
            },
            {
                "id": "acct-3",
                "name": "Needs reauth",
                "account_type": "oauth",
                "availability_status": "needs_reauth",
                "current_concurrency": 0,
                "concurrency": 5,
                "quota_remaining": 80,
                "group_ids": ["g2"],
                "group_names": ["Group Two"],
                "raw": {"type": "oauth"},
            },
        ]

    def list_users(self):
        return [
            {"id": "u1", "email": "a@example.com", "balance": 10},
            {"id": "u2", "email": "b@example.com", "balance": 3},
        ]

    def get_user_usage(self, user_id, period):
        return {"total_cost": 1.0, "period": period}

    def get_user_api_keys(self, user_id):
        return {
            "items": [
                {
                    "id": f"key-{user_id}",
                    "usage_5h": 1.0,
                    "usage_1d": 2.0,
                    "usage_7d": 3.0,
                }
            ],
            "total": 1,
        }

    def list_groups(self, platform=None):
        return [{"id": "g1", "name": "Group One", "status": "active"}]

    def get_usage_stats(self, *, user_id, start_date, end_date, timezone_name):
        if str(start_date) == "2026-05-10":
            return {"total_actual_cost": 200, "daily_limit_usd": 250}
        return {"total_actual_cost": 100, "daily_limit_usd": 250}


# --- Collector registry ---


def test_collector_registry_default_signals_are_unimplemented() -> None:
    registry = default_registry()
    sample, reason = registry.collect(_rule())
    assert sample is None
    assert reason is not None
    assert "not implemented" in reason


def test_sub2api_registry_registers_frontend_signal_collectors() -> None:
    registry = sub2api_registry(_FakeSub2APIClient())

    for signal_key in KNOWN_SIGNAL_KEYS:
        assert registry.is_registered(signal_key)


def test_sub2api_collectors_account_signals() -> None:
    collectors = Sub2APINotificationCollectors(_FakeSub2APIClient())

    assert collectors.account_invalid(_rule()).value == 1
    assert collectors.account_rate_limited(_rule()).value == 1
    assert collectors.account_reauth_needed(_rule()).value == 1
    assert collectors.account_capacity_high(_rule()).value == 100
    assert collectors.account_capacity_full(_rule()).value == 1
    assert collectors.group_capacity_full(_rule()).value == 0
    assert collectors.account_quota_low(_rule()).value == 12
    assert collectors.platform_key_health(_rule()).value == 1
    assert collectors.platform_key_expiry(_rule()).value >= 0


def test_sub2api_collectors_group_capacity_full_counts_grouped_capacity() -> None:
    class _FullGroupClient(_FakeSub2APIClient):
        def list_openai_accounts(self):
            return [
                {
                    "id": "acct-1",
                    "name": "A",
                    "current_concurrency": 4,
                    "concurrency": 4,
                    "group_ids": ["g1"],
                    "group_names": ["Group One"],
                },
                {
                    "id": "acct-2",
                    "name": "B",
                    "current_concurrency": 10,
                    "concurrency": 10,
                    "group_ids": ["g1"],
                    "group_names": ["Group One"],
                },
                {
                    "id": "acct-3",
                    "name": "C",
                    "current_concurrency": 0,
                    "concurrency": 5,
                    "group_ids": ["g2"],
                    "group_names": ["Group Two"],
                },
            ]

    sample = Sub2APINotificationCollectors(_FullGroupClient()).group_capacity_full(_rule())

    assert sample.value == 1
    assert sample.snapshot is not None
    assert sample.snapshot["full_groups"][0]["id"] == "g1"
    assert sample.snapshot["full_groups"][0]["current_capacity"] == 14
    assert sample.snapshot["full_groups"][0]["capacity"] == 14


def test_sub2api_collectors_usage_and_balance_signals() -> None:
    collectors = Sub2APINotificationCollectors(_FakeSub2APIClient())

    assert collectors.user_balance_low(_rule()).value == 3
    with patch("app.services.notification_collector.date") as fake_date:
        fake_date.today.return_value = date(2026, 5, 10)
        fake_date.fromisoformat.side_effect = date.fromisoformat
        assert collectors.subscription_usage(_rule()).value == 80
        assert collectors.admin_usage_anomaly(_rule()).value == 100


def test_collector_registry_runs_registered_collector() -> None:
    registry = CollectorRegistry()
    registry.register("account_invalid", lambda rule: CollectorSample(value=42))
    sample, reason = registry.collect(_rule())
    assert sample is not None
    assert sample.value == 42
    assert reason is None


def test_collector_registry_swallows_collector_exceptions() -> None:
    registry = CollectorRegistry()

    def boom(rule):
        raise RuntimeError("upstream timed out")

    registry.register("account_invalid", boom)
    sample, reason = registry.collect(_rule())
    assert sample is None
    assert reason is not None
    assert "raised" in reason


# --- Notification pipeline collector ---


def test_operational_data_collector_collects_sources_in_order_and_persists_samples(client) -> None:
    class _RecordingClient(_FakeSub2APIClient):
        def __init__(self) -> None:
            self.calls: list[str] = []

        def list_openai_accounts(self):
            self.calls.append("accounts")
            return super().list_openai_accounts()

        def list_groups(self, platform=None):
            self.calls.append(f"groups:{platform}")
            return super().list_groups(platform=platform)

        def list_users(self):
            self.calls.append("users")
            return super().list_users()

        def get_user_usage(self, user_id, period):
            self.calls.append(f"user_usage:{user_id}:{period}")
            return super().get_user_usage(user_id, period)

        def get_user_api_keys(self, user_id):
            self.calls.append(f"user_api_keys:{user_id}")
            return super().get_user_api_keys(user_id)

        def get_usage_stats(self, *, user_id, start_date, end_date, timezone_name):
            self.calls.append(f"usage:{start_date}")
            return super().get_usage_stats(
                user_id=user_id,
                start_date=start_date,
                end_date=end_date,
                timezone_name=timezone_name,
            )

    api_client = _RecordingClient()
    collector = OperationalDataCollector(
        client=api_client,
        store=main.get_flow_store(),
    )
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)

    result = collector.collect(now=now)

    assert api_client.calls == [
        "accounts",
        "groups:openai",
        "users",
        "user_usage:u1:5h",
        "user_usage:u1:1d",
        "user_usage:u1:7d",
        "user_usage:u1:30d",
        "user_usage:u2:5h",
        "user_usage:u2:1d",
        "user_usage:u2:7d",
        "user_usage:u2:30d",
        "user_api_keys:u1",
        "user_api_keys:u2",
        "usage:2026-05-10",
        "usage:2026-05-09",
    ]
    assert result.error_message is None
    assert result.sampled_signal_count > 1
    assert {status.source_key for status in result.source_statuses} == {
        "accounts",
        "groups",
        "users",
        "user_usage",
        "user_api_keys",
        "usage_current_day",
        "usage_previous_day",
    }
    latest = main.get_flow_store().get_latest_operational_metric_sample("account_invalid")
    assert latest is not None
    assert latest.value == 1
    snapshot = main.get_flow_store().get_latest_operational_data_snapshot("accounts")
    assert snapshot is not None
    assert isinstance(snapshot.payload, list)


def test_operational_data_collector_records_source_failures(client) -> None:
    class _FailingGroupsClient(_FakeSub2APIClient):
        def list_groups(self, platform=None):
            raise RuntimeError("groups unavailable")

    collector = OperationalDataCollector(
        client=_FailingGroupsClient(),
        store=main.get_flow_store(),
    )

    result = collector.collect(now=datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc))

    statuses = {status.source_key: status for status in result.source_statuses}
    assert statuses["groups"].status == "failed"
    assert statuses["groups"].error_message is not None
    assert "groups unavailable" in statuses["groups"].error_message
    assert result.error_message is not None
    assert main.get_flow_store().get_latest_operational_metric_sample("account_invalid")


def test_notification_refresh_samples_runs_operational_data_cleanup(client) -> None:
    login(client)
    older = datetime(2026, 5, 10, 11, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    main.get_flow_store().save_operational_metric_samples(
        [
            OperationalMetricSample(
                metric_key="account_invalid",
                value=99,
                observed_at=older,
                collected_at=older,
            )
        ]
    )
    main.get_flow_store().save_operational_data_runtime_settings(
        OperationalDataRuntimeSettings(
            enabled=True,
            collect_interval_seconds=60,
            retention_seconds=1800,
            max_storage_mb=None,
        )
    )
    service = main.get_notification_service()
    service.operational_data_collector = OperationalDataCollector(
        client=_FakeSub2APIClient(),
        store=main.get_flow_store(),
    )

    service.refresh_samples(now=now)

    latest = main.get_flow_store().get_latest_operational_metric_sample("account_invalid")
    assert latest is not None
    assert latest.value == 1


# --- Evaluator ---


def test_evaluator_holds_first_breach_when_sustained_is_required() -> None:
    rule = _rule(threshold="10", for_minutes=5)
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    decision = evaluate_rule(rule, CollectorSample(value=20), None, now)
    assert decision.action == NotificationRuleAction.hold
    assert decision.next_state.breach_started_at == now
    assert decision.next_state.is_firing is False


def test_evaluator_fires_after_sustained_window() -> None:
    rule = _rule(threshold="10", for_minutes=5)
    breach_started = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    now = breach_started + timedelta(minutes=6)
    prior = NotificationRuleState(rule_id=rule.id, breach_started_at=breach_started)
    decision = evaluate_rule(rule, CollectorSample(value=20), prior, now)
    assert decision.action == NotificationRuleAction.fire
    assert decision.next_state.is_firing is True
    assert decision.next_state.last_alert_at == now


def test_evaluator_holds_during_cooldown() -> None:
    rule = _rule(threshold="10", cooldown_minutes=15)
    last_alert = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    now = last_alert + timedelta(minutes=5)
    prior = NotificationRuleState(
        rule_id=rule.id,
        is_firing=True,
        last_alert_at=last_alert,
    )
    decision = evaluate_rule(rule, CollectorSample(value=20), prior, now)
    assert decision.action == NotificationRuleAction.hold
    assert "cooldown" in decision.reason


def test_evaluator_recovers_when_value_no_longer_breaches() -> None:
    rule = _rule(threshold="10", include_resolved=True)
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    prior = NotificationRuleState(
        rule_id=rule.id,
        is_firing=True,
        breach_started_at=now - timedelta(minutes=30),
        last_alert_at=now - timedelta(minutes=10),
    )
    decision = evaluate_rule(rule, CollectorSample(value=4), prior, now)
    assert decision.action == NotificationRuleAction.recover
    assert decision.next_state.is_firing is False
    assert decision.next_state.breach_started_at is None


def test_evaluator_recover_without_include_resolved_is_silent() -> None:
    rule = _rule(threshold="10", include_resolved=False)
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    prior = NotificationRuleState(
        rule_id=rule.id,
        is_firing=True,
        breach_started_at=now - timedelta(minutes=30),
    )
    decision = evaluate_rule(rule, CollectorSample(value=4), prior, now)
    assert decision.action == NotificationRuleAction.hold
    assert decision.next_state.is_firing is False


def test_evaluator_no_data_when_sample_missing() -> None:
    rule = _rule()
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    decision = evaluate_rule(rule, None, None, now, no_data_reason="upstream not wired")
    assert decision.action == NotificationRuleAction.no_data
    assert "not wired" in decision.next_state.last_error
    assert decision.next_state.last_value is None


# --- NotificationService.tick + evaluate_once ---


def test_tick_dispatches_fire_to_sendable_receiver(client) -> None:
    login(client)
    settings = _settings([_rule(threshold="10", for_minutes=0, read_interval_minutes=1)])
    main.get_flow_store().save_notification_settings(settings)

    service = main.get_notification_service()
    collector = _install_fake_pipeline([_metric_sample(value=99)])

    sent: list[dict[str, Any]] = []

    class _OkResp:
        status_code = 200
        text = "ok"

    def fake_request(self, method, url, data=None, headers=None, timeout=None):
        sent.append({"method": method, "url": url})
        return _OkResp()

    with patch.object(requests.Session, "request", new=fake_request):
        outcomes = service.tick()

    assert len(outcomes) == 1
    assert collector.calls == 1
    assert outcomes[0].decision.action == NotificationRuleAction.fire
    assert sent and sent[0]["url"] == "https://hooks.example.com/incoming"

    persisted = main.get_flow_store().get_notification_rule_state("r1")
    assert persisted is not None
    assert persisted.is_firing is True


def test_tick_skips_disabled_rules(client) -> None:
    login(client)
    rule = _rule(threshold="10", for_minutes=0, read_interval_minutes=1)
    rule.enabled = False
    settings = _settings([rule])
    main.get_flow_store().save_notification_settings(settings)

    service = main.get_notification_service()
    collector = _install_fake_pipeline([_metric_sample(value=99)])
    outcomes = service.tick()

    assert outcomes == []
    assert collector.calls == 1
    assert main.get_flow_store().get_notification_rule_state("r1") is None


def test_tick_respects_read_interval(client) -> None:
    login(client)
    settings = _settings([_rule(threshold="10", read_interval_minutes=10, for_minutes=0)])
    main.get_flow_store().save_notification_settings(settings)

    service = main.get_notification_service()
    collector = _install_fake_pipeline([_metric_sample(value=1)])

    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    service.tick(now=now)

    with patch.object(requests.Session, "request", side_effect=AssertionError("should not send")):
        outcomes = service.tick(now=now + timedelta(minutes=3))

    assert outcomes == []
    assert collector.calls == 2


def test_tick_refreshes_samples_once_for_multiple_rules(client) -> None:
    login(client)
    settings = _settings(
        [
            _rule(rule_id="r1", threshold="10", for_minutes=0, read_interval_minutes=1),
            _rule(rule_id="r2", threshold="10", for_minutes=0, read_interval_minutes=1),
        ]
    )
    main.get_flow_store().save_notification_settings(settings)
    collector = _install_fake_pipeline([_metric_sample(value=99)])

    class _OkResp:
        status_code = 200
        text = "ok"

    def fake_request(self, method, url, data=None, headers=None, timeout=None):
        return _OkResp()

    with patch.object(requests.Session, "request", new=fake_request):
        outcomes = main.get_notification_service().tick()

    assert collector.calls == 1
    assert [outcome.rule.id for outcome in outcomes] == ["r1", "r2"]
    assert all(outcome.decision.action == NotificationRuleAction.fire for outcome in outcomes)


def test_notification_scheduler_runs_startup_tick_and_reports_snapshot() -> None:
    class _FakeService:
        def __init__(self) -> None:
            self.calls = 0

        def tick(self, *, now=None):
            self.calls += 1
            return []

    service = _FakeService()
    scheduler = NotificationScheduler(service, cadence_seconds=60)

    scheduler.start()
    snapshot = scheduler.snapshot()
    scheduler.stop()

    assert service.calls == 1
    assert snapshot.enabled is True
    assert snapshot.cadence_seconds == 60
    assert snapshot.tick_count == 1
    assert snapshot.last_tick_started_at is not None
    assert snapshot.last_tick_error is None


def test_evaluate_once_returns_no_data_when_collector_has_no_data(client) -> None:
    login(client)
    settings = _settings([_rule(threshold="10", for_minutes=0)])
    main.get_flow_store().save_notification_settings(settings)
    _install_fake_pipeline([])

    outcome = main.get_notification_service().evaluate_once("r1")

    assert outcome.decision.action == NotificationRuleAction.no_data
    assert "no local sample found" in outcome.decision.next_state.last_error
    assert outcome.deliveries == []


def test_evaluate_once_does_not_expire_samples_when_expiration_unset(client) -> None:
    login(client)
    settings = _settings([_rule(threshold="10", for_minutes=0, target_ids=())])
    main.get_flow_store().save_notification_settings(settings)
    service = main.get_notification_service()
    _install_fake_pipeline(
        [
            _metric_sample(
                value=20,
                observed_at=datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc),
            )
        ]
    )

    outcome = service.evaluate_once(
        "r1",
        now=datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc),
    )

    assert outcome.decision.action == NotificationRuleAction.fire


def test_evaluate_once_returns_no_data_when_sample_is_expired(client) -> None:
    login(client)
    settings = _settings([_rule(threshold="10", for_minutes=0)])
    main.get_flow_store().save_notification_settings(settings)
    main.get_flow_store().save_operational_data_runtime_settings(
        OperationalDataRuntimeSettings(enabled=True, expiration=180)
    )
    service = main.get_notification_service()
    _install_fake_pipeline(
        [
            _metric_sample(
                value=20,
                observed_at=datetime(2026, 5, 10, 11, 0, tzinfo=timezone.utc),
            )
        ]
    )

    outcome = service.evaluate_once(
        "r1",
        now=datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc),
    )

    assert outcome.decision.action == NotificationRuleAction.no_data
    assert "expired" in outcome.decision.next_state.last_error


# --- POST /notifications/evaluate ---


def test_scheduler_status_endpoint_requires_auth(client) -> None:
    response = client.get("/api/operational-data/status")
    assert response.status_code == 401


def test_scheduler_status_endpoint_reports_disabled_scheduler(client) -> None:
    login(client)

    response = client.get("/api/operational-data/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["running"] is True
    assert payload["cadence_seconds"] == 60
    assert payload["collect_interval_seconds"] == 60
    assert payload["expiration"] is None
    assert payload["retention_seconds"] is None
    assert payload["max_storage_mb"] is None
    assert payload["storage_bytes"] == 0
    assert payload["tick_count"] == 0
    assert payload["source_statuses"] == []


def test_operational_data_settings_api_updates_collection_interval(client) -> None:
    login(client)

    saved = client.put(
        "/api/operational-data/settings",
        json={
            "enabled": True,
            "collect_interval_seconds": 30,
            "expiration": 180,
            "retention_seconds": 3600,
            "max_storage_mb": 128,
        },
    )
    reloaded = client.get("/api/operational-data/settings")
    status = client.get("/api/operational-data/status")

    assert saved.status_code == 200
    assert saved.json()["settings"]["enabled"] is True
    assert saved.json()["settings"]["collect_interval_seconds"] == 30
    assert saved.json()["settings"]["expiration"] == 180
    assert saved.json()["settings"]["retention_seconds"] == 3600
    assert saved.json()["settings"]["max_storage_mb"] == 128
    assert reloaded.status_code == 200
    assert reloaded.json()["settings"]["collect_interval_seconds"] == 30
    assert reloaded.json()["settings"]["retention_seconds"] == 3600
    assert reloaded.json()["settings"]["max_storage_mb"] == 128
    assert status.status_code == 200
    assert status.json()["cadence_seconds"] == 30
    assert status.json()["collect_interval_seconds"] == 30
    assert status.json()["retention_seconds"] == 3600
    assert status.json()["max_storage_mb"] == 128


def test_operational_data_settings_api_rejects_too_short_collection_interval(client) -> None:
    login(client)

    response = client.put(
        "/api/operational-data/settings",
        json={
            "enabled": True,
            "collect_interval_seconds": 0,
            "expiration": None,
            "retention_seconds": None,
            "max_storage_mb": None,
        },
    )

    assert response.status_code == 422


def test_notification_scheduler_reads_runtime_cadence_provider() -> None:
    class _FakeService:
        last_collection_result = None
        store = None

        def tick(self, *, now=None):
            return []

    scheduler = NotificationScheduler(
        _FakeService(),
        cadence_seconds=60,
        cadence_provider=lambda: 15,
    )

    snapshot = scheduler.snapshot()

    assert snapshot.cadence_seconds == 15


def test_evaluate_endpoint_requires_auth(client) -> None:
    response = client.post("/notifications/evaluate", json={"rule_id": "r1"})
    assert response.status_code == 401


def test_evaluate_endpoint_rejects_unknown_rule(client) -> None:
    login(client)
    response = client.post("/notifications/evaluate", json={"rule_id": "nope"})
    assert response.status_code == 400
    assert "No rule found" in response.json()["detail"]


def test_evaluate_endpoint_returns_decision_and_state(client) -> None:
    login(client)
    settings = _settings([_rule(threshold="10", for_minutes=0)])
    main.get_flow_store().save_notification_settings(settings)
    _install_fake_pipeline([_metric_sample(value=20)])

    sent: list[dict[str, Any]] = []

    class _OkResp:
        status_code = 200
        text = "ok"

    def fake_request(self, method, url, data=None, headers=None, timeout=None):
        sent.append({"url": url})
        return _OkResp()

    with patch.object(requests.Session, "request", new=fake_request):
        response = client.post("/notifications/evaluate", json={"rule_id": "r1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "fire"
    assert payload["state"]["is_firing"] is True
    assert payload["deliveries"][0]["status"] == "succeeded"
    assert sent
