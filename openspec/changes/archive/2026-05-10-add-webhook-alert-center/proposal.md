## Why

Operators need a configurable alert center for the sidecar dashboard so operational signals from Sub2API, platform API keys, users, admin dashboards, and upstream AI accounts can be routed to the right webhook receivers with explicit thresholds and polling cadence.

The initial notification page was too close to a simple webhook form. This change makes the feature match common alert-center concepts: receivers, rules, thresholds, evaluation windows, routing, repeat intervals, recovery notifications, and quiet hours.

## What Changes

- Add a top-level `通知设置` / alert-center view beside `编排工作台` and `OAuth 预配`.
- Add Webhook receiver management for name, URL, optional secret, enabled state, and failure mention behavior.
- Add alert rules that bind one information signal to threshold, operator, aggregation, read frequency, evaluation window, sustained duration, repeat interval, severity, recovery behavior, payload snapshot behavior, and target webhooks.
- Add signal categories for platform API key health/quota/expiry/usage, user balance/API key/subscription usage, admin operations/payment/channel anomalies, and AI upstream account health/rate-limit/quota/auth/capacity.
- Add global routing and noise-control settings for grouping, group wait, repeat interval, and quiet hours.
- Persist the current UI configuration in browser local storage as a front-end implementation step until backend persistence and execution APIs are added.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `orchestration-dashboard`: Add authenticated operator UI behavior for configuring webhook receivers, alert rules, thresholds, read cadence, and notification routing.

## Impact

- Affected code: `frontend/src/App.tsx`, `frontend/src/styles.css`
- Affected UI: top-level React dashboard navigation and notification/alert configuration workspace
- Future backend impact: alert configuration persistence, signal collectors, scheduler, threshold evaluator, webhook delivery, delivery audit/history, and tests
- Operational impact: operators can model which webhooks receive which classes of data and tune cadence/thresholds before backend execution is wired
