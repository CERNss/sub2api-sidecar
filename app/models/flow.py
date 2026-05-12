from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FlowStatus(str, Enum):
    pending_oauth = "pending_oauth"
    completed = "completed"
    failed = "failed"


class AssignmentMode(str, Enum):
    dedicated = "dedicated"
    managed_pool = "managed_pool"


class ProvisionEventType(str, Enum):
    start_requested = "start_requested"
    user_created = "user_created"
    group_resolved = "group_resolved"
    user_bound = "user_bound"
    oauth_url_generated = "oauth_url_generated"
    pending_oauth = "pending_oauth"
    callback_parsed = "callback_parsed"
    oauth_exchanged = "oauth_exchanged"
    account_created = "account_created"
    account_bound = "account_bound"
    completed = "completed"
    failed = "failed"


class ProvisionEventStatus(str, Enum):
    info = "info"
    succeeded = "succeeded"
    failed = "failed"


class ProvisionFlow(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    flow_id: str
    email: str
    user_id: Any | None = None
    group_id: Any
    state: str
    status: FlowStatus
    assignment_mode: AssignmentMode = AssignmentMode.dedicated
    assignment_reason: str | None = None
    account_name: str
    oauth_url: str | None = None
    oauth_session_id: str | None = None
    oauth_account_id: Any | None = None
    oauth_exchange_payload: dict[str, Any] | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProvisionEvent(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    flow_id: str
    event_type: ProvisionEventType
    status: ProvisionEventStatus
    message: str
    details: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
