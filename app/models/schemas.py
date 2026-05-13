from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class Sub2APILoginRequest(BaseModel):
    token: str = Field(..., min_length=1)


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
    user_id: Any | None = None
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


class ProvisionFlowSummaryResponse(BaseModel):
    flow_id: str
    email: EmailStr
    user_id: Any | None = None
    group_id: Any
    assignment_mode: str | None = None
    status: str
    account_name: str
    oauth_account_id: Any | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ProvisionEventResponse(BaseModel):
    event_id: str
    flow_id: str
    event_type: str
    status: str
    message: str
    details: dict[str, Any] | None = None
    created_at: datetime


class ProvisionFlowDetailResponse(ProvisionFlowSummaryResponse):
    success: bool = True
    state: str
    assignment_reason: str | None = None
    oauth_url: str | None = None
    oauth_redirect_uri: str
    oauth_exchange_payload: dict[str, Any] | None = None
    events: list[ProvisionEventResponse] = Field(default_factory=list)


class ProvisionFlowsEnvelope(BaseModel):
    success: bool = True
    items: list[ProvisionFlowSummaryResponse]
    total: int
    limit: int
    offset: int


class OrchestrationUserResponse(BaseModel):
    user_id: Any
    email: str
    name: str | None = None
    username: str | None = None
    display_name: str | None = None
    status: str | None = None
    current_group_id: Any | None = None
    current_group_name: str | None = None
    local_group_id: Any | None = None
    local_group_name: str | None = None
    has_local_assignment: bool = False


class OrchestrationUsersEnvelope(BaseModel):
    success: bool = True
    items: list[OrchestrationUserResponse]
    total: int


class OrchestrationGroupResponse(BaseModel):
    group_id: Any
    name: str
    group_kind: str | None = None
    platform: str | None = None
    status: str | None = None
    is_exclusive: bool
    is_subscription: bool = False
    rotation_supported: bool = True
    unsupported_reason: str | None = None
    account_count: int | None = None
    active_account_count: int | None = None
    rpm_limit: int | None = None
    rate_multiplier: float | None = None
    daily_limit_usd: float | None = None
    weekly_limit_usd: float | None = None
    monthly_limit_usd: float | None = None


class OrchestrationGroupsEnvelope(BaseModel):
    success: bool = True
    items: list[OrchestrationGroupResponse]
    total: int


class OrchestrationAccountResponse(BaseModel):
    account_id: Any
    name: str
    email: str | None = None
    provider: str | None = None
    platform: str | None = None
    account_type: str | None = None
    status: str | None = None
    availability_status: str = "unknown"
    availability_reason: str | None = None
    is_available: bool | None = None
    temporary_unschedulable: bool = False
    rate_limited: bool = False
    quota_remaining: float | None = None
    last_error: str | None = None
    availability_updated_at: str | None = None
    concurrency: float | None = None
    current_concurrency: float | None = None
    usage_5h_percent: float | None = None
    usage_7d_percent: float | None = None
    usage_updated_at: str | None = None
    group_ids: list[Any] = Field(default_factory=list)
    group_names: list[str] = Field(default_factory=list)


class OrchestrationAccountsEnvelope(BaseModel):
    success: bool = True
    items: list[OrchestrationAccountResponse]
    total: int


class OrchestrationApiKeyResponse(BaseModel):
    key_id: Any
    name: str | None = None
    group_id: Any | None = None
    group_name: str | None = None
    status: str | None = None
    usage_5h: float | None = None
    usage_1d: float | None = None
    usage_7d: float | None = None


class OrchestrationApiKeysEnvelope(BaseModel):
    success: bool = True
    items: list[OrchestrationApiKeyResponse]
    total: int


class OrchestrationAssignRequest(BaseModel):
    user_id: Any
    email: str = Field(..., min_length=1)
    source_group_id: Any
    target_group_id: Any
    reason: str | None = None


class OrchestrationApiKeyAssignRequest(BaseModel):
    user_id: Any
    email: str = Field(..., min_length=1)
    key_id: Any
    source_group_id: Any | None = None
    target_group_id: Any
    reason: str | None = None


class RotationPoolGroupRequest(BaseModel):
    group_id: Any
    priority: int | None = Field(default=None, ge=0)
    pool_kind: str = "rotation"


