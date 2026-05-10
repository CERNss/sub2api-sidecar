from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.errors import ProvisioningError
from app.models.notification import (
    CollectorSample,
    NotificationDeliveryTrigger,
    NotificationMessage,
    NotificationRoutingPolicy,
    NotificationRule,
    NotificationRuleAction,
    NotificationRuleState,
    NotificationSettings,
    NotificationSeverity,
    NotificationWebhook,
    RuleDecision,
    WebhookProvider,
)
from app.services.notification_collector import CollectorRegistry, default_registry
from app.services.notification_delivery import (
    DeliveryOutcome,
    NotificationDeliveryService,
)
from app.services.notification_evaluator import (
    evaluate_rule,
    is_quiet_hours,
    select_sendable_receivers,
)
from app.stores.sqlite import SQLiteFlowStore

logger = logging.getLogger(__name__)

DEFAULT_RECEIVER_ID = "ops-default"
DEFAULT_RULE_SIGNAL_KEYS: tuple[str, ...] = (
    "platform_key_quota",
    "platform_key_expiry",
    "user_balance_low",
    "admin_usage_anomaly",
    "account_invalid",
    "account_rate_limited",
    "account_quota_low",
    "account_reauth_needed",
)


class NotificationConfigError(ProvisioningError):
    """Raised when notification configuration validation fails."""


class NotificationTestError(ProvisioningError):
    """Raised when a test delivery cannot be performed."""


@dataclass
class RuleEvaluationOutcome:
    rule: NotificationRule
    decision: RuleDecision
    deliveries: list[DeliveryOutcome]


class NotificationService:
    def __init__(
        self,
        store: SQLiteFlowStore,
        delivery: NotificationDeliveryService,
        collectors: CollectorRegistry | None = None,
    ) -> None:
        self.store = store
        self.delivery = delivery
        self.collectors = collectors or default_registry()

    def load_config(self) -> NotificationSettings:
        stored = self.store.get_notification_settings()
        if stored is None:
            return _default_settings()
        return _hydrate_legacy(stored)

    def save_config(self, settings: NotificationSettings) -> NotificationSettings:
        self._validate(settings)
        return self.store.save_notification_settings(settings)

    def run_test(self, rule_id: str) -> tuple[NotificationRule, list[DeliveryOutcome]]:
        config = self.load_config()
        rule = next((rule for rule in config.rules if rule.id == rule_id), None)
        if rule is None:
            raise NotificationTestError(f"No rule found with id={rule_id}")
        receivers_by_id = {receiver.id: receiver for receiver in config.webhooks}
        sendable = select_sendable_receivers(rule, receivers_by_id)
        if not sendable:
            raise NotificationTestError(
                "Rule has no enabled receivers with a non-empty URL"
            )
        message = self._build_message(rule, NotificationDeliveryTrigger.test, "Test delivery")
        outcomes = [self.delivery.deliver(receiver, message) for receiver in sendable]
        return rule, outcomes

    def evaluate_once(
        self, rule_id: str, *, now: datetime | None = None, persist: bool = True
    ) -> RuleEvaluationOutcome:
        config = self.load_config()
        rule = next((rule for rule in config.rules if rule.id == rule_id), None)
        if rule is None:
            raise NotificationTestError(f"No rule found with id={rule_id}")
        return self._evaluate(rule, config, now=now, persist=persist)

    def tick(self, *, now: datetime | None = None) -> list[RuleEvaluationOutcome]:
        config = self.load_config()
        moment = now or datetime.now(timezone.utc)
        outcomes: list[RuleEvaluationOutcome] = []
        for rule in config.rules:
            if not rule.enabled:
                continue
            if not self._should_evaluate_now(rule, moment):
                continue
            outcomes.append(self._evaluate(rule, config, now=moment, persist=True))
        return outcomes

    def _should_evaluate_now(self, rule: NotificationRule, now: datetime) -> bool:
        prior = self.store.get_notification_rule_state(rule.id)
        if prior is None or prior.last_evaluated_at is None:
            return True
        from datetime import timedelta

        elapsed = now - prior.last_evaluated_at
        return elapsed >= timedelta(minutes=max(1, rule.read_interval_minutes))

    def _evaluate(
        self,
        rule: NotificationRule,
        config: NotificationSettings,
        *,
        now: datetime | None,
        persist: bool,
    ) -> RuleEvaluationOutcome:
        moment = now or datetime.now(timezone.utc)
        sample, reason = self.collectors.collect(rule)
        prior_state = self.store.get_notification_rule_state(rule.id)
        in_quiet = is_quiet_hours(config.policy, moment)
        decision = evaluate_rule(
            rule,
            sample,
            prior_state,
            moment,
            in_quiet_hours=in_quiet,
            no_data_reason=reason,
        )
        if persist:
            self.store.upsert_notification_rule_state(decision.next_state)

        deliveries: list[DeliveryOutcome] = []
        if decision.action in {NotificationRuleAction.fire, NotificationRuleAction.recover}:
            receivers_by_id = {receiver.id: receiver for receiver in config.webhooks}
            sendable = select_sendable_receivers(rule, receivers_by_id)
            trigger = (
                NotificationDeliveryTrigger.recovery
                if decision.action == NotificationRuleAction.recover
                else NotificationDeliveryTrigger.rule
            )
            message = self._build_message(
                rule, trigger, self._summary_for(decision, rule), sample=sample
            )
            deliveries = [self.delivery.deliver(receiver, message) for receiver in sendable]
        elif decision.action == NotificationRuleAction.suppress:
            self._record_suppressed(rule, decision, config)

        return RuleEvaluationOutcome(rule=rule, decision=decision, deliveries=deliveries)

    def _record_suppressed(
        self,
        rule: NotificationRule,
        decision: RuleDecision,
        config: NotificationSettings,
    ) -> None:
        from app.models.notification import (
            NotificationDeliveryRecord,
            NotificationDeliveryStatus,
        )

        receivers_by_id = {receiver.id: receiver for receiver in config.webhooks}
        targets = select_sendable_receivers(rule, receivers_by_id)
        for receiver in targets:
            record = NotificationDeliveryRecord(
                receiver_id=receiver.id,
                rule_id=rule.id,
                provider=receiver.provider,
                severity=rule.severity,
                trigger=NotificationDeliveryTrigger.rule,
                status=NotificationDeliveryStatus.skipped,
                attempt_index=0,
                error_message="quiet hours suppressed delivery",
            )
            self.store.save_notification_delivery(record)

    def _summary_for(self, decision: RuleDecision, rule: NotificationRule) -> str:
        if decision.action == NotificationRuleAction.recover:
            return f"Rule '{rule.name or rule.signal_key}' recovered: {decision.reason}"
        return f"Rule '{rule.name or rule.signal_key}' firing: {decision.reason}"

    def _build_message(
        self,
        rule: NotificationRule,
        trigger: NotificationDeliveryTrigger,
        summary: str,
        *,
        sample: CollectorSample | None = None,
    ) -> NotificationMessage:
        snapshot: dict[str, Any] | None = None
        if rule.include_snapshot:
            snapshot = {"trigger": trigger.value}
            if sample is not None:
                snapshot["value"] = sample.value
                if sample.snapshot:
                    snapshot["data"] = sample.snapshot
        return NotificationMessage(
            rule_id=rule.id,
            rule_name=rule.name or rule.signal_key,
            signal_key=rule.signal_key,
            severity=rule.severity,
            summary=summary,
            trigger=trigger,
            snapshot=snapshot,
        )

    def _validate(self, settings: NotificationSettings) -> None:
        receiver_ids = {receiver.id for receiver in settings.webhooks}
        if len(receiver_ids) != len(settings.webhooks):
            raise NotificationConfigError("Receiver ids must be unique")
        rule_ids = {rule.id for rule in settings.rules}
        if len(rule_ids) != len(settings.rules):
            raise NotificationConfigError("Rule ids must be unique")
        for rule in settings.rules:
            unknown = [wid for wid in rule.target_webhook_ids if wid not in receiver_ids]
            if unknown:
                raise NotificationConfigError(
                    f"Rule {rule.id} targets unknown receiver ids: {', '.join(unknown)}"
                )


