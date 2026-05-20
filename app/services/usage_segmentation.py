from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.models.operational_data import OperationalDataSnapshot
from app.models.usage_segmentation import (
    SEGMENT_LABELS,
    UsageSegment,
    UsageSegmentationRefreshResult,
    UserUsageSegmentRecord,
)
from app.services.operational_data import (
    SOURCE_USER_API_KEYS,
    SOURCE_USER_USAGE,
    SOURCE_USERS,
    USER_USAGE_WINDOWS,
)
from app.stores.postgres import PostgresFlowStore

logger = logging.getLogger(__name__)

WINDOW_DAYS = {
    "5h": 5.0 / 24.0,
    "1d": 1.0,
    "7d": 7.0,
    "30d": 30.0,
}
HEAVY_30D_DAILY = 5.0
HEAVY_7D_DAILY = 7.0
ACTIVE_30D_DAILY = 1.0
ACTIVE_7D_DAILY = 1.5
SPIKE_SHORT_TERM_RATIO = 3.0


class UsageSegmentationService:
    def __init__(self, store: PostgresFlowStore) -> None:
        self.store = store

    def refresh(self, *, now: datetime | None = None) -> UsageSegmentationRefreshResult:
        refreshed_at = now or datetime.now(timezone.utc)
        users_snapshot = self.store.get_latest_operational_data_snapshot(SOURCE_USERS)
        usage_snapshot = self.store.get_latest_operational_data_snapshot(SOURCE_USER_USAGE)
        api_keys_snapshot = self.store.get_latest_operational_data_snapshot(SOURCE_USER_API_KEYS)
        users = users_snapshot.payload if users_snapshot else []
        if not isinstance(users, list):
            users = []
        usage_payload = usage_snapshot.payload if usage_snapshot else {}
        if not isinstance(usage_payload, dict):
            usage_payload = {}
        api_keys_payload = api_keys_snapshot.payload if api_keys_snapshot else {}
        if not isinstance(api_keys_payload, dict):
            api_keys_payload = {}

        observed_at = _latest_observed_at(
            refreshed_at,
            users_snapshot,
            usage_snapshot,
            api_keys_snapshot,
        )
        existing_by_user_id = {
            str(record.user_id): record
            for record in self.store.list_user_usage_segments(limit=100000)
        }
        records: list[UserUsageSegmentRecord] = []
        for user in users:
            if not isinstance(user, dict) or user.get("id") in (None, ""):
                continue
            records.append(
                self._build_record(
                    user=user,
                    user_usage=usage_payload.get(str(user.get("id"))),
                    api_keys=api_keys_payload.get(str(user.get("id"))),
                    observed_at=observed_at,
                    refreshed_at=refreshed_at,
                    existing=existing_by_user_id.get(str(user.get("id"))),
                )
            )

        self.store.upsert_user_usage_segments(records)
        segment_counts: dict[str, int] = {}
        for record in records:
            segment_counts[record.segment.value] = segment_counts.get(record.segment.value, 0) + 1
        result = UsageSegmentationRefreshResult(
            refreshed_at=refreshed_at,
            user_count=len(records),
            segment_counts=segment_counts,
            records=records,
        )
        logger.info(
            "Usage segmentation refresh completed | users=%s segments=%s",
            result.user_count,
            result.segment_counts,
        )
        return result

    def _build_record(
        self,
        *,
        user: dict[str, Any],
        user_usage: Any,
        api_keys: Any,
        observed_at: datetime,
        refreshed_at: datetime,
        existing: UserUsageSegmentRecord | None,
    ) -> UserUsageSegmentRecord:
        usage_by_window = _usage_by_window(user_usage)
        daily_average_by_window = {
            window: _daily_average(window, value)
            for window, value in usage_by_window.items()
        }
        known_count = sum(1 for value in usage_by_window.values() if value is not None)
        positive_count = sum(
            1 for value in usage_by_window.values() if value is not None and value > 0
        )
        short_term_ratio = _ratio(
            daily_average_by_window.get("5h"),
            daily_average_by_window.get("30d"),
        )
        medium_term_ratio = _ratio(
            daily_average_by_window.get("7d"),
            daily_average_by_window.get("30d"),
        )
        baseline_window, baseline_daily_average = _baseline_daily_average(
            daily_average_by_window
        )
        balance = _optional_float(user.get("balance"))
        runway_days = (
            balance / baseline_daily_average
            if balance is not None and baseline_daily_average and baseline_daily_average > 0
            else None
        )
        segment, reasons = _classify_segment(
            daily_average_by_window=daily_average_by_window,
            usage_by_window=usage_by_window,
            short_term_ratio=short_term_ratio,
            positive_count=positive_count,
        )
        api_key_count = _api_key_count(api_keys)
        current_group_id = user.get("current_group_id", user.get("group_id"))
        current_group_name = user.get("current_group_name", user.get("group_name"))
        return UserUsageSegmentRecord(
            user_id=user.get("id"),
            email=str(user.get("email") or ""),
            username=_optional_text(user.get("username")),
            name=_optional_text(user.get("name")),
            display_name=_optional_text(user.get("display_name")),
            status=_optional_text(user.get("status")),
            group_id=current_group_id,
            group_name=_optional_text(current_group_name),
            group_ids=list(user.get("group_ids") or ([current_group_id] if current_group_id not in (None, "") else [])),
            balance=balance,
            balance_display=_optional_text(user.get("balance_display")),
            balance_unit=_optional_text(user.get("balance_unit")),
            has_api_keys=api_key_count > 0,
            api_key_count=api_key_count,
            usage_by_window=usage_by_window,
            daily_average_by_window=daily_average_by_window,
            baseline_window=baseline_window,
            baseline_daily_average=baseline_daily_average,
            short_term_ratio=short_term_ratio,
            medium_term_ratio=medium_term_ratio,
            runway_days=runway_days,
            known_usage_window_count=known_count,
            positive_usage_window_count=positive_count,
            segment=segment,
            segment_label=SEGMENT_LABELS[segment],
            reasons=reasons,
            metadata={
                "thresholds": {
                    "heavy_30d_daily": HEAVY_30D_DAILY,
                    "heavy_7d_daily": HEAVY_7D_DAILY,
                    "active_30d_daily": ACTIVE_30D_DAILY,
                    "active_7d_daily": ACTIVE_7D_DAILY,
                    "spike_short_term_ratio": SPIKE_SHORT_TERM_RATIO,
                }
            },
            observed_at=observed_at,
            refreshed_at=refreshed_at,
            created_at=existing.created_at if existing else refreshed_at,
            updated_at=refreshed_at,
        )


