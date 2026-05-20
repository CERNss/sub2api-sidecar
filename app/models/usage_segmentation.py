from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UsageSegment(str, Enum):
    heavy = "heavy"
    spike = "spike"
    active = "active"
    light = "light"
    idle = "idle"


SEGMENT_LABELS: dict[UsageSegment, str] = {
    UsageSegment.heavy: "高频",
    UsageSegment.spike: "短期突增",
    UsageSegment.active: "活跃",
    UsageSegment.light: "轻量",
    UsageSegment.idle: "沉默",
}


class UserUsageSegmentRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: Any
    email: str
    username: str | None = None
    name: str | None = None
    display_name: str | None = None
    status: str | None = None
    group_id: Any | None = None
    group_name: str | None = None
    group_ids: list[Any] = Field(default_factory=list)
    balance: float | None = None
    balance_display: str | None = None
    balance_unit: str | None = None
    has_api_keys: bool | None = None
    api_key_count: int = 0
    usage_by_window: dict[str, float | None] = Field(default_factory=dict)
    daily_average_by_window: dict[str, float | None] = Field(default_factory=dict)
    baseline_window: str | None = None
    baseline_daily_average: float | None = None
    short_term_ratio: float | None = None
    medium_term_ratio: float | None = None
    runway_days: float | None = None
    known_usage_window_count: int = 0
    positive_usage_window_count: int = 0
    segment: UsageSegment = UsageSegment.idle
    segment_label: str = SEGMENT_LABELS[UsageSegment.idle]
    reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    refreshed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UsageSegmentationRefreshResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    refreshed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    user_count: int = 0
    segment_counts: dict[str, int] = Field(default_factory=dict)
    records: list[UserUsageSegmentRecord] = Field(default_factory=list)


__all__ = [
    "SEGMENT_LABELS",
    "UsageSegment",
    "UsageSegmentationRefreshResult",
    "UserUsageSegmentRecord",
]
