from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class ProxyHealthRuntimeSettings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    enabled: bool = True
    probe_interval_seconds: int = Field(default=60, ge=5)
    quality_check_interval_seconds: int = Field(default=300, ge=30)
    failure_threshold: int = Field(default=3, ge=1)
    recovery_threshold: int = Field(default=3, ge=1)
    auto_move_enabled: bool = True
    # Quality-check targets that must pass for a proxy to count as healthy. The
    # sidecar's verdict is intentionally stricter than the upstream's own
    # "connectivity ok" status: accounts here are OpenAI-platform, so a proxy that
    # cannot reach OpenAI is unusable even when its exit is reachable.
    critical_targets: list[str] = Field(default_factory=lambda: ["openai"])
    # A reachable-but-slow proxy also fails the round when its probe latency
    # exceeds this bound. None disables the latency criterion.
    latency_threshold_ms: int | None = Field(default=10_000, ge=1)
    # Proxies (matched by id or name) whose death should not raise alerts. They
    # are still probed and still evicted/rebalanced — only the alarm is muted.
    alert_whitelist: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProxyHealthState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    proxy_id: str
    proxy_name: str = ""
    health: str = "unknown"  # unknown | healthy | dead
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_probe_at: datetime | None = None
    last_probe_success: bool | None = None
    last_probe_latency_ms: float | None = None
    last_probe_error: str | None = None
    last_quality_at: datetime | None = None
    last_quality_score: float | None = None
    last_quality_grade: str | None = None
    last_quality_summary: str | None = None
    failing_critical_targets: list[str] = Field(default_factory=list)
    last_transition_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProxyParkedAccount(BaseModel):
    """An account force-moved to direct connection because every proxy was dead.

    Parked accounts are re-proxied (and unparked) by the next rebalance that has
    an eligible proxy; genuinely-direct accounts are never in this set and are
    never touched.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    account_id: str
    account_name: str = ""
    parked_from_proxy_id: str | None = None
    parked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProxyAccountPin(BaseModel):
    """An account manually bound to one specific proxy by an operator.

    Pinned accounts are excluded from the even-split rebalance: as long as their
    proxy is eligible they stay on (or are returned to) it. When that proxy dies
    they are rebalanced like any other account so traffic keeps flowing, but the
    pin record survives so a later recovery sends them home.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    account_id: str
    account_name: str = ""
    proxy_id: str
    pinned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProxyAccountMove(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    account_id: str
    account_name: str = ""
    from_proxy_id: str | None = None
    to_proxy_id: str | None = None
    status: str = "planned"  # planned | moved | skipped | failed
    reason: str | None = None
    pinned: bool = False


class ProxyHealthRun(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    trigger: str  # proxy_dead | proxy_recovered | manual
    dry_run: bool = False
    status: str = "noop"  # noop | completed | partial_failed | failed
    reason: str | None = None
    fallback_direct: bool = False
    dead_proxy_ids: list[str] = Field(default_factory=list)
    eligible_proxy_ids: list[str] = Field(default_factory=list)
    moves: list[ProxyAccountMove] = Field(default_factory=list)
    moved_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProxyProbeResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    probed_count: int = 0
    quality_checked_count: int = 0
    dead_count: int = 0
    all_proxies_down: bool = False
    transitions: list[str] = Field(default_factory=list)
    runs: list[ProxyHealthRun] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
