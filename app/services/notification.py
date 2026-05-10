from __future__ import annotations

import logging
from typing import Any

from app.errors import ProvisioningError
from app.models.notification import (
    NotificationDeliveryTrigger,
    NotificationMessage,
    NotificationRoutingPolicy,
    NotificationRule,
    NotificationSettings,
    NotificationSeverity,
    NotificationWebhook,
    WebhookProvider,
)
from app.services.notification_delivery import (
    DeliveryOutcome,
    NotificationDeliveryService,
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


class NotificationService:
    def __init__(
        self,
        store: SQLiteFlowStore,
        delivery: NotificationDeliveryService,
    ) -> None:
        self.store = store
        self.delivery = delivery

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
        targets = [
            receivers_by_id[wid]
            for wid in rule.target_webhook_ids
            if wid in receivers_by_id
        ]
        sendable = [
            receiver
            for receiver in targets
            if receiver.enabled and receiver.url.strip()
        ]
        if not sendable:
            raise NotificationTestError(
                "Rule has no enabled receivers with a non-empty URL"
            )
        message = NotificationMessage(
            rule_id=rule.id,
            rule_name=rule.name or rule.signal_key,
            signal_key=rule.signal_key,
            severity=rule.severity,
            summary=f"Test delivery for rule '{rule.name or rule.signal_key}'.",
            trigger=NotificationDeliveryTrigger.test,
            snapshot={"test": True} if rule.include_snapshot else None,
        )
        outcomes = [self.delivery.deliver(receiver, message) for receiver in sendable]
        return rule, outcomes

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