def _default_policy() -> NotificationRoutingPolicy:
    return NotificationRoutingPolicy()


def _default_receiver() -> NotificationWebhook:
    return NotificationWebhook(
        id=DEFAULT_RECEIVER_ID,
        name="Ops Webhook",
        enabled=False,
        provider=WebhookProvider.generic,
        url="",
        secret="",
        mention_on_failure=True,
    )


def _default_rule(signal_key: str, target_id: str) -> NotificationRule:
    return NotificationRule(
        id=f"rule-{signal_key}",
        name=signal_key,
        enabled=True,
        signal_key=signal_key,
        severity=NotificationSeverity.warning,
        target_webhook_ids=[target_id],
    )


def _default_settings() -> NotificationSettings:
    receiver = _default_receiver()
    return NotificationSettings(
        webhooks=[receiver],
        rules=[_default_rule(key, receiver.id) for key in DEFAULT_RULE_SIGNAL_KEYS],
        policy=_default_policy(),
    )


def _hydrate_legacy(settings: NotificationSettings) -> NotificationSettings:
    if settings.rules:
        return settings
    if not settings.webhooks:
        return _default_settings()
    fallback_target = settings.webhooks[0].id
    return NotificationSettings(
        webhooks=settings.webhooks,
        rules=[_default_rule(key, fallback_target) for key in DEFAULT_RULE_SIGNAL_KEYS],
        policy=settings.policy or _default_policy(),
    )


def redact_settings(settings: NotificationSettings) -> dict[str, Any]:
    payload = settings.model_dump(by_alias=True)
    for receiver in payload.get("webhooks", []):
        if receiver.get("secret"):
            receiver["secret"] = "[redacted]"
    return payload
