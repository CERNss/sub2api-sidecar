from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FlowStatus(str, Enum):
    pending_oauth = "pending_oauth"
    completed = "completed"
    failed = "failed"


class ProvisionFlow(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    flow_id: str
    email: str
    user_id: Any
    group_id: Any
    state: str
    status: FlowStatus
    account_name: str
    oauth_url: str | None = None
    oauth_account_id: Any | None = None
    oauth_exchange_payload: dict[str, Any] | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
