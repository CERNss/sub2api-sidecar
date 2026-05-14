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
from app.services.notification_collector import (
    CollectorRegistry,
    KNOWN_SIGNAL_KEYS,
    Sub2APINotificationCollectors,
    default_registry,
    sub2api_registry,
)
from app.services.notification_evaluator import evaluate_rule


AUTH_PAYLOAD = {"username": "admin", "password": "test-admin-pass"}


def login(client) -> None:
    response = client.post("/auth/login", json=AUTH_PAYLOAD)
    assert response.status_code == 200


def _rule(
    *,
    rule_id: str = "r1",
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
        signalKey="account_invalid",
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
    service.collectors.register("account_invalid", lambda rule: CollectorSample(value=99))

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
    called = {"count": 0}

    def collector(rule):
        called["count"] += 1
        return CollectorSample(value=99)

    service.collectors.register("account_invalid", collector)
    outcomes = service.tick()

    assert outcomes == []
    assert called["count"] == 0


def test_tick_respects_read_interval(client) -> None:
    login(client)
    settings = _settings([_rule(threshold="10", read_interval_minutes=10, for_minutes=0)])
    main.get_flow_store().save_notification_settings(settings)

    service = main.get_notification_service()
    service.collectors.register("account_invalid", lambda rule: CollectorSample(value=1))

    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    service.tick(now=now)

    with patch.object(requests.Session, "request", side_effect=AssertionError("should not send")):
        outcomes = service.tick(now=now + timedelta(minutes=3))

    assert outcomes == []


def test_evaluate_once_returns_no_data_when_collector_has_no_data(client) -> None:
    login(client)
    settings = _settings([_rule(threshold="10", for_minutes=0)])
    main.get_flow_store().save_notification_settings(settings)
    main.get_notification_service().collectors.register("account_invalid", lambda rule: None)

    outcome = main.get_notification_service().evaluate_once("r1")

    assert outcome.decision.action == NotificationRuleAction.no_data
    assert "returned no data" in outcome.decision.next_state.last_error
    assert outcome.deliveries == []


# --- POST /notifications/evaluate ---


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
    main.get_notification_service().collectors.register(
        "account_invalid", lambda rule: CollectorSample(value=20)
    )

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
