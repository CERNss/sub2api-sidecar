from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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


class ApiTokenResponse(BaseModel):
    success: bool = True
    username: str
    access_key: str
    token_type: str = "bearer"


class AuthSessionResponse(BaseModel):
    success: bool = True
    username: str
    expires_at: datetime | None


class Sub2APIUpstreamResponse(BaseModel):
    upstream_id: str
    name: str
    base_url: str
    is_default: bool = False


class Sub2APIUpstreamsEnvelope(BaseModel):
    success: bool = True
    items: list[Sub2APIUpstreamResponse]
    default_upstream_id: str


class ProvisionStartRequest(BaseModel):
    email: EmailStr
    upstream_id: str | None = Field(default=None, min_length=1)


class ProvisioningRuntimeSettingsResponse(BaseModel):
    assignment_mode: str = "dedicated"
    updated_at: datetime | None = None


class ProvisioningRuntimeSettingsRequest(BaseModel):
    assignment_mode: str = Field(default="dedicated", pattern="^(dedicated|managed_pool)$")


class ProvisioningRuntimeSettingsEnvelope(BaseModel):
    success: bool = True
    settings: ProvisioningRuntimeSettingsResponse


class ProvisionStartResponse(BaseModel):
    success: bool = True
    upstream_id: str
    flow_id: str
    email: EmailStr
    user_id: Any | None = None
    group_id: Any
    assignment_mode: str
    assignment_reason: str | None = None
    account_name: str
    status: str
    oauth_required: bool
    oauth_account_id: Any | None = None
    oauth_url: str | None = None
    oauth_redirect_uri: str


class ProvisionCompleteRequest(BaseModel):
    callback_url: str = Field(..., min_length=1)


class ProvisionCompleteResponse(BaseModel):
    success: bool = True
    upstream_id: str
    flow_id: str
    email: EmailStr
    group_id: Any
    oauth_account_id: Any
    status: str


class ProvisionFlowSummaryResponse(BaseModel):
    flow_id: str
    upstream_id: str
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
    upstream_id: str
    user_id: Any
    email: str
    name: str | None = None
    username: str | None = None
    display_name: str | None = None
    status: str | None = None
    current_group_id: Any | None = None
    current_group_name: str | None = None
    group_ids: list[Any] = Field(default_factory=list)
    local_group_id: Any | None = None
    local_group_name: str | None = None
    has_local_assignment: bool = False


class OrchestrationUsersEnvelope(BaseModel):
    success: bool = True
    upstream_id: str
    items: list[OrchestrationUserResponse]
    total: int


class OrchestrationGroupResponse(BaseModel):
    upstream_id: str
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
    upstream_id: str
    items: list[OrchestrationGroupResponse]
    total: int


class OrchestrationAccountResponse(BaseModel):
    upstream_id: str
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
    upstream_id: str
    items: list[OrchestrationAccountResponse]
    total: int


class OrchestrationApiKeyResponse(BaseModel):
    upstream_id: str
    key_id: Any
    name: str | None = None
    user_id: Any | None = None
    user_email: str | None = None
    group_id: Any | None = None
    group_name: str | None = None
    status: str | None = None
    usage_5h: float | None = None
    usage_1d: float | None = None
    usage_7d: float | None = None


class OrchestrationApiKeysEnvelope(BaseModel):
    success: bool = True
    upstream_id: str
    items: list[OrchestrationApiKeyResponse]
    total: int


class CreditControlUserResponse(BaseModel):
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
    consumption: float | None = None
    usage_window: str
    usage: dict[str, Any] = Field(default_factory=dict)
    usage_segment: str | None = None
    usage_segment_label: str | None = None
    usage_profile: dict[str, Any] = Field(default_factory=dict)
    api_key_count: int | None = None
    last_activity_at: str | None = None
    updated_at: str | None = None


class CreditControlUsersEnvelope(BaseModel):
    success: bool = True
    items: list[CreditControlUserResponse]
    total: int
    limit: int
    offset: int
    aggregates: dict[str, Any] = Field(default_factory=dict)