def _usage_by_window(user_usage: Any) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    source = user_usage if isinstance(user_usage, dict) else {}
    for window in USER_USAGE_WINDOWS:
        stats = source.get(window)
        result[window] = _usage_value_from_stats(stats if isinstance(stats, dict) else {})
    return result


def _usage_value_from_stats(stats: dict[str, Any]) -> float | None:
    if stats.get("error"):
        return None
    for key in (
        "total_actual_cost",
        "total_cost",
        "actual_cost",
        "cost",
        "usage",
        "amount",
    ):
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


def _classify_segment(
    *,
    daily_average_by_window: dict[str, float | None],
    usage_by_window: dict[str, float | None],
    short_term_ratio: float | None,
    positive_count: int,
) -> tuple[UsageSegment, list[str]]:
    daily_30d = daily_average_by_window.get("30d")
    daily_7d = daily_average_by_window.get("7d")
    if (daily_30d is not None and daily_30d >= HEAVY_30D_DAILY) or (
        daily_7d is not None and daily_7d >= HEAVY_7D_DAILY
    ):
        return UsageSegment.heavy, [
            f"30d_daily={_format_reason_number(daily_30d)}",
            f"7d_daily={_format_reason_number(daily_7d)}",
        ]
    if (
        usage_by_window.get("5h") is not None
        and usage_by_window.get("5h", 0) > 0
        and short_term_ratio is not None
        and short_term_ratio >= SPIKE_SHORT_TERM_RATIO
    ):
        return UsageSegment.spike, [
            f"5h_vs_30d_ratio={_format_reason_number(short_term_ratio)}",
        ]
    if (daily_30d is not None and daily_30d >= ACTIVE_30D_DAILY) or (
        daily_7d is not None and daily_7d >= ACTIVE_7D_DAILY
    ):
        return UsageSegment.active, [
            f"30d_daily={_format_reason_number(daily_30d)}",
            f"7d_daily={_format_reason_number(daily_7d)}",
        ]
    if positive_count > 0:
        return UsageSegment.light, [f"positive_windows={positive_count}"]
    return UsageSegment.idle, ["no_positive_usage"]


def _api_key_count(api_keys: Any) -> int:
    if not isinstance(api_keys, dict):
        return 0
    total = api_keys.get("total")
    if isinstance(total, int) and not isinstance(total, bool):
        return max(0, total)
    items = api_keys.get("items")
    return len(items) if isinstance(items, list) else 0


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


def _optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _format_reason_number(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{value:.6g}"


def _latest_observed_at(
    fallback: datetime,
    *snapshots: OperationalDataSnapshot | None,
) -> datetime:
    observed = [snapshot.observed_at for snapshot in snapshots if snapshot is not None]
    return max(observed) if observed else fallback


__all__ = ["UsageSegmentationService"]