class RotationPoolCandidateResponse(BaseModel):
    group_id: Any
    name: str
    group_kind: str | None = None
    platform: str | None = None
    status: str | None = None
    is_exclusive: bool
    is_subscription: bool = False
    rotation_supported: bool = True
    unsupported_reason: str | None = None
    selected: bool
    rotation_selected: bool = False
    landing_selected: bool = False
    priority: int | None = None
    landing_priority: int | None = None


class RotationPoolCandidatesEnvelope(BaseModel):
    success: bool = True
    items: list[RotationPoolCandidateResponse]


class AutoRotationConfigResponse(BaseModel):
    enabled: bool
    auto_assign_new_users: bool = False
    cooldown_minutes: int
    usage_window: str
    usage_thresholds: list[float]
    imbalance_epsilon: float = 0.0
    improvement_delta: float = 0.0
    schedule_source_group_ids: list[Any] = Field(default_factory=list)


class AutoRotationConfigRequest(BaseModel):
    enabled: bool = False
    auto_assign_new_users: bool = False
    cooldown_minutes: int = Field(default=0, ge=0)
    usage_window: str
    usage_thresholds: list[float] = Field(default_factory=list)
    imbalance_epsilon: float = Field(default=0.0, ge=0.0)
    improvement_delta: float = Field(default=0.0, ge=0.0)
    schedule_source_group_ids: list[Any] = Field(default_factory=list)


class AutoRotationConfigEnvelope(BaseModel):
    success: bool = True
    config: AutoRotationConfigResponse
    landing_pool: list[RotationPoolCandidateResponse] = Field(default_factory=list)
    rotation_pool: list[RotationPoolCandidateResponse] = Field(default_factory=list)


class ManualRotationRequest(BaseModel):
    user_id: Any
    target_group_id: Any
    reason: str | None = None


class RotationExecutionResponse(BaseModel):
    success: bool = True
    run_id: str | None = None
    run_kind: str | None = None
    tag: str | None = None
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
    metadata: dict[str, Any] | None = None


class AutoRotationRunRequest(BaseModel):
    dry_run: bool = False


class AutoRotationRunResponse(BaseModel):
    success: bool = True
    run_id: str | None = None
    run_kind: str | None = None
    tag: str | None = None
    status: str | None = None
    window: str
    dry_run: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    synced: dict[str, int] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    dead_band_skipped: bool = False
    planned: list[RotationExecutionResponse] = Field(default_factory=list)
    moved: list[RotationExecutionResponse]
    skipped: list[RotationExecutionResponse]
    failed: list[RotationExecutionResponse]
    rollback_results: list[RotationExecutionResponse] = Field(default_factory=list)
    rollback_status: str | None = None
    rollback_reason: str | None = None


class AutoRotationRunsEnvelope(BaseModel):
    success: bool = True
    items: list[AutoRotationRunResponse]
    total: int


class NotificationTestRequest(BaseModel):
    rule_id: str = Field(..., min_length=1)


class NotificationDeliveryOutcomeResponse(BaseModel):
    receiver_id: str
    provider: str
    status: str
    attempt_count: int
    response_status: int | None = None
    error_message: str | None = None


class NotificationTestResponse(BaseModel):
    success: bool = True
    rule_id: str
    rule_name: str
    outcomes: list[NotificationDeliveryOutcomeResponse]


class NotificationEvaluateRequest(BaseModel):
    rule_id: str = Field(..., min_length=1)


class NotificationRuleStateResponse(BaseModel):
    rule_id: str
    last_evaluated_at: datetime | None = None
    last_value: float | None = None
    breach_started_at: datetime | None = None
    last_alert_at: datetime | None = None
    is_firing: bool = False
    last_error: str | None = None


class NotificationEvaluateResponse(BaseModel):
    success: bool = True
    rule_id: str
    rule_name: str
    action: str
    reason: str
    state: NotificationRuleStateResponse
    deliveries: list[NotificationDeliveryOutcomeResponse]


class NotificationDeliveryRecordResponse(BaseModel):
    delivery_id: str
    receiver_id: str
    rule_id: str
    provider: str
    severity: str
    trigger: str
    status: str
    attempt_index: int
    response_status: int | None = None
    error_message: str | None = None
    payload_digest: str = ""
    created_at: datetime
    updated_at: datetime


class NotificationDeliveriesEnvelope(BaseModel):
    success: bool = True
    items: list[NotificationDeliveryRecordResponse]
    total: int


class ErrorResponse(BaseModel):
    success: bool = False
    detail: str = Field(..., description="Human readable error message")
