from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.errors import ProvisioningError
from app.models.notification import (
    REMOVED_ROOT_KEYS,
    REMOVED_RULE_KEYS,
    REMOVED_WEBHOOK_KEYS,
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
from app.services.notification_collector import CollectorRegistry, default_registry
from app.services.notification_delivery import (
    DeliveryOutcome,
    NotificationDeliveryService,
)
from app.services.notification_evaluator import (
    evaluate_rule,
    select_sendable_receivers,
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
        collectors: CollectorRegistry | None = None,
    ) -> None:
        self.store = store
        self.delivery = delivery
        self.collectors = collectors or default_registry()

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


def reject_removed_keys(payload: Any) -> None:
    """Raise NotificationConfigError if the inbound payload contains any field that was
    removed by the `simplify-alert-center` change. Persisted documents may still carry
    those keys (we drop them on load); but inbound API writes must come from a
    compatible client.
    """
    if not isinstance(payload, dict):
        raise NotificationConfigError("Notification settings payload must be an object")
    bad_root = [key for key in payload if key in REMOVED_ROOT_KEYS]
    if bad_root:
        raise NotificationConfigError(
            f"Unsupported field(s): {', '.join(sorted(bad_root))}. "
            "Upgrade the dashboard client; the alert center no longer accepts this field."
        )
    for webhook in payload.get("webhooks", []) or []:
        if not isinstance(webhook, dict):
            continue
        bad = [key for key in webhook if key in REMOVED_WEBHOOK_KEYS]
        if bad:
            raise NotificationConfigError(
                f"Unsupported webhook field(s): {', '.join(sorted(bad))}."
            )
    for rule in payload.get("rules", []) or []:
        if not isinstance(rule, dict):
            continue
        bad = [key for key in rule if key in REMOVED_RULE_KEYS]
        if bad:
            raise NotificationConfigError(
                f"Unsupported rule field(s): {', '.join(sorted(bad))}."
            )


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


__all__ = [
    "NotificationConfigError",
    "NotificationService",
    "NotificationTestError",
    "RuleEvaluationOutcome",
    "redact_settings",
    "reject_removed_keys",
]
