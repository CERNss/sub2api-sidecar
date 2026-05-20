from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreditUsageWindow(str, Enum):
    window_5h = "5h"
    window_1d = "1d"
    window_7d = "7d"
    window_30d = "30d"


class CreditBalanceOperation(str, Enum):
    add = "add"
    subtract = "subtract"


class CreditTargetScopeKind(str, Enum):
    explicit_user_ids = "explicit_user_ids"
    all_users = "all_users"
    balance_threshold = "balance_threshold"
    group_ids = "group_ids"


class CreditScheduleKind(str, Enum):
    once = "once"
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"


class CreditRunStatus(str, Enum):
    planned = "planned"
    succeeded = "succeeded"
    partial_failed = "partial_failed"
    failed = "failed"
    skipped = "skipped"


class CreditOutcomeStatus(str, Enum):
    planned = "planned"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"


class CreditAuditOperation(str, Enum):
    manual_adjustment = "manual_adjustment"
    automatic_recharge = "automatic_recharge"
    policy_created = "policy_created"
    policy_updated = "policy_updated"
    policy_deleted = "policy_deleted"
    policy_enabled = "policy_enabled"
    policy_disabled = "policy_disabled"


class CreditTargetScope(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    kind: CreditTargetScopeKind
    user_ids: tuple[Any, ...] = ()
    group_ids: tuple[Any, ...] = ()
    balance_below: float | None = None

    @field_validator("user_ids", "group_ids", mode="before")
    @classmethod
    def _dedupe_tuple(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            value = (value,)
        deduped: list[Any] = []
        seen: set[str] = set()
        for item in value:
            if item in (None, ""):
                continue
            key = str(item)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return tuple(deduped)


class CreditRechargeSchedule(BaseModel):
    kind: CreditScheduleKind
    start_at: datetime
    timezone: str = "Asia/Shanghai"
    end_at: datetime | None = None


class CreditRechargePolicy(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    policy_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    enabled: bool = False
    amount: float
    target_scope: CreditTargetScope
    schedule: CreditRechargeSchedule
    reason_template: str = "automatic recharge"
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CreditUserSnapshot(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: Any
    email: str
    name: str | None = None
    username: str | None = None
    display_name: str | None = None
    status: str | None = None
    balance: float | None = None
    balance_display: str | None = None
    balance_unit: str | None = None
    current_group_id: Any | None = None
    current_group_name: str | None = None
    group_ids: list[Any] = Field(default_factory=list)
    consumption: float | None = None
    usage_window: CreditUsageWindow = CreditUsageWindow.window_1d
    usage: dict[str, Any] = Field(default_factory=dict)
    usage_segment: str | None = None
    usage_segment_label: str | None = None
    usage_profile: dict[str, Any] = Field(default_factory=dict)


class CreditAdjustmentOutcome(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: Any
    email: str
    status: CreditOutcomeStatus
    operation: CreditBalanceOperation | None = None
    amount: float
    balance_before: float | None = None
    balance_after: float | None = None
    error_message: str | None = None
    skipped_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CreditRechargeRunRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    policy_id: str | None = None
    policy_name: str | None = None
    occurrence_key: str | None = None
    operation_type: CreditAuditOperation
    status: CreditRunStatus
    dry_run: bool = False
    amount: float
    target_scope: CreditTargetScope
    reason: str
    actor: str | None = None
    scheduled_for: datetime | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    target_count: int = 0
    success_count: int = 0
    skipped_count: int = 0
    failure_count: int = 0
    outcomes: list[CreditAdjustmentOutcome] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CreditAuditRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    audit_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    operation_type: CreditAuditOperation
    status: str
    user_id: Any | None = None
    policy_id: str | None = None
    run_id: str | None = None
    actor: str | None = None
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
