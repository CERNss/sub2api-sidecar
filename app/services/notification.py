from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.errors import ProvisioningError
from app.models.notification import (
    CollectorSample,
    NotificationDeliveryTrigger,
    NotificationMessage,
    NotificationRule,
    NotificationRuleAction,
    NotificationRuleState,
    NotificationSettings,
    NotificationSeverity,
    NotificationWebhook,
    WebhookMethod,
    RuleDecision,
    WebhookProvider,
)
from app.models.operational_data import OperationalMetricSample
from app.services.notification_delivery import (
    DeliveryOutcome,
    NotificationDeliveryService,
)
from app.services.notification_evaluator import (
    evaluate_rule,
    select_sendable_receivers,
)
from app.services.operational_data import (
    OperationalDataCollectionResult,
    OperationalDataCollector,
)
from app.stores.sqlite import SQLiteFlowStore

logger = logging.getLogger(__name__)

DEFAULT_RECEIVER_ID = "ops-default"
REDACTED_SECRET = "[redacted]"


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
        operational_data_collector: OperationalDataCollector | None = None,
    ) -> None:
        self.store = store
        self.delivery = delivery
        self.operational_data_collector = operational_data_collector
        self.last_collection_result: OperationalDataCollectionResult | None = None

    def load_config(self) -> NotificationSettings:
        stored = self.store.get_notification_settings()
        if stored is None:
            return _default_settings()
        return self._normalize_methods(stored)

    def save_config(self, settings: NotificationSettings) -> NotificationSettings:
        settings = self._preserve_redacted_secrets(settings)
        settings = self._normalize_methods(settings)
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
        moment = now or datetime.now(timezone.utc)
        self.refresh_samples(now=moment)
        return self._evaluate(rule, config, now=moment, persist=persist)

    def tick(self, *, now: datetime | None = None) -> list[RuleEvaluationOutcome]:
        config = self.load_config()
        moment = now or datetime.now(timezone.utc)
        self.refresh_samples(now=moment)
        outcomes: list[RuleEvaluationOutcome] = []
        for rule in config.rules:
            if not rule.enabled:
                continue
            if not self._should_evaluate_now(rule, moment):
                continue
            outcomes.append(self._evaluate(rule, config, now=moment, persist=True))
        return outcomes

    def refresh_samples(self, *, now: datetime | None = None) -> None:
        if self.operational_data_collector is None:
            return
        if not self.operational_data_runtime_settings().enabled:
            return
        self.last_collection_result = self.operational_data_collector.collect(now=now)

    def operational_data_runtime_settings(self):
        stored = self.store.get_operational_data_runtime_settings()
        if stored is not None:
            return stored
        from app.models.operational_data import OperationalDataRuntimeSettings

        return OperationalDataRuntimeSettings()

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
        sample, reason = self._sample_for_rule(rule, now=moment)
        prior_state = self.store.get_notification_rule_state(rule.id)
        decision = evaluate_rule(
            rule,
            sample,
            prior_state,
            moment,
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

        return RuleEvaluationOutcome(rule=rule, decision=decision, deliveries=deliveries)

    def _sample_for_rule(
        self, rule: NotificationRule, *, now: datetime
    ) -> tuple[CollectorSample | None, str | None]:
        sample_record = self.store.get_latest_operational_metric_sample(rule.signal_key)
        if sample_record is None:
            return None, f"no local sample found for signal '{rule.signal_key}'"
        if self._sample_is_expired(sample_record, now=now):
            return None, (
                f"latest local sample for signal '{rule.signal_key}' is expired "
                f"(observed_at={sample_record.observed_at.isoformat()})"
            )
        return sample_record.collector_sample(), None

    def _sample_is_expired(
        self, sample: OperationalMetricSample, *, now: datetime
    ) -> bool:
        expiration = self.operational_data_runtime_settings().expiration
        if expiration is None:
            return False
        observed_at = _ensure_aware(sample.observed_at)
        moment = _ensure_aware(now)
        return moment - observed_at > timedelta(seconds=expiration)

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
            rule_config=_rule_payload_template(rule),
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

    def _preserve_redacted_secrets(
        self, settings: NotificationSettings
    ) -> NotificationSettings:
        if not any(receiver.secret == REDACTED_SECRET for receiver in settings.webhooks):
            return settings
        stored = self.store.get_notification_settings()
        stored_by_id = {receiver.id: receiver for receiver in stored.webhooks} if stored else {}
        webhooks = []
        changed = False
        for receiver in settings.webhooks:
            if receiver.secret != REDACTED_SECRET:
                webhooks.append(receiver)
                continue
            stored_secret = stored_by_id.get(receiver.id).secret if receiver.id in stored_by_id else ""
            webhooks.append(receiver.model_copy(update={"secret": stored_secret}))
            changed = True
        if not changed:
            return settings
        return settings.model_copy(update={"webhooks": webhooks})

    def _normalize_methods(self, settings: NotificationSettings) -> NotificationSettings:
        webhooks = []
        changed = False
        for receiver in settings.webhooks:
            if receiver.provider == WebhookProvider.generic:
                webhooks.append(receiver)
                continue
            if receiver.method == WebhookMethod.post:
                webhooks.append(receiver)
                continue
            webhooks.append(receiver.model_copy(update={"method": WebhookMethod.post}))
            changed = True
        if not changed:
            return settings
        return settings.model_copy(update={"webhooks": webhooks})


def _default_receiver() -> NotificationWebhook:
    return NotificationWebhook(
        id=DEFAULT_RECEIVER_ID,
        name="Ops Webhook",
        enabled=False,
        provider=WebhookProvider.generic,
        method=WebhookMethod.post,
        url="",
        secret="",
    )


def _default_settings() -> NotificationSettings:
    return NotificationSettings(webhooks=[_default_receiver()], rules=[])


def redact_settings(settings: NotificationSettings) -> dict[str, Any]:
    payload = settings.model_dump(by_alias=True)
    for receiver in payload.get("webhooks", []):
        if receiver.get("secret"):
            receiver["secret"] = REDACTED_SECRET
    return payload


def _rule_payload_template(rule: NotificationRule) -> dict[str, Any]:
    return {
        "name": rule.name,
        "enabled": rule.enabled,
        "signalKey": rule.signal_key,
        "severity": rule.severity.value,
        "operator": rule.operator.value,
        "threshold": rule.threshold,
        "thresholdUnit": rule.threshold_unit,
        "readIntervalMinutes": rule.read_interval_minutes,
        "forMinutes": rule.for_minutes,
        "cooldownMinutes": rule.cooldown_minutes,
        "includeResolved": rule.include_resolved,
        "includeSnapshot": rule.include_snapshot,
    }


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


__all__ = [
    "NotificationConfigError",
    "NotificationService",
    "NotificationTestError",
    "RuleEvaluationOutcome",
    "redact_settings",
]
