from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.flow import AssignmentMode


class OperationalMetricSample(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    metric_key: str
    value: float
    scope_key: str = ""
    scope_label: str = ""
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    snapshot: dict[str, Any] | None = None

    def collector_sample(self) -> "CollectorSample":
        from app.models.notification import CollectorSample

        return CollectorSample(
            value=self.value,
            scope_key=self.scope_key,
            scope_label=self.scope_label,
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
    collect_interval_seconds: int = Field(default=60, ge=5)
    expiration: int | None = None
    retention_seconds: int | None = Field(default=None, gt=0)
    max_storage_mb: int | None = Field(default=None, gt=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OperationalDataCleanupResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    cleaned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    retention_cutoff: datetime | None = None
    size_limit_bytes: int | None = None
    storage_bytes_before: int = 0
    storage_bytes_after: int = 0
    deleted_metric_samples: int = 0
    deleted_snapshots: int = 0


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
