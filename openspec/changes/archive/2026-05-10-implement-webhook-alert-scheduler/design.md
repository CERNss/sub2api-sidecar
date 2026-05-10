## Context

Sprint A (`implement-webhook-alert-backend`) made notification configuration durable, taught the sidecar to deliver via six provider adapters, and added an audit log. What's missing: nothing actually runs unless an operator clicks "test". This change closes the loop — periodic evaluation, sustained breach detection, recovery, cooldown, quiet hours, and repeat-interval suppression.

The harder constraint: most signals in the UI catalog (`platform_key_quota`, `account_invalid`, `admin_usage_anomaly`, etc.) reference Sub2API admin endpoints that may or may not exist in the deployed upstream. We refuse to invent collectors that hit endpoints we have not verified. Instead, every signal key has a slot in a collector registry, the default slot returns `None` (no data), and the scheduler treats `None` as "nothing to evaluate this tick" rather than as a failure. As upstream endpoints are confirmed, collectors are replaced one at a time without changing the scheduler or evaluator.

## Goals / Non-Goals

**Goals:**

- Periodic, durable rule evaluation that survives restarts via persisted rule state.
- Sustained-for and recovery semantics that match the threshold-engineering literature: a rule must breach for at least `forMinutes` before firing, and (when configured) must stay below `recoveryThreshold` to recover.
- Repeat-interval suppression so a still-firing rule does not spam every tick.
- Quiet hours that suppress sends without losing track of state.
- An on-demand `POST /notifications/evaluate` endpoint that runs a single rule through the real collector path and returns the decision and per-receiver outcomes.
- Minimum-surprise integration: scheduler is daemon-thread, exits cleanly on lifespan shutdown.

**Non-Goals:**

- Real implementations of every `SignalCollector`. Stubs are the deliverable; real upstream queries land per-signal in follow-up work.
- Rule grouping (`groupBy`, `groupWaitMinutes`) beyond a passthrough hook. The UI already saves the policy; activating grouping is a separate problem (it changes message shape and storage).
- Distributed scheduling. The scheduler is a single in-process thread, matching `AutoRotationScheduler`.
- Backfill / historical re-evaluation.

## Decisions

### 1. Scheduler is one daemon thread, lifespan-managed

`NotificationScheduler` mirrors `AutoRotationScheduler`: an `Event`-gated `while not self._stop_event.wait(self.tick_seconds)` loop. The tick interval is intentionally short (e.g. 30 seconds default) so per-rule `readIntervalMinutes` granularity stays close to the configured cadence without busy-waiting.

**Why this approach:**

- Reuses the proven shape from rotation scheduler; the same lifespan teardown protocol applies.
- Lets `readIntervalMinutes` be enforced in the rule-state layer rather than as multiple OS timers.
- Keeps the worker single-threaded, so SQLite writes from the scheduler do not contend with API writes.

### 2. Per-rule state lives in SQLite

A new `notification_rule_states` table stores `(rule_id, last_evaluated_at, last_value, breach_started_at, last_alert_at, is_firing, last_error, updated_at)`. The scheduler reads + writes this row each tick.

**Why this approach:**

- Sustained-for needs to know when the breach started. We persist it so a restart does not reset the window.
- Repeat-interval needs `last_alert_at`. Persisting avoids re-firing immediately after a restart.
- One row per rule is small and bounded; no need for a separate event log when the existing `notification_deliveries` audit covers actual sends.

### 3. Evaluator is a pure function

`evaluate_rule(rule, sample, prior_state, now)` returns a `RuleDecision` with `action ∈ {fire, recover, hold, no_data, suppress}` plus next-state. The caller (scheduler, evaluate endpoint) is responsible for persisting state and dispatching deliveries.

**Why this approach:**

- Easy to test: feed prior state + sample, assert next decision and state.
- Lets the on-demand `POST /notifications/evaluate` reuse exactly the same logic as the scheduler tick.
- Keeps quiet-hours suppression (`suppress`) distinct from "no breach" (`hold`) so audit trails stay honest.

### 4. Collectors return Optional samples

`SignalCollector.collect(rule) -> CollectorSample | None`. `None` means "no data this tick — do not evaluate". Stubs return `None` for all current signal keys. A real collector returns `CollectorSample(value=..., observed_at=..., snapshot={...})`.

**Why this approach:**

- Avoids falsy magic numbers (e.g. `0`) being treated as breaches when the real value is "I don't know".
- Lets us ship the scheduler without false alerts while collectors remain stubbed.
- Surfaces "this signal has no implementation yet" via the `last_error` state field so operators see what's stubbed.

### 5. Quiet hours suppress send, not state

When `policy.quietHoursEnabled`, the dispatcher checks the current local time against `[quietHoursStart, quietHoursEnd]`. During quiet hours, decision is `suppress` and an audit row with status `skipped` is written; rule state still updates so when the window ends the firing/recovery transitions are correct.

**Why this approach:**

- Operators expect "do not page me at 3am" semantics. Losing state would mean an alert that started during quiet hours is forgotten.
- The audit row gives operators a way to see what was suppressed.

### 6. Repeat-interval is per-rule

Each firing rule re-fires only after `cooldownMinutes` have passed since `last_alert_at`. While firing, intermediate evaluations report `hold`. This is independent of the routing-policy `repeatIntervalMinutes` (which would govern grouped re-sends and is out of scope).

**Why this approach:**

- The UI already labels `cooldownMinutes` as the per-rule repeat interval. We respect what's saved.
- Avoids two interacting timers (rule cooldown + policy repeat interval) before grouping is implemented.

## Future Backend Contract

Slots that the next change can fill in:

- `SignalCollector` real implementations per `signalKey` once Sub2API admin endpoints are confirmed.
- Rule grouping by `groupBy` policy with `groupWaitMinutes` aggregation window before send.
- Multi-tenant or shared-state scheduling beyond a single in-process thread.

## Risks / Trade-offs

- **Default-stub collectors mean enabled rules silently do nothing.** Mitigated: `last_error` field surfaces "collector not implemented for signal X". Operators can see at a glance which rules have data and which are awaiting upstream wiring.
- **Quiet-hours uses server local time.** Acceptable for a sidecar that runs on a known host. If multi-tz support is needed, add a `timezone` field to the policy in a future change.
- **Single-thread scheduler under heavy rule counts.** Hundreds of rules at minute cadence is fine for SQLite. If counts grow past that, batch reads or move to a queued worker.
- **No grouping yet.** Tag deliveries with `trigger='rule'` so a future grouping change can group by `trigger + rule_id` without schema migration.
