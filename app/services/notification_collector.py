from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

from app.clients.sub2api import Sub2APIClient
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
    "account_capacity_full",
    "group_capacity_full",
)

UNIMPLEMENTED_REASON = (
    "collector for signal '{signal_key}' is not implemented yet; "
    "register a real collector to enable evaluation"
)
LOCAL_TIMEZONE = "Asia/Shanghai"


class CollectorNoData(RuntimeError):
    """Raised when a collector ran successfully but upstream data lacks the required fields."""


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
        except CollectorNoData as exc:
            return None, str(exc)
        except Exception as exc:
            return None, f"collector for '{rule.signal_key}' raised: {exc}"
        if sample is None:
            return None, f"collector for '{rule.signal_key}' returned no data"
        return sample, None

    def is_registered(self, signal_key: str) -> bool:
        return signal_key in self._collectors


def default_registry() -> CollectorRegistry:
    return CollectorRegistry()


def sub2api_registry(client: Sub2APIClient) -> CollectorRegistry:
    registry = CollectorRegistry()
    collectors = Sub2APINotificationCollectors(client)
    registry.register("account_invalid", collectors.account_invalid)
    registry.register("account_rate_limited", collectors.account_rate_limited)
    registry.register("account_reauth_needed", collectors.account_reauth_needed)
    registry.register("account_capacity_high", collectors.account_capacity_high)
    registry.register("account_capacity_full", collectors.account_capacity_full)
    registry.register("group_capacity_full", collectors.group_capacity_full)
    registry.register("account_quota_low", collectors.account_quota_low)
    registry.register("platform_key_health", collectors.platform_key_health)
    registry.register("platform_key_quota", collectors.platform_key_quota)
    registry.register("platform_key_expiry", collectors.platform_key_expiry)
    registry.register("subscription_usage", collectors.subscription_usage)
    registry.register("api_key_usage_spike", collectors.admin_usage_anomaly)
    registry.register("user_balance_low", collectors.user_balance_low)
    registry.register("user_api_key_state", collectors.user_api_key_state)
    registry.register("user_usage_summary", collectors.user_usage_summary)
    registry.register("user_subscription", collectors.subscription_usage)
    registry.register("admin_usage_anomaly", collectors.admin_usage_anomaly)
    registry.register("admin_dashboard", collectors.admin_ops_alert)
    registry.register("admin_group_channel", collectors.admin_group_channel)
    registry.register("admin_payment", collectors.admin_payment)
    registry.register("admin_ops_alert", collectors.admin_ops_alert)
    return registry