class CreditControlApiKeyResponse(BaseModel):
    key_id: Any
    name: str | None = None
    usage: float | None = None
    group_id: Any | None = None
    group_name: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class CreditControlAuditResponse(BaseModel):
    audit_id: str | None = None
    event_id: str | None = None
    user_id: Any | None = None
    policy_id: str | None = None
    run_id: str | None = None
    actor: str | None = None
    action: str
    status: str | None = None
    amount: float | None = None
    balance_before: float | None = None
    balance_after: float | None = None
    reason: str | None = None
    summary: str | None = None
    details: dict[str, Any] | None = None
    created_at: datetime | None = None


class CreditControlUserDetailEnvelope(BaseModel):
    success: bool = True
    item: CreditControlUserResponse
    api_keys: list[CreditControlApiKeyResponse] = Field(default_factory=list)
    audit_items: list[CreditControlAuditResponse] = Field(default_factory=list)


class CreditControlTargetRequest(BaseModel):
    mode: str = Field(..., min_length=1)
    user_ids: list[Any] = Field(default_factory=list)
    window: str = "1d"
    search: str | None = None
    status: str | None = None
    group_id: Any | None = None
    balance_min: float | None = None
    balance_max: float | None = None
    consumption_min: float | None = None
    consumption_max: float | None = None
    usage_segment: str | None = None


class CreditControlAdjustmentRequest(BaseModel):
    preview: bool = False
    amount: float
    reason: str = Field(..., min_length=1)
    target: CreditControlTargetRequest


class CreditControlAdjustmentItemResponse(BaseModel):
    user_id: Any
    email: str | None = None
    amount: float
    operation: str | None = None
    balance_before: float | None = None
    balance_after: float | None = None
    status: str | None = None
    error: str | None = None
    skipped_reason: str | None = None


class CreditControlAdjustmentEnvelope(BaseModel):
    success: bool = True
    run_id: str | None = None
    status: str | None = None
    dry_run: bool = False
    affected_count: int
    total_amount: float
    items: list[CreditControlAdjustmentItemResponse]
    details: dict[str, Any] = Field(default_factory=dict)


class CreditControlPolicyRequest(BaseModel):
    name: str = Field(..., min_length=1)
    enabled: bool = True
    amount: float = Field(..., gt=0)
    schedule_type: str = "one_time"
    schedule: str | None = None
    timezone: str = "Asia/Shanghai"
    target_scope: str = "all"
    target_group_id: Any | None = None
    target_user_ids: list[Any] = Field(default_factory=list)
    target_balance_below: float | None = None
    reason_template: str | None = None


class CreditControlPolicyResponse(BaseModel):
    policy_id: str
    name: str
    enabled: bool
    amount: float
    schedule_type: str
    schedule: str | None = None
    timezone: str | None = None
    target_scope: str
    target_group_id: Any | None = None
    target_user_ids: list[Any] = Field(default_factory=list)
    target_balance_below: float | None = None
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class CreditControlPoliciesEnvelope(BaseModel):
    success: bool = True
    items: list[CreditControlPolicyResponse]
    total: int


class CreditControlPolicyEnvelope(BaseModel):
    success: bool = True
    item: CreditControlPolicyResponse


class CreditControlRunResponse(BaseModel):
    run_id: str
    policy_id: str | None = None
    policy_name: str | None = None
    status: str | None = None
    dry_run: bool = False
    affected_count: int | None = None
    total_amount: float | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    scheduled_for: datetime | None = None
    error_message: str | None = None
    details: dict[str, Any] | None = None


class CreditControlRunsEnvelope(BaseModel):
    success: bool = True
    items: list[CreditControlRunResponse]
    total: int


class CreditControlSchedulerStatusResponse(BaseModel):
    success: bool = True
    enabled: bool
    running: bool
    cadence_seconds: int
    tick_count: int
    last_tick_started_at: datetime | None = None
    last_tick_finished_at: datetime | None = None
    last_tick_error: str | None = None
    last_run_count: int = 0
    last_affected_count: int = 0
    last_failure_count: int = 0


