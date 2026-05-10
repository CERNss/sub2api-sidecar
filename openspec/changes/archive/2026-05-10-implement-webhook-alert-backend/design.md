## Context

`add-webhook-alert-center` shipped the React notification settings view but explicitly deferred backend work. Operators currently rely on browser localStorage for configuration and the "test delivery" button only validates UI state. Without a backend, no real webhook reaches a receiver, no audit history exists, and configuration vanishes when localStorage is cleared.

This change adds the minimum backend that delivers value end-to-end: server-side persistence, real outbound deliveries, and audit history. Periodic signal collection and threshold evaluation are still deferred to a follow-up change so we can confirm Sub2API admin endpoints first instead of inventing collector contracts that may not exist.

## Goals / Non-Goals

**Goals:**

- Persist the full `NotificationSettings` shape (receivers + rules + routing policy) in SQLite as a single config row.
- Support all six provider types from the UI (`generic`, `feishu`, `dingtalk`, `wecom`, `slack`, `discord`) with provider-specific payload and signing semantics.
- Deliver real test payloads via `POST /notifications/test` and audit every attempt.
- Expose recent delivery attempts via `GET /notifications/deliveries` so operators can debug routing without reading server logs.
- Stay forward-compatible with the still-pending scheduler/evaluator work.

**Non-Goals:**

- Periodic scheduler that reads enabled rules by `readIntervalMinutes`.
- Signal collectors keyed by `signalKey`. The proposal acknowledges these depend on Sub2API admin endpoints that need separate verification.
- Evaluator with aggregation, evaluation window, sustained-for, recovery, grouping, quiet hours, or repeat interval execution. Configuration for these is persisted today; behavior is added in the follow-up change.
- Encrypting the saved `secret` field at rest. The same trust boundary as the existing `sub2api_admin_api_key` applies; the secret never leaves the server in any response.

## Decisions

### 1. Single-row blob persistence

Notification configuration is a small, atomic, operator-edited document. We store the entire `NotificationSettings` JSON as one row in `notification_config` keyed by `config_key='default'` (mirroring `auto_rotation_config`). PUT replaces the entire document.

**Why this approach:**

- Matches the UX where the operator hits "保存" and expects the saved state to be exactly what the form shows.
- Avoids modeling receivers, rules, and routing as separate tables only to rejoin them on every read.
- Keeps round-trip identical to the localStorage shape, so the migration path from the front-end-only phase is trivial.

**Alternative considered:** Separate tables for receivers, rules, and routing. Rejected because the join overhead has no value and partial updates are not part of the UX.

### 2. Provider adapter registry

Each receiver `provider` value maps to an adapter that knows the payload schema and signing scheme. Adapters expose a single function `build_request(receiver, message) -> PreparedRequest` so the delivery worker stays provider-agnostic.

**Why this approach:**

- Centralizes provider quirks (`feishu` HMAC SHA256 with `timestamp:secret`; `dingtalk` HMAC SHA256 base64 with timestamp query; `slack` raw text; etc.).
- Makes adding a new provider mechanical: register the adapter, add a UI option.
- Lets the test endpoint share exactly the same delivery code path as the future scheduler.

**Alternative considered:** A single switch in the delivery worker. Rejected because provider semantics are non-trivial (mention behavior, signature placement, message shape) and tests are easier when each provider is isolated.

### 3. Audit log is append-only and indexed by created_at

Every delivery attempt — successful, failed, and retry — writes one row to `notification_deliveries`. The audit row stores `attempt_index` so retries are visible. We do not store full request bodies (operators do not need them and they may contain PII or secrets); we store status, error message, and a SHA256 digest of the outbound payload.

**Why this approach:**

- Lets operators see "did anything actually go out" without reading logs.
- Avoids long-term retention of payload contents that may include sensitive operational data.
- Keeps row size small, which matters when scheduler-driven deliveries arrive in bulk.

### 4. Test endpoint reuses the delivery worker

`POST /notifications/test` builds a labelled `NotificationMessage` (rule name, severity, signal key, "this is a test"), then runs it through the same provider adapter + delivery worker that the future scheduler will use.

**Why this approach:**

- Operators get an honest signal: if the test fails, real alerts will fail too.
- The delivery contract has exactly one implementation, not "test send" and "real send" diverging.
- Audit rows from test deliveries are tagged via `trigger='test'` so they can be filtered out of normal operational dashboards later.

### 5. Tolerate legacy receiver-only configuration

If the saved config (or migration source) only has `webhooks` populated and no `rules`/`policy`, the loader synthesizes default rules routed to the first receiver, mirroring the existing front-end tolerance behavior. This keeps older operators migrating from the localStorage-only phase from losing their receivers.

## Future Backend Contract

This change exposes the surface area the next change will plug into:

- `NotificationConfigService.get_config()` returns the saved `NotificationSettings`. The future scheduler reads enabled rules from here.
- `NotificationDeliveryService.send(receiver, message)` is the delivery seam; collectors/evaluator will call this with computed alert messages.
- `notification_deliveries` table absorbs all real-traffic and scheduler deliveries with `trigger` field discriminating `'test'`, `'rule'`, and (future) `'recovery'`.

## Risks / Trade-offs

- **Provider quirks evolve.** Feishu/dingtalk signing schemes have shifted before. Adapters are isolated so a future change rewrites only one file per provider.
- **Configuration grows past the 1MB row size.** With current rule counts (≤ tens) the blob is well under that. If it grows, splitting tables is mechanical.
- **No rate limiting on `POST /notifications/test`.** Authenticated operator only; abuse risk is low. If misused, audit rows make detection trivial.
- **Secrets are stored in plaintext.** Same trust boundary as `SUB2API_ADMIN_API_KEY` in `.env` — operator-only SQLite file. We never echo `secret` back in any GET response (the schema marks it write-only-from-PUT, redacted on GET).