class Sub2APINotificationCollectors:
    def __init__(self, client: Sub2APIClient) -> None:
        self.client = client

    def account_invalid(self, _: NotificationRule) -> CollectorSample | None:
        accounts = self._accounts()
        invalid = [
            account
            for account in accounts
            if _status_key(account.get("availability_status")) in {
                "banned",
                "disabled",
                "expired",
                "invalid",
                "unavailable",
            }
            or account.get("is_available") is False
        ]
        return _count_sample(invalid, accounts, "invalid_accounts")

    def account_rate_limited(self, _: NotificationRule) -> CollectorSample | None:
        accounts = self._accounts()
        limited = [
            account
            for account in accounts
            if account.get("rate_limited") is True
            or _status_key(account.get("availability_status"))
            in {"rate_limited", "ratelimited", "overload", "overloaded"}
        ]
        return _count_sample(limited, accounts, "rate_limited_accounts")

    def account_reauth_needed(self, _: NotificationRule) -> CollectorSample | None:
        accounts = self._accounts()
        needs_reauth = [
            account
            for account in accounts
            if _status_key(account.get("availability_status"))
            in {"needs_reauth", "needs_verify", "banned"}
        ]
        return _count_sample(needs_reauth, accounts, "reauth_accounts")

    def account_capacity_high(self, _: NotificationRule) -> CollectorSample | None:
        accounts = self._accounts()
        percentages: list[float] = []
        for account in accounts:
            capacity = _account_capacity(account)
            if capacity is not None:
                used, limit = capacity
                percentages.append(used / limit * 100)
        if not percentages:
            raise CollectorNoData("account capacity fields are missing from account data")
        return CollectorSample(
            value=max(percentages),
            snapshot={
                "max_capacity_percent": max(percentages),
                "account_count": len(accounts),
            },
        )

    def account_capacity_full(self, _: NotificationRule) -> CollectorSample | None:
        accounts = self._accounts()
        measurable: list[tuple[dict, float, float]] = []
        full: list[tuple[dict, float, float]] = []
        for account in accounts:
            capacity = _account_capacity(account)
            if capacity is None:
                continue
            used, limit = capacity
            measurable.append((account, used, limit))
            if _capacity_is_full(used, limit):
                full.append((account, used, limit))
        if not measurable:
            raise CollectorNoData("account capacity fields are missing from account data")
        return _capacity_count_sample(full, len(measurable), "full_accounts")

    def group_capacity_full(self, _: NotificationRule) -> CollectorSample | None:
        groups: dict[str, dict] = {}
        for account in self._accounts():
            capacity = _account_capacity(account)
            if capacity is None:
                continue
            used, limit = capacity
            for raw_group_id in account.get("group_ids") or []:
                group_key = _id_key(raw_group_id)
                if not group_key:
                    continue
                group = groups.setdefault(
                    group_key,
                    {
                        "id": raw_group_id,
                        "name": _group_name_for_account(account, raw_group_id),
                        "current_capacity": 0.0,
                        "capacity": 0.0,
                        "account_count": 0,
                    },
                )
                group["current_capacity"] += used
                group["capacity"] += limit
                group["account_count"] += 1
        measurable = [
            group
            for group in groups.values()
            if _number(group, "capacity") is not None and _number(group, "capacity") > 0
        ]
        if not measurable:
            raise CollectorNoData("group capacity fields are missing from grouped account data")
        full = [
            group
            for group in measurable
            if _capacity_is_full(group["current_capacity"], group["capacity"])
        ]
        return _capacity_count_sample(
            [(group, group["current_capacity"], group["capacity"]) for group in full],
            len(measurable),
            "full_groups",
        )

    def account_quota_low(self, _: NotificationRule) -> CollectorSample | None:
        accounts = self._accounts()
        values = [_quota_remaining_percent(account) for account in accounts]
        remaining = [value for value in values if value is not None]
        if not remaining:
            raise CollectorNoData("quota or account usage percentage fields are missing from account data")
        return CollectorSample(
            value=min(remaining),
            snapshot={"min_quota_remaining": min(remaining), "account_count": len(accounts)},
        )

    def platform_key_health(self, _: NotificationRule) -> CollectorSample | None:
        accounts = [
            account
            for account in self._accounts()
            if _status_key(account.get("account_type") or account.get("raw.type")) == "apikey"
        ]
        unhealthy = [
            account
            for account in accounts
            if _status_key(account.get("availability_status")) not in {"available", "active", "healthy"}
            and account.get("is_available") is not True
        ]
        return _count_sample(unhealthy, accounts, "unhealthy_platform_keys")

    def platform_key_quota(self, rule: NotificationRule) -> CollectorSample | None:
        return self.account_quota_low(rule)

    def platform_key_expiry(self, _: NotificationRule) -> CollectorSample | None:
        accounts = self._accounts()
        today = date.today()
        days: list[float] = []
        for account in accounts:
            expires = _date_value(account, "raw.expires_at", "expires_at")
            if expires is not None:
                days.append(float((expires - today).days))
        if not days:
            raise CollectorNoData("expires_at fields are missing from platform key account data")
        return CollectorSample(
            value=min(days),
            snapshot={"min_days_until_expiry": min(days), "expiring_account_count": len(days)},
        )

    def subscription_usage(self, _: NotificationRule) -> CollectorSample | None:
        stats = self._usage_for_day(date.today())
        percent = _limit_usage_percent(stats)
        if percent is not None:
            return CollectorSample(value=percent, snapshot={"usage": _usage_snapshot(stats)})
        raise CollectorNoData("subscription usage percent or non-zero usage limits are missing")

    def user_balance_low(self, _: NotificationRule) -> CollectorSample | None:
        users = self.client.list_users()
        balances = [_number(user, "balance", "raw.balance") for user in users]
        present = [balance for balance in balances if balance is not None]
        if not present:
            raise CollectorNoData("balance fields are missing from user data")
        low_users = [
            _entity_snapshot(user)
            for user in users
            if _number(user, "balance", "raw.balance") == min(present)
        ]
        return CollectorSample(
            value=min(present),
            snapshot={"min_balance": min(present), "low_users": low_users[:10]},
        )

    def user_api_key_state(self, _: NotificationRule) -> CollectorSample | None:
        users = self.client.list_users()
        unhealthy = [
            user
            for user in users
            if _status_key(user.get("status") or user.get("raw.status"))
            not in {"", "active", "enabled", "ok"}
        ]
        return _count_sample(unhealthy, users, "unhealthy_users")

    def user_usage_summary(self, _: NotificationRule) -> CollectorSample | None:
        stats = self._usage_for_day(date.today())
        value = _number(stats, "total_actual_cost", "total_cost", "total_requests")
        if value is None:
            raise CollectorNoData("usage summary fields are missing")
        return CollectorSample(value=value, snapshot={"usage": _usage_snapshot(stats)})

    def admin_usage_anomaly(self, _: NotificationRule) -> CollectorSample | None:
        today = date.today()
        current = self._usage_for_day(today)
        previous = self._usage_for_day(today - timedelta(days=1))
        current_value = _number(current, "total_actual_cost", "total_cost", "total_requests")
        previous_value = _number(previous, "total_actual_cost", "total_cost", "total_requests")
        if current_value is None or previous_value is None or previous_value <= 0:
            raise CollectorNoData("usage stats for current or previous day are missing")
        change = (current_value - previous_value) / previous_value * 100
        return CollectorSample(
            value=change,
            snapshot={
                "metric": "total_actual_cost",
                "current": current_value,
                "previous": previous_value,
                "current_date": today.isoformat(),
                "previous_date": (today - timedelta(days=1)).isoformat(),
            },
        )

    def admin_group_channel(self, _: NotificationRule) -> CollectorSample | None:
        groups = self.client.list_groups(platform="openai")
        unhealthy = [
            group
            for group in groups
            if _status_key(group.get("status") or group.get("raw.status"))
            not in {"", "active", "enabled", "ok"}
        ]
        return _count_sample(unhealthy, groups, "unhealthy_groups")

    def admin_payment(self, rule: NotificationRule) -> CollectorSample | None:
        return self.user_usage_summary(rule)

    def admin_ops_alert(self, _: NotificationRule) -> CollectorSample | None:
        accounts = self._accounts()
        groups = self.client.list_groups(platform="openai")
        problem_accounts = [
            account
            for account in accounts
            if account.get("is_available") is False
            or account.get("rate_limited") is True
            or _status_key(account.get("availability_status"))
            in {
                "banned",
                "disabled",
                "expired",
                "invalid",
                "needs_reauth",
                "needs_verify",
                "rate_limited",
                "unavailable",
            }
        ]
        problem_groups = [
            group
            for group in groups
            if _status_key(group.get("status") or group.get("raw.status"))
            not in {"", "active", "enabled", "ok"}
        ]
        return CollectorSample(
            value=float(len(problem_accounts) + len(problem_groups)),
            snapshot={
                "problem_accounts": [_entity_snapshot(item) for item in problem_accounts[:10]],
                "problem_groups": [_entity_snapshot(item) for item in problem_groups[:10]],
                "account_count": len(accounts),
                "group_count": len(groups),
            },
        )

    def _accounts(self) -> list[dict]:
        return self.client.list_openai_accounts()

    def _usage_for_day(self, day: date) -> dict:
        return self.client.get_usage_stats(
            user_id="",
            start_date=day,
            end_date=day,
            timezone_name=LOCAL_TIMEZONE,
        )


