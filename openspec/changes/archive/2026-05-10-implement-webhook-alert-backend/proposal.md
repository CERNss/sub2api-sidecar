## Why

The webhook alert center UI is in place but `add-webhook-alert-center` deliberately deferred the backend. Configuration only lives in `localStorage`, no real webhook delivery happens, and there is no audit trail. Operators cannot trust the alert center until the backend persists configuration server-side, can deliver to the configured providers, and records every delivery attempt.

## What Changes

- Add authenticated `GET /notifications/config` and `PUT /notifications/config` backed by SQLite so receivers, rules, and routing policy survive restarts.
- Add `POST /notifications/test` so an operator can trigger a real test payload to a saved rule's webhooks and read the per-receiver delivery outcome.
- Implement provider adapters for `generic`, `feishu`, `dingtalk`, `wecom`, `slack`, and `discord` so each receiver gets a payload and signing scheme that matches its target.
- Add a delivery worker that handles signing, timeout, retry with backoff, and per-attempt audit history.
- Add `GET /notifications/deliveries` so operators can inspect recent delivery attempts (status, provider, error).
- Tolerate legacy receiver-only configuration on load so older saved state in localStorage or in-flight migrations do not lose receivers.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `orchestration-dashboard`: Add backend delivery, audit, and persistence behavior for the webhook alert center configuration.

## Impact

- Affected code: `app/main.py`, `app/models/schemas.py`, new `app/models/notification.py`, new `app/services/notification.py`, new `app/services/notification_delivery.py`, `app/stores/sqlite.py`, tests
- Affected APIs: `GET /notifications/config`, `PUT /notifications/config`, `POST /notifications/test`, `GET /notifications/deliveries`
- Operator workflow: configuration now persists server-side and the test action delivers a real payload; operators can audit failures from `GET /notifications/deliveries`
- Out of scope (next change): periodic scheduler, signal collectors keyed by `signalKey`, evaluator with aggregation/window/sustained/recovery/grouping/quiet-hours. The backend exposes a stable extension surface so those can be added without changing the configuration contract.
