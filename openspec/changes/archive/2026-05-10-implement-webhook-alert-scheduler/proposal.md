## Why

Sprint A delivered persistence and on-demand test deliveries for the webhook alert center, but no rule actually evaluates anything on its own. Operators have to remember to hit the test endpoint, and there is no way to be alerted when a real signal crosses a threshold. This change adds the periodic evaluator, sustained-for / recovery / cooldown semantics, and the routing noise controls (quiet hours, repeat interval) that the UI already lets operators configure.

Signal collectors stay narrowly scoped: most platform/admin/account information signals depend on Sub2API admin endpoints that are not yet confirmed in this codebase. The scheduler is wired so that an unimplemented collector returns "no data" cleanly instead of crashing, and individual collectors can be filled in incrementally as upstream endpoints are verified.

## What Changes

- Add a periodic `NotificationScheduler` that, on each tick, lists enabled rules and runs every rule whose `readIntervalMinutes` window has elapsed.
- Add a `SignalCollector` registry keyed by `signalKey`. Provide one stub per signal key from the UI catalog. Default behavior is "no data, do nothing"; collectors can be filled in as Sub2API admin endpoints are verified.
- Add an evaluator that consumes a collector sample plus the rule configuration and returns a decision (`triggering`, `recovering`, `quiet`).
- Track per-rule state in SQLite: `last_value`, `breach_started_at`, `last_alert_at`, `is_firing`. State is consulted to enforce sustained-for, recovery, repeat-interval, and cooldown.
- Skip alert deliveries during the configured quiet-hours window, but still update rule state so resumption is correct after the window ends.
- Add `POST /notifications/evaluate` so an authenticated operator can run a single rule once on demand and see the decision and outbound deliveries (mirrors the test endpoint but uses the real collector path).
- Wire the scheduler into the FastAPI lifespan so it starts when notifications are configured and is cleanly stopped on shutdown.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `orchestration-dashboard`: Add backend rule evaluation, sustained/recovery/cooldown semantics, quiet-hours and repeat-interval routing, and on-demand evaluation API for the webhook alert center.

## Impact

- Affected code: `app/main.py`, `app/models/notification.py`, `app/services/notification.py`, new `app/services/notification_scheduler.py`, new `app/services/notification_collector.py`, new `app/services/notification_evaluator.py`, `app/stores/sqlite.py`, tests
- Affected APIs: new `POST /notifications/evaluate`; existing `/notifications/*` endpoints remain compatible
- Operator workflow: enabled rules now fire deliveries automatically based on configured cadence and thresholds; operators can still trigger a single rule on demand via the evaluate endpoint
- Out of scope: actually pulling real values from Sub2API admin endpoints. Collector stubs report "no data" so the scheduler can run without false alerts. Each collector is replaced by a real implementation as upstream endpoints are confirmed.
