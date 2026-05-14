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


class WebhookProvider(str, Enum):
    generic = "generic"
    feishu = "feishu"
    dingtalk = "dingtalk"
    wecom = "wecom"
    slack = "slack"
    discord = "discord"


class WebhookMethod(str, Enum):
    get = "GET"
    post = "POST"


DEFAULT_WEBHOOK_PAYLOAD_FIELDS: tuple[str, ...] = (
    "rule_id",
    "rule_name",
    "signal_key",
    "severity",
    "summary",
    "trigger",
    "snapshot",
    "occurred_at",
)


class NotificationWebhook(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    name: str = ""
    enabled: bool = False
    provider: WebhookProvider = WebhookProvider.generic
    method: WebhookMethod = WebhookMethod.post
    payload_fields: list[str] = Field(
        default_factory=lambda: list(DEFAULT_WEBHOOK_PAYLOAD_FIELDS),
        alias="payloadFields",
    )
    url: str = ""
    secret: str = ""


class NotificationRule(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    name: str = ""
    enabled: bool = True
    signal_key: str = Field(alias="signalKey")
    severity: NotificationSeverity = NotificationSeverity.warning
    operator: NotificationOperator = NotificationOperator.gte
    threshold: str = ""
    threshold_unit: str = Field(default="", alias="thresholdUnit")
    read_interval_minutes: int = Field(default=10, alias="readIntervalMinutes", ge=1)
    for_minutes: int = Field(default=5, alias="forMinutes", ge=0)
    cooldown_minutes: int = Field(default=60, alias="cooldownMinutes", ge=0)
    target_webhook_ids: list[str] = Field(default_factory=list, alias="targetWebhookIds")
    include_resolved: bool = Field(default=True, alias="includeResolved")
    include_snapshot: bool = Field(default=True, alias="includeSnapshot")


class NotificationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    webhooks: list[NotificationWebhook] = Field(default_factory=list)
    rules: list[NotificationRule] = Field(default_factory=list)


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
    rule_config: dict[str, Any] | None = None
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
