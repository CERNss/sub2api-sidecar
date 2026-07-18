from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class AccountHealthRuntimeSettings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    enabled: bool = True
    check_interval_seconds: int = Field(default=60, ge=5)
    failure_threshold: int = Field(default=3, ge=1)
    recovery_threshold: int = Field(default=3, ge=1)
    auto_evict_enabled: bool = True
    # Availability statuses treated as transient (upstream usually self-heals;
    # eviction still applies after the threshold, rejoin is automatic).
    transient_statuses: list[str] = Field(
        default_factory=lambda: ["rate_limited", "temporary_unschedulable"]
    )
    # Statuses that need human intervention: evicted accounts only rejoin after
    # the underlying signal actually clears (detected healthy again).
    persistent_statuses: list[str] = Field(
        default_factory=lambda: ["needs_reauth", "needs_verify", "banned", "unavailable"]
    )
    # While an evicted account still looks bad, actively trigger the upstream
    # account test at this interval so recovery (e.g. after a manual re-auth)
    # is detected even if passive signals stay frozen.
    recovery_test_interval_seconds: int = Field(default=300, ge=30)
    # Accounts (matched by id or name) whose eviction should not raise alerts.
    alert_whitelist: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AccountHealthState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    account_id: str
    account_name: str = ""
    health: str = "unknown"  # unknown | healthy | bad
    classification: str | None = None  # transient | persistent
    evicted: bool = False  # schedulable=false was set by the sidecar
    evicted_by: str | None = None  # auto | manual
    # Set before every eviction attempt: lets a half-executed toggle (applied
    # upstream, response lost) be adopted instead of misread as an admin pause.
    evict_attempted_at: datetime | None = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_availability_status: str | None = None
    last_error: str | None = None
    last_recovery_test_at: datetime | None = None
    last_transition_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AccountHealthAction(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    account_id: str
    account_name: str = ""
    action: str  # evict | rejoin
    classification: str | None = None
    status: str = "done"  # done | failed
    reason: str | None = None


class AccountHealthRun(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    trigger: str  # auto | manual
    actions: list[AccountHealthAction] = Field(default_factory=list)
    evicted_count: int = 0
    rejoined_count: int = 0
    failed_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AccountReconcileResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    checked_count: int = 0
    bad_count: int = 0
    evicted_total: int = 0
    transitions: list[str] = Field(default_factory=list)
    run: AccountHealthRun | None = None
    errors: list[str] = Field(default_factory=list)
