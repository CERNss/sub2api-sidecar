from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.clients.sub2api import Sub2APIClient
from app.models.notification import CollectorSample
from app.models.operational_data import (
    OperationalDataSnapshot,
    OperationalMetricSample,
    OperationalDataSourceStatus,
)
from app.services.notification_collector import (
    LOCAL_TIMEZONE,
    CollectorNoData,
    _account_capacity,
    _capacity_count_sample,
    _capacity_is_full,
    _count_sample,
    _date_value,
    _entity_snapshot,
    _group_name_for_account,
    _limit_usage_percent,
    _number,
    _quota_remaining_percent,
    _status_key,
    _usage_snapshot,
)
from app.stores.sqlite import SQLiteFlowStore

logger = logging.getLogger(__name__)

SOURCE_ACCOUNTS = "accounts"
SOURCE_GROUPS = "groups"
SOURCE_USERS = "users"
SOURCE_USER_USAGE = "user_usage"
SOURCE_USER_API_KEYS = "user_api_keys"
SOURCE_USAGE_CURRENT_DAY = "usage_current_day"
SOURCE_USAGE_PREVIOUS_DAY = "usage_previous_day"
USER_USAGE_WINDOWS = ("5h", "1d", "7d", "30d")


@dataclass(frozen=True)
class OperationalDataCollectionResult:
    samples: list[OperationalMetricSample]
    source_statuses: list[OperationalDataSourceStatus]
    started_at: datetime
    finished_at: datetime
    error_message: str | None = None

    @property
    def sampled_signal_count(self) -> int:
        return len(self.samples)


