from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
import requests

import app.main as main
from app.models.notification import (
    CollectorSample,
    NotificationOperator,
    NotificationRoutingPolicy,
    NotificationRule,
    NotificationRuleAction,
    NotificationRuleState,
    NotificationSettings,
    NotificationSeverity,
    NotificationWebhook,
    WebhookProvider,
)
from app.services.notification import NotificationService, RuleEvaluationOutcome
from app.services.notification_collector import (
    CollectorRegistry,
    UNIMPLEMENTED_REASON,
    default_registry,
)
from app.services.notification_delivery import NotificationDeliveryService
from app.services.notification_evaluator import (
    evaluate_rule,
    is_quiet_hours,
)


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
    recovery_threshold: str = "",
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
        warningThreshold=threshold,
        recoveryThreshold=recovery_threshold,
        thresholdUnit="",
        aggregation="latest",
        readIntervalMinutes=read_interval_minutes,
        evaluationWindowMinutes=10,
        forMinutes=for_minutes,
        cooldownMinutes=cooldown_minutes,
        targetWebhookIds=list(target_ids),
        includeResolved=include_resolved,
        includeSnapshot=True,
    )


def _settings(rules: list[NotificationRule], policy: NotificationRoutingPolicy | None = None) -> NotificationSettings:
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
        policy=policy or NotificationRoutingPolicy(),
    )


# --- Collector registry ---


def test_collector_registry_default_signals_are_unimplemented() -> None:
    registry = default_registry()
    sample, reason = registry.collect(_rule())
    assert sample is None
    assert reason is not None
    assert "not implemented" in reason


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


def test_evaluator_recovers_when_threshold_crossed() -> None:
    rule = _rule(threshold="10", recovery_threshold="5", include_resolved=True)
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
    rule = _rule(threshold="10", recovery_threshold="5", include_resolved=False)
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


def test_evaluator_suppresses_during_quiet_hours() -> None:
    rule = _rule(threshold="10", for_minutes=0)
    now = datetime(2026, 5, 10, 2, 30, tzinfo=timezone.utc)
    decision = evaluate_rule(
        rule,
        CollectorSample(value=20),
        None,
        now,
        in_quiet_hours=True,
    )
    assert decision.action == NotificationRuleAction.suppress
    assert decision.next_state.is_firing is True
    assert decision.next_state.last_alert_at == now


def test_quiet_hours_handles_overnight_window() -> None:
    policy = NotificationRoutingPolicy(
        quietHoursEnabled=True,
        quietHoursStart="22:00",
        quietHoursEnd="08:00",
    )
    night = datetime(2026, 5, 10, 2, 30, tzinfo=timezone.utc)
    morning = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    assert is_quiet_hours(policy, night) is True
    assert is_quiet_hours(policy, morning) is False


def test_quiet_hours_disabled_returns_false() -> None:
    policy = NotificationRoutingPolicy(quietHoursEnabled=False)
    assert is_quiet_hours(policy, datetime(2026, 5, 10, 2, 30, tzinfo=timezone.utc)) is False


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

    sent: list[dict[str, Any]] = []
    with patch.object(requests.Session, "request", side_effect=AssertionError("should not send")):
        # Second tick three minutes later — under read_interval_minutes=10
        outcomes = service.tick(now=now + timedelta(minutes=3))

    assert outcomes == []


def test_tick_quiet_hours_writes_skipped_audit(client) -> None:
    login(client)
    rule = _rule(threshold="10", for_minutes=0, read_interval_minutes=1)
    settings = _settings(
        [rule],
        policy=NotificationRoutingPolicy(
            quietHoursEnabled=True,
            quietHoursStart="00:00",
            quietHoursEnd="23:59",
        ),
    )
    main.get_flow_store().save_notification_settings(settings)

    service = main.get_notification_service()
    service.collectors.register("account_invalid", lambda rule: CollectorSample(value=99))

    with patch.object(requests.Session, "request", side_effect=AssertionError("should not send")):
        outcomes = service.tick()

    assert len(outcomes) == 1
    assert outcomes[0].decision.action == NotificationRuleAction.suppress
    audit = main.get_flow_store().list_notification_deliveries()
    assert any(row.status.value == "skipped" for row in audit)


def test_evaluate_once_returns_no_data_when_collector_unimplemented(client) -> None:
    login(client)
    settings = _settings([_rule(threshold="10", for_minutes=0)])
    main.get_flow_store().save_notification_settings(settings)

    outcome = main.get_notification_service().evaluate_once("r1")

    assert outcome.decision.action == NotificationRuleAction.no_data
    assert "not implemented" in outcome.decision.next_state.last_error
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
