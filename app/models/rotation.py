from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.flow import AssignmentMode


class AutoRotationUsageWindow(str, Enum):
    window_5h = "5h"
    window_1d = "1d"
    window_7d = "7d"
    window_30d = "30d"


class RotationTrigger(str, Enum):
    manual = "manual"
    automatic_api = "automatic_api"
    automatic_interval = "automatic_interval"


class RotationResultStatus(str, Enum):
    planned = "planned"
    moved = "moved"
    skipped = "skipped"
    failed = "failed"


class OrchestrationRunKind(str, Enum):
    manual = "manual"
    automatic = "automatic"


class RotationPoolKind(str, Enum):
    landing = "landing"
    rotation = "rotation"


class RotationPoolGroup(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    group_id: str
    pool_kind: RotationPoolKind = RotationPoolKind.rotation
    group_name: str
    group_kind: str | None = None
    platform: str | None = None
    status: str | None = None
    is_exclusive: bool = True
    is_subscription: bool = False
    priority: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("group_id", mode="before")
    @classmethod
    def _coerce_group_id(cls, value: Any) -> str:
        if value is None:
            raise ValueError("group_id is required")
        return str(value)

    @property
    def rotation_supported(self) -> bool:
        return self.is_exclusive and not self.is_subscription


class UserGroupAssignment(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: Any
    # Bindings are two-dimensional: one dedicated group per user *per platform*. The
    # "openai" default is a transitional value so callers that have not been upgraded
    # yet keep working; the orchestration layer should always pass platform explicitly.
    platform: str = "openai"
    email: str
    current_group_id: Any
    current_group_name: str | None = None
    assignment_mode: AssignmentMode = AssignmentMode.dedicated
    last_rotation_at: datetime | None = None
    last_decision_reason: str | None = None
    has_api_keys: bool | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AutoRotationRuntimeConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    enabled: bool = False
    auto_assign_new_users: bool = False
    cooldown_minutes: int = 0
    usage_window: AutoRotationUsageWindow = AutoRotationUsageWindow.window_1d
    usage_thresholds: tuple[float, ...] = ()
    imbalance_epsilon: float = 0.0
    improvement_delta: float = 0.0
    schedule_source_group_ids: tuple[Any, ...] = ()
    # Source-side evacuation. Load balancing only ever asks "which group is
    # emptiest"; it never asks whether the group a user already sits on can still
    # serve traffic at all. These two triggers do: a group that lost every
    # schedulable account, or whose quota is spent, is emptied outright -- the
    # dead band and the improvement delta do not apply to an evacuation, because
    # "the pool looks balanced" is no reason to leave users on a dead account.
    evacuate_unschedulable_sources: bool = True
    # Quota utilisation (0-100) at which a group stops being usable. None turns
    # the trigger off. Defaults to 95 rather than 100 so the move lands before
    # the account starts refusing requests.
    evacuate_quota_used_percent: float | None = 95.0
    # Pick landing spots by remaining quota share instead of raw "emptiest wins",
    # so a group that is idle *because* it is nearly exhausted stops attracting
    # traffic. Mirrors the weighting used for manual rebalances.
    capacity_weighted_targets: bool = True
    # Users that must never be rotated -- relay identities whose keys are pinned
    # to a group on purpose. Without this an evacuation would happily move them,
    # since it does not care how much traffic the user sends.
    protected_user_ids: tuple[Any, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RotationEvent(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Any
    email: str
    source_group_id: Any | None = None
    target_group_id: Any | None = None
    trigger_type: RotationTrigger
    status: RotationResultStatus
    reason: str
    usage_window: AutoRotationUsageWindow | None = None
    usage_value: float | None = None
    usage_snapshot: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OrchestrationRunRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_kind: OrchestrationRunKind
    tag: str
    trigger_type: RotationTrigger
    dry_run: bool = False
    status: str
    window: AutoRotationUsageWindow | None = None
    synced: dict[str, int] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    dead_band_skipped: bool = False
    planned: list[dict[str, Any]] = Field(default_factory=list)
    moved: list[dict[str, Any]] = Field(default_factory=list)
    skipped: list[dict[str, Any]] = Field(default_factory=list)
    failed: list[dict[str, Any]] = Field(default_factory=list)
    rollback_results: list[dict[str, Any]] = Field(default_factory=list)
    rollback_status: str | None = None
    rollback_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
