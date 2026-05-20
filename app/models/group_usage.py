from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GroupUsageSegmentRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    group_id: Any
    group_name: str
    group_kind: str | None = None
    platform: str | None = None
    status: str | None = None
    is_exclusive: bool | None = None
    is_subscription: bool | None = None
    member_count: int = 0
    usage_by_window: dict[str, float | None] = Field(default_factory=dict)
    daily_average_by_window: dict[str, float | None] = Field(default_factory=dict)
    request_count_by_window: dict[str, int | None] = Field(default_factory=dict)
    token_count_by_window: dict[str, int | None] = Field(default_factory=dict)
    account_cost_by_window: dict[str, float | None] = Field(default_factory=dict)
    source_by_window: dict[str, str] = Field(default_factory=dict)
    baseline_window: str | None = None
    baseline_daily_average: float | None = None
    short_term_ratio: float | None = None
    medium_term_ratio: float | None = None
    known_usage_window_count: int = 0
    positive_usage_window_count: int = 0
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    refreshed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GroupUsageRefreshResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    refreshed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    group_count: int = 0
    window_counts: dict[str, int] = Field(default_factory=dict)
    records: list[GroupUsageSegmentRecord] = Field(default_factory=list)


__all__ = [
    "GroupUsageRefreshResult",
    "GroupUsageSegmentRecord",
]
