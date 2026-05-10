from __future__ import annotations

from typing import Callable

from app.models.notification import CollectorSample, NotificationRule

CollectorFn = Callable[[NotificationRule], CollectorSample | None]


KNOWN_SIGNAL_KEYS: tuple[str, ...] = (
    "platform_key_health",
    "platform_key_quota",
    "platform_key_expiry",
    "subscription_usage",
    "api_key_usage_spike",
    "user_balance_low",
    "user_api_key_state",
    "user_usage_summary",
    "user_subscription",
    "admin_dashboard",
    "admin_usage_anomaly",
    "admin_group_channel",
    "admin_payment",
    "admin_ops_alert",
    "account_invalid",
    "account_rate_limited",
    "account_quota_low",
    "account_reauth_needed",
    "account_capacity_high",
)

UNIMPLEMENTED_REASON = (
    "collector for signal '{signal_key}' is not implemented yet; "
    "register a real collector to enable evaluation"
)


class CollectorRegistry:
    def __init__(self) -> None:
        self._collectors: dict[str, CollectorFn] = {}

    def register(self, signal_key: str, fn: CollectorFn) -> None:
        self._collectors[signal_key] = fn

    def collect(self, rule: NotificationRule) -> tuple[CollectorSample | None, str | None]:
        fn = self._collectors.get(rule.signal_key)
        if fn is None:
            return None, UNIMPLEMENTED_REASON.format(signal_key=rule.signal_key)
        try:
            sample = fn(rule)
        except Exception as exc:
            return None, f"collector for '{rule.signal_key}' raised: {exc}"
        if sample is None:
            return None, f"collector for '{rule.signal_key}' returned no data"
        return sample, None

    def is_registered(self, signal_key: str) -> bool:
        return signal_key in self._collectors


def default_registry() -> CollectorRegistry:
    return CollectorRegistry()
