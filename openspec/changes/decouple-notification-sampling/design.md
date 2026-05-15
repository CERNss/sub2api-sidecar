## Context

Notification evaluation currently couples rule timing, upstream data collection, threshold evaluation, and webhook delivery inside `NotificationService.tick()`. Each due rule invokes a collector for its own `signalKey`, so multiple rules can repeat the same Sub2API reads and there is no durable record of what the scheduler has recently observed.

This change intentionally discards that collector-as-rule-input design. The new operational data pipeline owns data collection as a separate runtime concern and exposes neutral runtime settings through SQLite/API/UI.

The desired shape is a shared two-stage operational data pipeline:

1. A collection step runs on the pipeline interval, fetches Sub2API datasets in a deterministic order, derives all supported notification signal samples, and writes them to SQLite.
2. Rule evaluation runs on the rule cadence and reads local samples from SQLite.

## Goals / Non-Goals

**Goals:**
- Collect Sub2API data once per scheduler tick and reuse it for all notification rules.
- Persist latest and historical operational snapshots and metric samples in SQLite.
- Evaluate scheduled rules from local samples while preserving `readIntervalMinutes`, `forMinutes`, `cooldownMinutes`, recovery, and delivery semantics.
- Make operational-data status report sampling freshness and per-source errors.
- Keep on-demand evaluation useful by refreshing samples before evaluating one rule.
- Introduce neutral operational data runtime settings as the source of truth for optional data expiration and enabled state.
- Explicitly document the data source used by each pipeline stage.

**Non-Goals:**
- Rewriting webhook delivery adapters.
- Changing the user-facing rule configuration schema.
- Building a frontend sample-history browser in this change.
- Adding a separate external time-series database.
- Preserving the old per-rule collector design as an alternate runtime path.

## Decisions

### Use SQLite as the sample store

Samples will be stored in SQLite tables owned by the existing `SQLiteFlowStore`. This keeps deployment simple and matches the service's existing durable state model.

Alternative considered: keep samples in process memory only. That would be faster but would lose restart visibility and would not solve the "prove what was last sampled" problem.

### Use operational data runtime settings only

Add SQLite-backed runtime settings edited through authenticated API/UI, not through `config.yaml`:

```json
{
  "enabled": true,
  "expiration": null
}
```

The collection cadence is the service-owned 60 second operational cadence and has no user-facing config field. `expiration` is optional and measured in seconds; when it is unset, persisted local operational data does not expire.

### Add a snapshot-based collector

Introduce an `OperationalDataCollector` service that:
- Fetches required upstream datasets in this order: accounts, groups, users, usage.
- Persists raw source snapshots for shared consumers such as notifications and automatic orchestration.
- Computes one latest metric sample per supported notification `signalKey`.
- Persists metric samples and per-source collection status.

The first implementation data sources are:

| Stage | Data Source | Purpose |
| --- | --- | --- |
| Collection | `Sub2APIClient.list_openai_accounts()` | account invalid/rate-limited/reauth/quota/capacity/platform-key signals and grouped capacity derived from account group membership |
| Collection | `Sub2APIClient.list_groups(platform="openai")` | admin group/channel health and source visibility for group-oriented snapshots |
| Collection | `Sub2APIClient.list_users()` | user balance and user API-key/account-state style signals |
| Collection | `Sub2APIClient.get_usage_stats(user_id="", start_date=today, end_date=today, timezone_name=Asia/Shanghai)` | subscription usage, user usage summary, payment usage proxy, and current-day admin usage |
| Collection | `Sub2APIClient.get_usage_stats(user_id="", start_date=yesterday, end_date=yesterday, timezone_name=Asia/Shanghai)` | previous-day comparison for admin usage anomaly |
| Persistence | SQLite `operational_data_snapshots` | raw source snapshots keyed by source key and collection time for shared consumers |
| Persistence | SQLite `operational_metric_samples` | historical metric samples keyed by metric key and collection time |
| Persistence | SQLite latest-query indexes | latest snapshot and metric lookup for consumers |
| Persistence | SQLite `operational_data_source_statuses` | per-source collection status, item count, timestamps, and error messages |
| Evaluation | SQLite notification config | rule definitions, receiver routing, and rule cadence |
| Evaluation | SQLite operational metric samples | latest non-expired metric sample for each rule signal |
| Evaluation | SQLite notification rule states | sustained-for, cooldown, firing, recovery, and last-evaluation state |

### Keep the evaluator unchanged

`evaluate_rule()` already has the correct threshold, sustained-for, cooldown, and recovery behavior when given a sample. The change should adapt data sourcing, not rewrite the evaluator.

### Refresh samples for on-demand evaluation

`POST /notifications/evaluate` should call the collector before reading the requested rule's sample. That makes manual checks reflect the current upstream state while still exercising the same local-sample evaluation path as the scheduler.

## Risks / Trade-offs

- Local samples can expire if upstream collection fails and runtime `expiration` is configured -> Store per-source errors and expose sampling status in `/api/operational-data/status`; expired or missing samples evaluate as `no_data`.
- A single sampling step can fail before all signals are computed → Persist status per source and keep previous samples available, but mark failure so operators can see the problem.
- Existing tests that registered fake collectors directly may need to shift to seeded samples or fake samplers → Prefer tests that verify local-sample evaluation and one sampling call per tick.

## Migration Plan

1. Add new operational data runtime settings and migrate runtime construction to those settings.
2. Add SQLite tables for operational snapshots, metric samples, and source statuses with `CREATE TABLE IF NOT EXISTS`.
3. Add models and store methods for saving/listing latest snapshots, metric samples, and source statuses.
4. Add `OperationalDataCollector` and inject it into `NotificationService`.
5. Change scheduled and on-demand evaluation to refresh/read local samples.
6. Expose `/api/operational-data/status` with collection status.
7. Keep existing rule state and delivery tables unchanged.
