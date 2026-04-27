from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    success: bool = True
    username: str
    access_key: str
    expires_at: datetime


class ProvisionStartRequest(BaseModel):
    email: EmailStr


class ProvisionStartResponse(BaseModel):
    success: bool = True
    flow_id: str
    email: EmailStr
    user_id: Any
    group_id: Any
    account_name: str
    oauth_url: str
    oauth_redirect_uri: str


class ProvisionCompleteRequest(BaseModel):
    callback_url: str = Field(..., min_length=1)


class ProvisionCompleteResponse(BaseModel):
    success: bool = True
    flow_id: str
    email: EmailStr
    group_id: Any
    oauth_account_id: Any
    status: str


class RotationPoolGroupRequest(BaseModel):
    group_id: Any
    priority: int | None = Field(default=None, ge=0)


class RotationPoolCandidateResponse(BaseModel):
    group_id: Any
    name: str
    platform: str | None = None
    status: str | None = None
    is_exclusive: bool
    selected: bool
    priority: int | None = None


class RotationPoolCandidatesEnvelope(BaseModel):
    success: bool = True
    items: list[RotationPoolCandidateResponse]


class ManualRotationRequest(BaseModel):
    user_id: Any
    target_group_id: Any
    reason: str | None = None


class RotationExecutionResponse(BaseModel):
    success: bool = True
    user_id: Any
    email: EmailStr
    source_group_id: Any | None = None
    target_group_id: Any | None = None
    trigger_type: str
    status: str
    reason: str
    migrated_keys: int = 0
    usage_window: str | None = None
    usage_value: float | None = None
    usage_snapshot: dict[str, Any] | None = None


class AutoRotationRunResponse(BaseModel):
    success: bool = True
    window: str
    moved: list[RotationExecutionResponse]
    skipped: list[RotationExecutionResponse]
    failed: list[RotationExecutionResponse]


class ErrorResponse(BaseModel):
    success: bool = False
    detail: str = Field(..., description="Human readable error message")
