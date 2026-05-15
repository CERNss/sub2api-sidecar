## Why

Notification evaluation currently pulls upstream Sub2API data inside each rule evaluation. That makes multiple rules repeat the same API reads, hides whether the system is actively collecting data, and makes sustained capacity alerts depend on ad hoc live reads instead of a durable sample history.

## What Changes

- Replace the previous per-rule live collector design with a shared operational data pipeline: collect upstream data, persist local snapshots and metrics, then let notifications evaluate rules from local samples.
- Use neutral operational data runtime settings for collection enablement and data expiration.
- Add a periodic operational data collection stage that fetches the required Sub2API datasets on a fixed cadence and persists raw snapshots plus normalized metric samples into SQLite.
- Change scheduled rule evaluation to read the latest persisted local samples instead of calling upstream collectors per rule.
- Preserve on-demand evaluation semantics while making it use the same local sample source after an immediate sampling refresh.
- Expose sampling status, per-source status, and expired/error information through the existing scheduler status path.
- Keep rule-level `readIntervalMinutes`, `forMinutes`, and `cooldownMinutes` behavior for evaluation cadence and notification suppression.

## Capabilities

### New Capabilities

### Modified Capabilities
- `orchestration-dashboard`: Notification rule evaluation changes from per-rule live upstream collection to periodic local sampling plus local rule evaluation.

## Impact

- Affected code: notification services, the old collector integration, scheduler, SQLite store, API status schemas, tests, and runtime-settings docs.
- Affected APIs: `GET /notifications/scheduler` gains sampling status fields; `POST /notifications/evaluate` refreshes local samples before evaluating a single rule.
- Runtime settings: the pipeline uses only the SQLite-backed operational data runtime settings.
- Storage: adds durable operational data snapshot, metric sample, and source-status tables to SQLite.
- No breaking API removals are expected.
