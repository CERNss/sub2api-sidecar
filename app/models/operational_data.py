from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.flow import AssignmentMode


class OperationalMetricSample(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    metric_key: str
    value: float
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    snapshot: dict[str, Any] | None = None

    def collector_sample(self) -> "CollectorSample":
        from app.models.notification import CollectorSample

        return CollectorSample(
            value=self.value,
            observed_at=self.observed_at,
            snapshot=self.snapshot,
        )


class OperationalDataSourceStatus(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_key: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    error_message: str | None = None
    item_count: int | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OperationalDataSnapshot(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_key: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: Any


class OperationalDataRuntimeSettings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    enabled: bool = True
    expiration: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CreditControlRuntimeSettings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProvisioningRuntimeSettings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    assignment_mode: AssignmentMode = AssignmentMode.dedicated
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