def _count_sample(matches: list[dict], source: list[dict], key: str) -> CollectorSample:
    return CollectorSample(
        value=float(len(matches)),
        snapshot={
            key: [_entity_snapshot(item) for item in matches[:10]],
            "matched_count": len(matches),
            "total_count": len(source),
        },
    )


def _capacity_count_sample(
    matches: list[tuple[dict, float, float]],
    measurable_count: int,
    key: str,
) -> CollectorSample:
    return CollectorSample(
        value=float(len(matches)),
        snapshot={
            key: [_capacity_snapshot(item, used, limit) for item, used, limit in matches[:10]],
            "matched_count": len(matches),
            "total_count": measurable_count,
        },
    )


def _entity_snapshot(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "name": item.get("name") or item.get("email") or item.get("username"),
        "status": item.get("status") or item.get("availability_status"),
        "reason": item.get("availability_reason") or item.get("last_error"),
    }


def _capacity_snapshot(item: dict, used: float, limit: float) -> dict:
    snapshot = _entity_snapshot(item)
    snapshot.update(
        {
            "current_capacity": used,
            "capacity": limit,
            "capacity_percent": used / limit * 100 if limit > 0 else None,
        }
    )
    if "account_count" in item:
        snapshot["account_count"] = item["account_count"]
    return snapshot