class CreditControlRuntimeSettingsResponse(BaseModel):
    enabled: bool = True
    updated_at: datetime | None = None


class CreditControlRuntimeSettingsRequest(BaseModel):
    enabled: bool = True


class CreditControlRuntimeSettingsEnvelope(BaseModel):
    success: bool = True
    settings: CreditControlRuntimeSettingsResponse


class CreditControlAuditEnvelope(BaseModel):
    success: bool = True
    items: list[CreditControlAuditResponse]
    total: int


class UserUsageSegmentResponse(BaseModel):
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
    segment: str
    segment_label: str
    reasons: list[str] = Field(default_factory=list)
    observed_at: datetime | None = None
    refreshed_at: datetime | None = None


class UserUsageSegmentsEnvelope(BaseModel):
    success: bool = True
    items: list[UserUsageSegmentResponse]
    total: int
    limit: int
    offset: int
    segment_counts: dict[str, int] = Field(default_factory=dict)


class UsageSegmentationRefreshEnvelope(BaseModel):
    success: bool = True
    refreshed_at: datetime
    user_count: int
    segment_counts: dict[str, int] = Field(default_factory=dict)


class UsageSegmentationSchedulerStatusResponse(BaseModel):
    success: bool = True
    enabled: bool
    running: bool
    cadence_seconds: int
    tick_count: int
    last_tick_started_at: datetime | None = None
    last_tick_finished_at: datetime | None = None
    last_tick_error: str | None = None
    last_refreshed_count: int = 0
    last_segment_counts: dict[str, int] | None = None


class GroupUsageSegmentResponse(BaseModel):
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
    observed_at: datetime | None = None
    refreshed_at: datetime | None = None


class GroupUsageSegmentsEnvelope(BaseModel):
    success: bool = True
    items: list[GroupUsageSegmentResponse]
    total: int
    limit: int
    offset: int


class GroupUsageRefreshEnvelope(BaseModel):
    success: bool = True
    refreshed_at: datetime
    group_count: int
    window_counts: dict[str, int] = Field(default_factory=dict)


class GroupUsageSchedulerStatusResponse(BaseModel):
    success: bool = True
    enabled: bool
    running: bool
    cadence_seconds: int
    tick_count: int
    last_tick_started_at: datetime | None = None
    last_tick_finished_at: datetime | None = None
    last_tick_error: str | None = None
    last_refreshed_count: int = 0
    last_window_counts: dict[str, int] | None = None


class OrchestrationAssignRequest(BaseModel):
    upstream_id: str | None = Field(default=None, min_length=1)
    user_id: Any
    email: str = Field(..., min_length=1)
    source_group_id: Any | None = None
    target_group_id: Any
    reason: str | None = None


class OrchestrationApiKeyAssignRequest(BaseModel):
    upstream_id: str | None = Field(default=None, min_length=1)
    user_id: Any
    email: str = Field(..., min_length=1)
    key_id: Any
    source_group_id: Any | None = None
    target_group_id: Any
    reason: str | None = None


class OrchestrationGroupMigrationRequest(BaseModel):
    upstream_id: str | None = Field(default=None, min_length=1)
    source_group_id: Any
    target_group_id: Any
    reason: str | None = None


class KeyTransferRequest(BaseModel):
    upstream_id: str | None = Field(default=None, min_length=1)
    source_user_id: Any | None = None
    key_ids: list[Any] | None = None
    dry_run: bool = False
    scope: str = "all_users"
    reason: str | None = None


class KeyTransferItemResponse(BaseModel):
    key_id: Any
    key_name: str | None = None
    key_service: str | None = None
    key_environment: str | None = None
    key_object: str | None = None
    key_version: str | None = None
    source_user_id: Any | None = None
    source_group_id: Any | None = None
    target_user_id: Any | None = None
    target_email: str | None = None
    target_group_id: Any | None = None
    status: str
    reason: str
    quota: float | None = None


