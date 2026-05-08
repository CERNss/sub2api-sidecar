## Context

The sidecar dashboard already has authenticated top-level views for existing-resource orchestration and OAuth provisioning. Operators now need a webhook alert configuration view that reflects how mature alerting systems are organized.

Common alert-center patterns separate:

- receivers/contact points: where notifications are sent
- alert rules/monitors: what data is evaluated and under what threshold/window
- routing policies: which alerts go to which receiver and how noise is controlled
- notification behavior: repeat interval, recovery notification, grouping, quiet hours, and payload content

This change applies that model to the existing React UI. It does not yet implement backend scheduling, data collection, or webhook delivery.

## Goals / Non-Goals

**Goals:**

- Add a top-level notification settings view that fits the operational dashboard theme.
- Let operators add multiple webhook receivers.
- Let operators define alert rules against known information signals from platform keys, users, admin operations, and AI upstream accounts.
- Let operators configure threshold, operator, aggregation, read interval, evaluation window, sustained duration, repeat interval, severity, recovery notification, snapshot payload, and target webhook routing.
- Preserve current config locally so the UX can be exercised before backend persistence exists.

**Non-Goals:**

- Sending real webhook requests from the browser.
- Polling Sub2API/admin/user/account endpoints from the browser.
- Building a backend alert scheduler, delivery worker, retry queue, or delivery history in this change.
- Persisting alert configuration server-side in SQLite in this change.

## Decisions

### 1. Model receivers and rules separately

Webhook receivers store only delivery destination metadata: name, URL, optional secret, enabled state, and mention behavior. Rules store signal selection, thresholds, cadence, routing, and payload options.

Why this approach:

- matches mature alerting products where contact points are reusable
- lets multiple rules target one webhook or one rule target multiple webhooks
- avoids duplicating threshold/cadence settings inside every receiver

Alternative considered: keep each webhook as a set of checked information types. Rejected because it cannot represent threshold, frequency, evaluation window, recovery, or routing policies cleanly.

### 2. Encode operational information as first-class signal metadata

The UI keeps a typed catalog of supported signals. Each signal includes label, description, source, unit, default threshold, default operator, default aggregation, default severity, default read interval, and default evaluation window.

Why this approach:

- keeps the UI understandable before backend collectors exist
- gives operators sensible defaults when changing signal type
- makes the future backend contract easier because each rule references a stable `signalKey`

Alternative considered: free-form signal names. Rejected because operators need constrained, explainable choices tied to known data sources.

### 3. Use browser localStorage as a temporary persistence layer

The current implementation serializes receivers, rules, and routing policy to localStorage. It also tolerates earlier receiver-only saved state by generating default rules routed to the first receiver.

Why this approach:

- lets the UI behavior be validated immediately without inventing incomplete backend APIs
- avoids losing operator changes during refresh in the current front-end-only phase
- preserves a migration path to server-side configuration later

Alternative considered: only keep React state. Rejected because refresh would lose the configuration and make the view feel unfinished.

### 4. Keep delivery tests as validation, not actual sends

The "发送测试" action validates that the selected rule targets at least one enabled webhook with a URL and reports that a test payload is ready. It does not send from the browser.

Why this approach:

- avoids leaking secrets or implementing cross-origin delivery in the browser
- keeps delivery semantics for the future backend worker, where retries, signing, and audit logs belong
- still gives immediate feedback on invalid configuration

Alternative considered: browser-side `fetch` to webhook URL. Rejected because production webhook sending should be server-side and auditable.

## UI Structure

- Top toolbar:
  - `编排工作台`
  - `OAuth 预配`
  - `通知设置`
- Notification workspace:
  - left/main panel:
    - Webhook receiver list and receiver editor
    - Alert rule list and rule editor
    - Routing and noise-control section
    - save/test/delete actions
  - right summary panel:
    - receiver/rule counts
    - receiver routing summary
    - rule threshold/cadence summary
    - payload scope hint

## Future Backend Contract

The UI is shaped so the backend can later expose:

- `GET /notifications/config`
- `PUT /notifications/config`
- `POST /notifications/test`
- alert scheduler that reads enabled rules by `readIntervalMinutes`
- collectors keyed by `signalKey`
- evaluator that applies aggregation/window/operator/threshold/recovery
- webhook delivery worker with retries, signing, quiet-hours, grouping, and delivery audit records
- per-provider sender registry that maps each receiver's `provider` field (`generic`, `feishu`, `dingtalk`, `wecom`, `slack`, `discord`) to its payload schema, signature scheme, and mention semantics; the UI stays platform-agnostic and the backend owns provider-specific formatting

## Risks / Trade-offs

- [UI looks complete but delivery is not wired] -> The test action text says backend delivery is needed; OpenSpec tasks mark backend persistence/execution as future work.
- [Rule catalog drifts from real backend data] -> Use stable `signalKey` values and source labels tied to known API areas.
- [localStorage config shape changes] -> The loader tolerates receiver-only saved state and fills missing rule/policy defaults.
- [Dense operational UI overwhelms mobile] -> Layout collapses to single-column on narrow screens, and controls use concise field labels.