class OperationalDataCollector:
    def __init__(
        self,
        *,
        client: Sub2APIClient,
        store: SQLiteFlowStore,
        timezone_name: str = LOCAL_TIMEZONE,
    ) -> None:
        self.client = client
        self.store = store
        self.timezone_name = timezone_name

    def collect(self, *, now: datetime | None = None) -> OperationalDataCollectionResult:
        started_at = now or datetime.now(timezone.utc)
        local_zone = ZoneInfo(self.timezone_name)
        local_now = started_at.astimezone(local_zone)
        local_today = local_now.date()
        errors: list[str] = []

        accounts, accounts_status = self._fetch_source(
            SOURCE_ACCOUNTS,
            self.client.list_openai_accounts,
            item_count=lambda value: len(value),
        )
        groups, groups_status = self._fetch_source(
            SOURCE_GROUPS,
            lambda: self.client.list_groups(platform="openai"),
            item_count=lambda value: len(value),
        )
        users, users_status = self._fetch_source(
            SOURCE_USERS,
            self.client.list_users,
            item_count=lambda value: len(value),
        )
        user_usage, user_usage_status = self._fetch_source(
            SOURCE_USER_USAGE,
            lambda: self._fetch_user_usage(users, local_now=local_now),
            item_count=_mapping_item_count,
        )
        user_api_keys, user_api_keys_status = self._fetch_source(
            SOURCE_USER_API_KEYS,
            lambda: self._fetch_user_api_keys(users),
            item_count=_mapping_item_count,
        )
        current_usage, current_usage_status = self._fetch_source(
            SOURCE_USAGE_CURRENT_DAY,
            lambda: self.client.get_usage_stats(
                user_id="",
                start_date=local_today,
                end_date=local_today,
                timezone_name=self.timezone_name,
            ),
            item_count=_usage_item_count,
        )
        previous_day = local_today - timedelta(days=1)
        previous_usage, previous_usage_status = self._fetch_source(
            SOURCE_USAGE_PREVIOUS_DAY,
            lambda: self.client.get_usage_stats(
                user_id="",
                start_date=previous_day,
                end_date=previous_day,
                timezone_name=self.timezone_name,
            ),
            item_count=_usage_item_count,
        )

        source_statuses = [
            accounts_status,
            groups_status,
            users_status,
            user_usage_status,
            user_api_keys_status,
            current_usage_status,
            previous_usage_status,
        ]
        errors.extend(
            f"{status.source_key}: {status.error_message}"
            for status in source_statuses
            if status.status == "failed" and status.error_message
        )

        collected_at = datetime.now(timezone.utc)
        self._save_snapshots(
            observed_at=started_at,
            collected_at=collected_at,
            accounts=accounts,
            groups=groups,
            users=users,
            user_usage=user_usage,
            user_api_keys=user_api_keys,
            current_usage=current_usage,
            previous_usage=previous_usage,
        )
        samples, derivation_errors = self._derive_samples(
            accounts=accounts,
            groups=groups,
            users=users,
            current_usage=current_usage,
            previous_usage=previous_usage,
            observed_at=started_at,
            collected_at=collected_at,
        )
        errors.extend(derivation_errors)
        self.store.save_operational_metric_samples(samples)

        finished_at = datetime.now(timezone.utc)
        return OperationalDataCollectionResult(
            samples=samples,
            source_statuses=source_statuses,
            started_at=started_at,
            finished_at=finished_at,
            error_message="; ".join(errors) if errors else None,
        )

    def _fetch_source(
        self,
        source_key: str,
        fetch: Callable[[], Any],
        *,
        item_count: Callable[[Any], int],
    ) -> tuple[Any | None, OperationalDataSourceStatus]:
        started_at = datetime.now(timezone.utc)
        try:
            value = fetch()
        except Exception as exc:
            finished_at = datetime.now(timezone.utc)
            status = OperationalDataSourceStatus(
                source_key=source_key,
                status="failed",
                started_at=started_at,
                finished_at=finished_at,
                error_message=str(exc),
                item_count=None,
                updated_at=finished_at,
            )
            self.store.save_operational_data_source_status(status)
            logger.warning(
                "Notification sample source failed | source=%s error=%s",
                source_key,
                exc,
            )
            return None, status

        finished_at = datetime.now(timezone.utc)
        status = OperationalDataSourceStatus(
            source_key=source_key,
            status="succeeded",
            started_at=started_at,
            finished_at=finished_at,
            error_message=None,
            item_count=item_count(value),
            updated_at=finished_at,
        )
        self.store.save_operational_data_source_status(status)
        return value, status

    def _fetch_user_usage(
        self,
        users: list[dict[str, Any]] | None,
        *,
        local_now: datetime,
    ) -> dict[str, dict[str, Any]]:
        if not users:
            return {}
        today_start_date, today_end_date = _user_usage_date_range("1d", local_now)
        recent_logs = self.client.list_usage_logs(
            start_date=today_start_date,
            end_date=today_end_date,
            timezone_name=self.timezone_name,
        )
        recent_logs_by_user_id = _usage_logs_by_user_id(recent_logs)
        rankings_by_window = self._fetch_user_usage_rankings(local_now=local_now)
        result: dict[str, dict[str, Any]] = {}
        for user in users:
            user_id = user.get("id")
            if user_id in (None, ""):
                continue
            usage_by_window: dict[str, Any] = {}
            for window in USER_USAGE_WINDOWS:
                try:
                    if window == "5h":
                        usage_by_window[window] = _aggregate_usage_logs(
                            recent_logs_by_user_id.get(str(user_id), []),
                            user_id=user_id,
                            window=window,
                            local_now=local_now,
                        )
                    else:
                        usage_by_window[window] = rankings_by_window.get(window, {}).get(
                            str(user_id),
                            _empty_user_usage_stats(
                                user_id=user_id,
                                window=window,
                                local_now=local_now,
                            ),
                        )
                except Exception as exc:
                    logger.warning(
                        "Operational user usage fetch failed | user_id=%s window=%s error=%s",
                        user_id,
                        window,
                        exc,
                    )
                    usage_by_window[window] = {"error": str(exc)}
            result[str(user_id)] = usage_by_window
        return result

    def _fetch_user_usage_rankings(
        self,
        *,
        local_now: datetime,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for window in ("1d", "7d", "30d"):
            start_date, end_date = _user_usage_date_range(window, local_now)
            ranking = self.client.get_user_spending_ranking(
                start_date=start_date,
                end_date=end_date,
                timezone_name=self.timezone_name,
            )
            result[window] = _ranking_usage_by_user_id(
                ranking,
                window=window,
                start_date=start_date,
                end_date=end_date,
                local_now=local_now,
            )
        return result

    def _fetch_user_api_keys(
        self,
        users: list[dict[str, Any]] | None,
    ) -> dict[str, dict[str, Any]]:
        if not users:
            return {}
        result: dict[str, dict[str, Any]] = {}
        for user in users:
            user_id = user.get("id")
            if user_id in (None, ""):
                continue
            try:
                result[str(user_id)] = self.client.get_user_api_keys(user_id)
            except Exception as exc:
                logger.warning(
                    "Operational user API key fetch failed | user_id=%s error=%s",
                    user_id,
                    exc,
                )
                result[str(user_id)] = {"items": [], "total": 0, "error": str(exc)}
        return result

    def _derive_samples(
        self,
        *,
        accounts: list[dict[str, Any]] | None,
        groups: list[dict[str, Any]] | None,
        users: list[dict[str, Any]] | None,
        current_usage: dict[str, Any] | None,
        previous_usage: dict[str, Any] | None,
        observed_at: datetime,
        collected_at: datetime,
    ) -> tuple[list[OperationalMetricSample], list[str]]:
        samples: list[OperationalMetricSample] = []
        errors: list[str] = []

        def add(metric_key: str, fn: Callable[[], CollectorSample | None]) -> None:
            try:
                sample = fn()
            except CollectorNoData:
                return
            except Exception as exc:
                logger.exception("Operational metric derivation failed | metric=%s", metric_key)
                errors.append(f"{metric_key}: {exc}")
                return
            if sample is None:
                return
            samples.append(
                OperationalMetricSample(
                    metric_key=metric_key,
                    value=sample.value,
                    observed_at=observed_at,
                    collected_at=collected_at,
                    snapshot=sample.snapshot,
                )
            )

        if accounts is not None:
            add("account_invalid", lambda: _account_invalid_sample(accounts))
            add("account_rate_limited", lambda: _account_rate_limited_sample(accounts))
            add("account_reauth_needed", lambda: _account_reauth_needed_sample(accounts))
            add("account_capacity_high", lambda: _account_capacity_high_sample(accounts))
            add("account_capacity_full", lambda: _account_capacity_full_sample(accounts))
            add("group_capacity_full", lambda: _group_capacity_full_sample(accounts))
            add("account_quota_low", lambda: _account_quota_low_sample(accounts))
            add("platform_key_health", lambda: _platform_key_health_sample(accounts))
            add("platform_key_quota", lambda: _account_quota_low_sample(accounts))
            add("platform_key_expiry", lambda: _platform_key_expiry_sample(accounts))

        if users is not None:
            add("user_balance_low", lambda: _user_balance_low_sample(users))
            add("user_api_key_state", lambda: _user_api_key_state_sample(users))

        if current_usage is not None:
            add("subscription_usage", lambda: _subscription_usage_sample(current_usage))
            add("user_subscription", lambda: _subscription_usage_sample(current_usage))
            add("user_usage_summary", lambda: _usage_summary_sample(current_usage))
            add("admin_payment", lambda: _usage_summary_sample(current_usage))

        if current_usage is not None and previous_usage is not None:
            add(
                "admin_usage_anomaly",
                lambda: _admin_usage_anomaly_sample(current_usage, previous_usage, observed_at),
            )
            add(
                "api_key_usage_spike",
                lambda: _admin_usage_anomaly_sample(current_usage, previous_usage, observed_at),
            )

        if groups is not None:
            add("admin_group_channel", lambda: _admin_group_channel_sample(groups))

        if accounts is not None and groups is not None:
            add("admin_ops_alert", lambda: _admin_ops_alert_sample(accounts, groups))
            add("admin_dashboard", lambda: _admin_ops_alert_sample(accounts, groups))

        return samples, errors

    def _save_snapshots(
        self,
        *,
        observed_at: datetime,
        collected_at: datetime,
        accounts: list[dict[str, Any]] | None,
        groups: list[dict[str, Any]] | None,
        users: list[dict[str, Any]] | None,
        user_usage: dict[str, dict[str, Any]] | None,
        user_api_keys: dict[str, dict[str, Any]] | None,
        current_usage: dict[str, Any] | None,
        previous_usage: dict[str, Any] | None,
    ) -> None:
        snapshots = {
            SOURCE_ACCOUNTS: accounts,
            SOURCE_GROUPS: groups,
            SOURCE_USERS: users,
            SOURCE_USER_USAGE: user_usage,
            SOURCE_USER_API_KEYS: user_api_keys,
            SOURCE_USAGE_CURRENT_DAY: current_usage,
            SOURCE_USAGE_PREVIOUS_DAY: previous_usage,
        }
        for source_key, payload in snapshots.items():
            if payload is None:
                continue
            self.store.save_operational_data_snapshot(
                OperationalDataSnapshot(
                    source_key=source_key,
                    observed_at=observed_at,
                    collected_at=collected_at,
                    payload=payload,
                )
            )


def _usage_item_count(value: Any) -> int:
    if isinstance(value, dict) and value:
        return 1
    return 0


def _mapping_item_count(value: Any) -> int:
    if isinstance(value, dict):
        return len(value)
    return 0


def _user_usage_date_range(window: str, local_now: datetime) -> tuple[date, date]:
    local_day = local_now.date()
    if window == "5h":
        return (local_now - timedelta(hours=5)).date(), local_day
    if window == "1d":
        return local_day, local_day
    if window == "7d":
        return local_day - timedelta(days=6), local_day
    if window == "30d":
        return local_day - timedelta(days=29), local_day
    raise ValueError(f"Unsupported user usage window: {window}")


def _usage_logs_by_user_id(logs: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in logs.get("items", []):
        if not isinstance(item, dict):
            continue
        user_id = item.get("user_id")
        if user_id in (None, ""):
            user = item.get("user")
            if isinstance(user, dict):
                user_id = user.get("id") or user.get("user_id")
        if user_id in (None, ""):
            continue
        grouped.setdefault(str(user_id), []).append(item)
    return grouped


def _ranking_usage_by_user_id(
    ranking: dict[str, Any],
    *,
    window: str,
    start_date: date,
    end_date: date,
    local_now: datetime,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    rows = ranking.get("ranking", [])
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict):
            continue
        user_id = row.get("user_id")
        if user_id in (None, ""):
            continue
        result[str(user_id)] = {
            "user_id": user_id,
            "email": row.get("email"),
            "window": window,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "window_started_at": _user_usage_window_start(window, local_now).isoformat(),
            "window_finished_at": local_now.isoformat(),
            "total_requests": int(_number(row, "requests") or 0),
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cache_tokens": 0,
            "total_tokens": int(_number(row, "tokens") or 0),
            "total_cost": _number(row, "actual_cost") or 0.0,
            "total_actual_cost": _number(row, "actual_cost") or 0.0,
            "source": "dashboard_users_ranking",
        }
    return result


def _empty_user_usage_stats(
    *,
    user_id: Any,
    window: str,
    local_now: datetime,
) -> dict[str, Any]:
    start_date, end_date = _user_usage_date_range(window, local_now)
    return {
        "user_id": user_id,
        "window": window,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "window_started_at": _user_usage_window_start(window, local_now).isoformat(),
        "window_finished_at": local_now.isoformat(),
        "total_requests": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_tokens": 0,
        "total_tokens": 0,
        "total_cost": 0.0,
        "total_actual_cost": 0.0,
        "source": "dashboard_users_ranking",
    }


def _aggregate_usage_logs(
    logs: list[dict[str, Any]],
    *,
    user_id: Any,
    window: str,
    local_now: datetime,
) -> dict[str, Any]:
    window_started_at = _user_usage_window_start(window, local_now)
    window_finished_at = local_now
    start_date, end_date = _user_usage_date_range(window, local_now)
    items = [item for item in logs if isinstance(item, dict)]
    included: list[dict[str, Any]] = []
    for item in items:
        created_at = _usage_log_created_at(item, local_now.tzinfo)
        if created_at is not None and window_started_at <= created_at <= window_finished_at:
            included.append(item)
    total_cost = sum(_number(item, "total_cost") or 0.0 for item in included)
    total_actual_cost = sum(_number(item, "actual_cost", "total_actual_cost") or 0.0 for item in included)
    total_input_tokens = sum(int(_number(item, "input_tokens") or 0) for item in included)
    total_output_tokens = sum(int(_number(item, "output_tokens") or 0) for item in included)
    total_cache_tokens = sum(
        int(_number(item, "cache_creation_tokens") or 0)
        + int(_number(item, "cache_read_tokens") or 0)
        + int(_number(item, "cache_creation_5m_tokens") or 0)
        + int(_number(item, "cache_creation_1h_tokens") or 0)
        for item in included
    )
    duration_values = [_number(item, "duration_ms") for item in included]
    present_durations = [value for value in duration_values if value is not None]
    return {
        "user_id": user_id,
        "window": window,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "window_started_at": window_started_at.isoformat(),
        "window_finished_at": window_finished_at.isoformat(),
        "total_requests": len(included),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cache_tokens": total_cache_tokens,
        "total_tokens": total_input_tokens + total_output_tokens + total_cache_tokens,
        "total_cost": total_cost,
        "total_actual_cost": total_actual_cost,
        "average_duration_ms": (
            sum(present_durations) / len(present_durations) if present_durations else 0.0
        ),
        "source_item_count": len(items),
    }


def _user_usage_window_start(window: str, local_now: datetime) -> datetime:
    if window == "5h":
        return local_now - timedelta(hours=5)
    local_day = local_now.date()
    if window == "1d":
        start_day = local_day
    elif window == "7d":
        start_day = local_day - timedelta(days=6)
    elif window == "30d":
        start_day = local_day - timedelta(days=29)
    else:
        raise ValueError(f"Unsupported user usage window: {window}")
    return datetime.combine(start_day, datetime.min.time(), tzinfo=local_now.tzinfo)


def _usage_log_created_at(item: dict[str, Any], local_tz: tzinfo | None) -> datetime | None:
    raw = item.get("created_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_tz or timezone.utc)
    return parsed.astimezone(local_tz or timezone.utc)


def _account_invalid_sample(accounts: list[dict[str, Any]]) -> CollectorSample:
    invalid = [
        account
        for account in accounts
        if _status_key(account.get("availability_status"))
        in {"banned", "disabled", "expired", "invalid", "unavailable"}
        or account.get("is_available") is False
    ]
    return _count_sample(invalid, accounts, "invalid_accounts")


def _account_rate_limited_sample(accounts: list[dict[str, Any]]) -> CollectorSample:
    limited = [
        account
        for account in accounts
        if account.get("rate_limited") is True
        or _status_key(account.get("availability_status"))
        in {"rate_limited", "ratelimited", "overload", "overloaded"}
    ]
    return _count_sample(limited, accounts, "rate_limited_accounts")


def _account_reauth_needed_sample(accounts: list[dict[str, Any]]) -> CollectorSample:
    needs_reauth = [
        account
        for account in accounts
        if _status_key(account.get("availability_status"))
        in {"needs_reauth", "needs_verify", "banned"}
    ]
    return _count_sample(needs_reauth, accounts, "reauth_accounts")


def _account_capacity_high_sample(accounts: list[dict[str, Any]]) -> CollectorSample:
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


def _account_capacity_full_sample(accounts: list[dict[str, Any]]) -> CollectorSample:
    measurable: list[tuple[dict[str, Any], float, float]] = []
    full: list[tuple[dict[str, Any], float, float]] = []
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


def _group_capacity_full_sample(accounts: list[dict[str, Any]]) -> CollectorSample:
    grouped: dict[str, dict[str, Any]] = {}
    for account in accounts:
        capacity = _account_capacity(account)
        if capacity is None:
            continue
        used, limit = capacity
        for raw_group_id in account.get("group_ids") or []:
            group_key = str(raw_group_id).strip()
            if not group_key:
                continue
            group = grouped.setdefault(
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
        for group in grouped.values()
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


def _account_quota_low_sample(accounts: list[dict[str, Any]]) -> CollectorSample:
    values = [_quota_remaining_percent(account) for account in accounts]
    remaining = [value for value in values if value is not None]
    if not remaining:
        raise CollectorNoData("quota or account usage percentage fields are missing")
    return CollectorSample(
        value=min(remaining),
        snapshot={"min_quota_remaining": min(remaining), "account_count": len(accounts)},
    )


def _platform_key_health_sample(accounts: list[dict[str, Any]]) -> CollectorSample:
    platform_keys = [
        account
        for account in accounts
        if _status_key(account.get("account_type") or account.get("raw.type")) == "apikey"
    ]
    unhealthy = [
        account
        for account in platform_keys
        if _status_key(account.get("availability_status"))
        not in {"available", "active", "healthy"}
        and account.get("is_available") is not True
    ]
    return _count_sample(unhealthy, platform_keys, "unhealthy_platform_keys")


def _platform_key_expiry_sample(accounts: list[dict[str, Any]]) -> CollectorSample:
    today = datetime.now(timezone.utc).date()
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


def _subscription_usage_sample(stats: dict[str, Any]) -> CollectorSample:
    percent = _limit_usage_percent(stats)
    if percent is None:
        raise CollectorNoData("subscription usage percent or non-zero usage limits are missing")
    return CollectorSample(value=percent, snapshot={"usage": _usage_snapshot(stats)})


def _user_balance_low_sample(users: list[dict[str, Any]]) -> CollectorSample:
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


def _user_api_key_state_sample(users: list[dict[str, Any]]) -> CollectorSample:
    unhealthy = [
        user
        for user in users
        if _status_key(user.get("status") or user.get("raw.status"))
        not in {"", "active", "enabled", "ok"}
    ]
    return _count_sample(unhealthy, users, "unhealthy_users")


def _usage_summary_sample(stats: dict[str, Any]) -> CollectorSample:
    value = _number(stats, "total_actual_cost", "total_cost", "total_requests")
    if value is None:
        raise CollectorNoData("usage summary fields are missing")
    return CollectorSample(value=value, snapshot={"usage": _usage_snapshot(stats)})


def _admin_usage_anomaly_sample(
    current: dict[str, Any],
    previous: dict[str, Any],
    observed_at: datetime,
) -> CollectorSample:
    current_value = _number(current, "total_actual_cost", "total_cost", "total_requests")
    previous_value = _number(previous, "total_actual_cost", "total_cost", "total_requests")
    if current_value is None or previous_value is None or previous_value <= 0:
        raise CollectorNoData("usage stats for current or previous day are missing")
    change = (current_value - previous_value) / previous_value * 100
    local_day = observed_at.astimezone(ZoneInfo(LOCAL_TIMEZONE)).date()
    return CollectorSample(
        value=change,
        snapshot={
            "metric": "total_actual_cost",
            "current": current_value,
            "previous": previous_value,
            "current_date": local_day.isoformat(),
            "previous_date": (local_day - timedelta(days=1)).isoformat(),
        },
    )


def _admin_group_channel_sample(groups: list[dict[str, Any]]) -> CollectorSample:
    unhealthy = [
        group
        for group in groups
        if _status_key(group.get("status") or group.get("raw.status"))
        not in {"", "active", "enabled", "ok"}
    ]
    return _count_sample(unhealthy, groups, "unhealthy_groups")


def _admin_ops_alert_sample(
    accounts: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> CollectorSample:
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


__all__ = [
    "OperationalDataCollectionResult",
    "OperationalDataCollector",
    "SOURCE_ACCOUNTS",
    "SOURCE_GROUPS",
    "SOURCE_USERS",
    "SOURCE_USAGE_CURRENT_DAY",
    "SOURCE_USAGE_PREVIOUS_DAY",
]