class KeyTransferEnvelope(BaseModel):
    success: bool = True
    run_id: str | None = None
    run_kind: str | None = None
    tag: str | None = None
    dry_run: bool = False
    key_name_pattern: str
    source_user_id: Any | None = None
    scope: str = "all_users"
    planned_count: int = 0
    moved_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    items: list[KeyTransferItemResponse] = Field(default_factory=list)


class ApiKeyAutomationRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    action: str = Field(..., pattern="^(create|list)$")
    upstream_id: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)
    target: str | None = None
    email: EmailStr | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class ApiKeyAutomationItemResponse(BaseModel):
    key_id: Any | None = None
    name: str | None = None
    key_value: str | None = None
    key_service: str | None = None
    key_environment: str | None = None
    key_object: str | None = None
    key_version: str | None = None
    target_email: str | None = None
    user_id: Any | None = None
    user_email: str | None = None
    group_id: Any | None = None
    group_name: str | None = None
    status: str | None = None
    quota: Any | None = None
    quota_used: Any | None = None
    usage_5h: float | None = None
    usage_1d: float | None = None
    usage_7d: float | None = None


class ApiKeyAutomationEnvelope(BaseModel):
    success: bool = True
    action: str
    status: str = "ok"
    key_name_pattern: str = "service:environment:object:version:email"
    fallback_to_admin: bool | None = None
    fallback_reason: str | None = None
    item: ApiKeyAutomationItemResponse | None = None
    items: list[ApiKeyAutomationItemResponse] = Field(default_factory=list)
    total: int = 0


class RotationPoolGroupRequest(BaseModel):
    group_id: Any
    priority: int | None = Field(default=None, ge=0)
    pool_kind: str = "rotation"


class RotationPoolGroupRemoveRequest(BaseModel):
    group_id: Any
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


class AutoRotationSchedulerStatusResponse(BaseModel):
    success: bool = True
    enabled: bool
    running: bool
    cadence_seconds: int
    tick_count: int
    last_tick_started_at: datetime | None = None
    last_tick_finished_at: datetime | None = None
    last_tick_error: str | None = None
    last_run_id: str | None = None
    last_status: str | None = None
    last_moved_count: int = 0
    last_planned_count: int = 0
    last_skipped_count: int = 0
    last_failed_count: int = 0


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


class OperationalDataSourceStatusResponse(BaseModel):
    source_key: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None
    item_count: int | None = None
    updated_at: datetime | None = None


class OperationalDataStatusResponse(BaseModel):
    success: bool = True
    enabled: bool
    running: bool
    cadence_seconds: int
    collect_interval_seconds: int
    expiration: int | None = None
    retention_seconds: int | None = None
    max_storage_mb: int | None = None
    storage_bytes: int = 0
    tick_count: int
    last_tick_started_at: datetime | None = None
    last_tick_finished_at: datetime | None = None
    last_tick_error: str | None = None
    last_sampling_started_at: datetime | None = None
    last_sampling_finished_at: datetime | None = None
    last_sampling_error: str | None = None
    sampled_signal_count: int = 0
    source_statuses: list[OperationalDataSourceStatusResponse] = Field(default_factory=list)


class OperationalDataRuntimeSettingsResponse(BaseModel):
    enabled: bool = True
    collect_interval_seconds: int = 60
    expiration: int | None = None
    retention_seconds: int | None = None
    max_storage_mb: int | None = None
    updated_at: datetime | None = None


class OperationalDataRuntimeSettingsRequest(BaseModel):
    enabled: bool = True
    collect_interval_seconds: int = Field(default=60, ge=5)
    expiration: int | None = Field(default=None, gt=0)
    retention_seconds: int | None = Field(default=None, gt=0)
    max_storage_mb: int | None = Field(default=None, gt=0)


class OperationalDataRuntimeSettingsEnvelope(BaseModel):
    success: bool = True
    settings: OperationalDataRuntimeSettingsResponse


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
