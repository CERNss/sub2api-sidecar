from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.models.group_usage import GroupUsageRefreshResult, GroupUsageSegmentRecord
from app.models.operational_data import OperationalDataSnapshot
from app.services.operational_data import SOURCE_GROUP_USAGE, SOURCE_GROUPS, SOURCE_USERS
from app.stores.postgres import PostgresFlowStore

logger = logging.getLogger(__name__)

WINDOW_DAYS = {
    "5h": 5.0 / 24.0,
    "1d": 1.0,
    "7d": 7.0,
    "30d": 30.0,
}


class GroupUsageService:
    def __init__(self, store: PostgresFlowStore) -> None:
        self.store = store

    def refresh(self, *, now: datetime | None = None) -> GroupUsageRefreshResult:
        refreshed_at = now or datetime.now(timezone.utc)
        groups_snapshot = self.store.get_latest_operational_data_snapshot(SOURCE_GROUPS)
        users_snapshot = self.store.get_latest_operational_data_snapshot(SOURCE_USERS)
        usage_snapshot = self.store.get_latest_operational_data_snapshot(SOURCE_GROUP_USAGE)
        groups = groups_snapshot.payload if groups_snapshot else []
        if not isinstance(groups, list):
            groups = []
        users = users_snapshot.payload if users_snapshot else []
        if not isinstance(users, list):
            users = []
        group_usage = usage_snapshot.payload if usage_snapshot else {}
        if not isinstance(group_usage, dict):
            group_usage = {}
        observed_at = _latest_observed_at(
            refreshed_at,
            groups_snapshot,
            users_snapshot,
            usage_snapshot,
        )
        member_counts = _member_counts_by_group(users)
        existing_by_group_id = {
            str(record.group_id): record
            for record in self.store.list_group_usage_segments(limit=100000)
        }
        records: list[GroupUsageSegmentRecord] = []
        for group in groups:
            if not isinstance(group, dict) or group.get("id") in (None, ""):
                continue
            records.append(
                self._build_record(
                    group=group,
                    group_usage=group_usage.get(str(group.get("id"))),
                    member_count=member_counts.get(str(group.get("id")), 0),
                    observed_at=observed_at,
                    refreshed_at=refreshed_at,
                    existing=existing_by_group_id.get(str(group.get("id"))),
                )
            )

        self.store.upsert_group_usage_segments(records)
        window_counts: dict[str, int] = {}
        for record in records:
            for window, value in record.usage_by_window.items():
                if value is not None:
                    window_counts[window] = window_counts.get(window, 0) + 1
        result = GroupUsageRefreshResult(
            refreshed_at=refreshed_at,
            group_count=len(records),
            window_counts=window_counts,
            records=records,
        )
        logger.info(
            "Group usage refresh completed | groups=%s windows=%s",
            result.group_count,
            result.window_counts,
        )
        return result

    def _build_record(
        self,
        *,
        group: dict[str, Any],
        group_usage: Any,
        member_count: int,
        observed_at: datetime,
        refreshed_at: datetime,
        existing: GroupUsageSegmentRecord | None,
    ) -> GroupUsageSegmentRecord:
        usage_by_window = _usage_by_window(group_usage)
        daily_average_by_window = {
            window: _daily_average(window, value)
            for window, value in usage_by_window.items()
        }
        baseline_window, baseline_daily_average = _baseline_daily_average(
            daily_average_by_window
        )
        short_term_ratio = _ratio(
            daily_average_by_window.get("5h"),
            daily_average_by_window.get("30d"),
        )
        medium_term_ratio = _ratio(
            daily_average_by_window.get("7d"),
            daily_average_by_window.get("30d"),
        )
        known_count = sum(1 for value in usage_by_window.values() if value is not None)
        positive_count = sum(
            1 for value in usage_by_window.values() if value is not None and value > 0
        )
        group_kind = group.get("group_kind", group.get("type"))
        return GroupUsageSegmentRecord(
            group_id=group.get("id"),
            group_name=str(group.get("name") or ""),
            group_kind=_optional_text(group_kind),
            platform=_optional_text(group.get("platform")),
            status=_optional_text(group.get("status")),
            is_exclusive=_optional_bool(group.get("is_exclusive")),
            is_subscription=bool(
                group.get("is_subscription")
                or str(group_kind or "").strip().lower() == "subscription"
                or group.get("subscription_id") not in (None, "")
            ),
            member_count=member_count,
            usage_by_window=usage_by_window,
            daily_average_by_window=daily_average_by_window,
            request_count_by_window=_count_by_window(group_usage, "total_requests"),
            token_count_by_window=_count_by_window(group_usage, "total_tokens"),
            account_cost_by_window=_float_by_window(group_usage, "total_account_cost"),
            source_by_window=_source_by_window(group_usage),
            baseline_window=baseline_window,
            baseline_daily_average=baseline_daily_average,
            short_term_ratio=short_term_ratio,
            medium_term_ratio=medium_term_ratio,
            known_usage_window_count=known_count,
            positive_usage_window_count=positive_count,
            observed_at=observed_at,
            refreshed_at=refreshed_at,
            created_at=existing.created_at if existing else refreshed_at,
            updated_at=refreshed_at,
        )


def _member_counts_by_group(users: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for user in users:
        if not isinstance(user, dict):
            continue
        group_id = user.get("current_group_id", user.get("group_id"))
        if group_id in (None, ""):
            continue
        counts[str(group_id)] = counts.get(str(group_id), 0) + 1
    return counts


def _usage_by_window(group_usage: Any) -> dict[str, float | None]:
    return _float_by_window(group_usage, "total_actual_cost")


def _float_by_window(source: Any, key: str) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    source = source if isinstance(source, dict) else {}
    for window in WINDOW_DAYS:
        stats = source.get(window)
        result[window] = _float_value(stats if isinstance(stats, dict) else {}, key)
    return result


def _count_by_window(source: Any, key: str) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    source = source if isinstance(source, dict) else {}
    for window in WINDOW_DAYS:
        stats = source.get(window)
        value = _float_value(stats if isinstance(stats, dict) else {}, key)
        result[window] = int(value) if value is not None else None
    return result


def _source_by_window(source: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    source = source if isinstance(source, dict) else {}
    for window in WINDOW_DAYS:
        stats = source.get(window)
        if isinstance(stats, dict) and stats.get("source"):
            result[window] = str(stats["source"])
    return result


def _float_value(stats: dict[str, Any], primary_key: str) -> float | None:
    if stats.get("error"):
        return None
    keys = (
        primary_key,
        "total_actual_cost",
        "actual_cost",
        "total_cost",
        "cost",
        "usage",
        "amount",
    )
    for key in keys:
        value = stats.get(key)
        parsed = _optional_float(value)
        if parsed is not None:
            return parsed
    return None


def _daily_average(window: str, value: float | None) -> float | None:
    if value is None:
        return None
    days = WINDOW_DAYS.get(window)
    if not days:
        return None
    return value / days


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _baseline_daily_average(
    daily_average_by_window: dict[str, float | None],
) -> tuple[str | None, float | None]:
    for window in ("30d", "7d", "1d", "5h"):
        value = daily_average_by_window.get(window)
        if value is not None and value > 0:
            return window, value
    return None, None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _latest_observed_at(
    fallback: datetime,
    *snapshots: OperationalDataSnapshot | None,
) -> datetime:
    observed = [snapshot.observed_at for snapshot in snapshots if snapshot is not None]
    return max(observed) if observed else fallback


__all__ = ["GroupUsageService"]
