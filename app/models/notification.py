from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NotificationSeverity(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class NotificationOperator(str, Enum):
    gt = "gt"
    gte = "gte"
    lt = "lt"
    lte = "lte"
    eq = "eq"
    neq = "neq"


class NotificationAggregation(str, Enum):
    latest = "latest"
    avg = "avg"
    max = "max"
    min = "min"
    sum = "sum"


class WebhookProvider(str, Enum):
    generic = "generic"
    feishu = "feishu"
    dingtalk = "dingtalk"
    wecom = "wecom"
    slack = "slack"
    discord = "discord"


class RoutingGroupBy(str, Enum):
    signal = "signal"
    source = "source"
    severity = "severity"


class NotificationWebhook(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str = ""
    enabled: bool = False
    provider: WebhookProvider = WebhookProvider.generic
    url: str = ""
    secret: str = ""
    mention_on_failure: bool = Field(default=False, alias="mentionOnFailure")


class NotificationRule(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str = ""
    enabled: bool = True
    signal_key: str = Field(alias="signalKey")
    severity: NotificationSeverity = NotificationSeverity.warning
    operator: NotificationOperator = NotificationOperator.gte
    threshold: str = ""
    warning_threshold: str = Field(default="", alias="warningThreshold")
    recovery_threshold: str = Field(default="", alias="recoveryThreshold")
    threshold_unit: str = Field(default="", alias="thresholdUnit")
    aggregation: NotificationAggregation = NotificationAggregation.latest
    read_interval_minutes: int = Field(default=10, alias="readIntervalMinutes", ge=1)
    evaluation_window_minutes: int = Field(default=30, alias="evaluationWindowMinutes", ge=1)
    for_minutes: int = Field(default=5, alias="forMinutes", ge=0)
    cooldown_minutes: int = Field(default=60, alias="cooldownMinutes", ge=0)
    target_webhook_ids: list[str] = Field(default_factory=list, alias="targetWebhookIds")
    include_resolved: bool = Field(default=True, alias="includeResolved")
    include_snapshot: bool = Field(default=True, alias="includeSnapshot")


class NotificationRoutingPolicy(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    group_by: RoutingGroupBy = Field(default=RoutingGroupBy.severity, alias="groupBy")
    group_wait_minutes: int = Field(default=2, alias="groupWaitMinutes", ge=0)
    repeat_interval_minutes: int = Field(default=120, alias="repeatIntervalMinutes", ge=0)
    quiet_hours_enabled: bool = Field(default=False, alias="quietHoursEnabled")
    quiet_hours_start: str = Field(default="22:00", alias="quietHoursStart")
    quiet_hours_end: str = Field(default="08:00", alias="quietHoursEnd")


class NotificationSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    webhooks: list[NotificationWebhook] = Field(default_factory=list)
    rules: list[NotificationRule] = Field(default_factory=list)
    policy: NotificationRoutingPolicy = Field(default_factory=NotificationRoutingPolicy)


class NotificationDeliveryStatus(str, Enum):
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"


class NotificationDeliveryTrigger(str, Enum):
    test = "test"
    rule = "rule"
    recovery = "recovery"


class NotificationMessage(BaseModel):
    rule_id: str
    rule_name: str
    signal_key: str
    severity: NotificationSeverity
    summary: str
    trigger: NotificationDeliveryTrigger
    snapshot: dict[str, Any] | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class NotificationDeliveryRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    delivery_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    receiver_id: str
    rule_id: str
    provider: WebhookProvider
    severity: NotificationSeverity
    trigger: NotificationDeliveryTrigger
    status: NotificationDeliveryStatus
    attempt_index: int = 0
    response_status: int | None = None
    error_message: str | None = None
    payload_digest: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class NotificationRuleAction(str, Enum):
    fire = "fire"
    recover = "recover"
    hold = "hold"
    no_data = "no_data"
    suppress = "suppress"


class NotificationRuleState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    rule_id: str
    last_evaluated_at: datetime | None = None
    last_value: float | None = None
    breach_started_at: datetime | None = None
    last_alert_at: datetime | None = None
    is_firing: bool = False
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CollectorSample(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    value: float
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    snapshot: dict[str, Any] | None = None


class RuleDecision(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    action: NotificationRuleAction
    reason: str = ""
    sample: CollectorSample | None = None
    next_state: NotificationRuleState