def _status_key(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _number(payload: object, *paths: str) -> float | None:
    for path in paths:
        current = payload
        found = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
                continue
            found = False
            break
        if not found or current is None or isinstance(current, bool):
            continue
        if isinstance(current, (int, float)):
            return float(current)
        if isinstance(current, str):
            text = current.strip().rstrip("%")
            if not text:
                continue
            try:
                return float(text)
            except ValueError:
                continue
    return None


def _account_capacity(account: dict) -> tuple[float, float] | None:
    used = _number(
        account,
        "current_concurrency",
        "current_capacity",
        "active_concurrency",
        "used_concurrency",
        "raw.current_concurrency",
        "raw.current_capacity",
        "raw.active_concurrency",
        "raw.used_concurrency",
        "raw.usage.current_concurrency",
    )
    limit = _number(
        account,
        "concurrency",
        "capacity",
        "account_capacity",
        "max_concurrency",
        "raw.concurrency",
        "raw.capacity",
        "raw.account_capacity",
        "raw.max_concurrency",
        "raw.limits.concurrency",
    )
    if used is None or limit is None or limit <= 0:
        return None
    return used, limit


def _capacity_is_full(used: float, limit: float) -> bool:
    return limit > 0 and used >= limit


def _id_key(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _group_name_for_account(account: dict, raw_group_id: object) -> str:
    group_ids = account.get("group_ids") or []
    group_names = account.get("group_names") or []
    target_key = _id_key(raw_group_id)
    for index, group_id in enumerate(group_ids):
        if _id_key(group_id) == target_key and index < len(group_names):
            name = group_names[index]
            if name:
                return str(name)
    return target_key


def _date_value(payload: object, *paths: str) -> date | None:
    for path in paths:
        current = payload
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if not current:
            continue
        text = str(current).strip()
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            continue
    return None


def _limit_usage_percent(stats: dict) -> float | None:
    total_cost = _number(stats, "total_actual_cost", "total_cost")
    limits = [
        _number(stats, "daily_limit_usd", "limits.daily_limit_usd"),
        _number(stats, "weekly_limit_usd", "limits.weekly_limit_usd"),
        _number(stats, "monthly_limit_usd", "limits.monthly_limit_usd"),
    ]
    percents = [total_cost / limit * 100 for limit in limits if total_cost is not None and limit and limit > 0]
    if percents:
        return max(percents)
    return _number(stats, "usage_percent", "used_percent", "subscription_usage_percent")


def _usage_snapshot(stats: dict) -> dict:
    return {
        "total_requests": stats.get("total_requests"),
        "total_cost": stats.get("total_cost"),
        "total_actual_cost": stats.get("total_actual_cost"),
    }


def _quota_remaining_percent(account: dict) -> float | None:
    explicit = _number(account, "quota_remaining", "raw.quota_remaining")
    if explicit is not None:
        return explicit
    used_values = [
        _number(account, "usage_5h_percent", "raw.usage_5h_percent"),
        _number(account, "usage_7d_percent", "raw.usage_7d_percent"),
        _number(account, "usage_percent", "raw.usage_percent"),
    ]
    used = [value for value in used_values if value is not None]
    if not used:
        return None
    return max(0.0, 100.0 - max(used))
