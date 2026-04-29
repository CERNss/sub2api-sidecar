from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
    moved = "moved"
    skipped = "skipped"
    failed = "failed"


class RotationPoolGroup(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    group_id: Any
    group_name: str
    group_kind: str | None = None
    platform: str | None = None
    status: str | None = None
    is_exclusive: bool = True
    is_subscription: bool = False
    priority: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserGroupAssignment(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: Any
    email: str
    current_group_id: Any
    current_group_name: str | None = None
    assignment_mode: AssignmentMode = AssignmentMode.dedicated
    last_rotation_at: datetime | None = None
    last_decision_reason: str | None = None
    has_api_keys: bool | None = None
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
