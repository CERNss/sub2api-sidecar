## Context

The alert center was modelled after Prometheus + Alertmanager: dual-threshold hysteresis, aggregation operators, separate read interval vs. evaluation window, per-rule cooldown plus policy-level repeat interval plus quiet hours plus group wait. But the sidecar:

- doesn't have a time-series database — collectors return point samples
- doesn't have an Alertmanager — webhooks are sent directly
- doesn't have an on-call/owner model — `mentionOnFailure` has nothing to mention
- monitors mostly discrete booleans (account invalid, key expiring, low balance) where recovery thresholds and aggregations buy nothing

The current data model accumulates field cost on every layer (TS types, FastAPI models, SQLite document, scheduler, evaluator, delivery, UI, summary). Each new field doubles its surface area.

## Decisions

### D1 — Drop policy entirely

Remove `NotificationRoutingPolicy` and the `policy` block from the settings document. `groupBy` / `groupWaitMinutes` / `repeatIntervalMinutes` / `quietHours*` are not consumed by any delivery aggregation layer — the scheduler dispatches per-rule.

Per-rule `cooldownMinutes` already handles "don't spam the same alert" and we keep it.

Quiet hours are useful but out-of-scope; a future change can reintroduce them at the rule level if needed (`rule.quietHours: optional`), not as a global policy. Removing the global setting now avoids the silent-suppression footgun: an admin sees "fire" decisions in the scheduler log without realising delivery was suppressed by an unrelated quiet-hours toggle.

### D2 — Single threshold, no hysteresis

The Alertmanager-style `triggerThreshold` + `recoveryThreshold` only matters when the signal is noisy and you want flap suppression. Our signals are mostly:

- `account_invalid` — boolean
- `user_balance_low` — already a tunable threshold over a slowly-changing value
- `quota_low` — same

`forMinutes` (sustained-for) already gives us flap suppression for noisy signals. We keep `forMinutes` and drop `recoveryThreshold` and `warningThreshold`. Recovery uses the inverse of the trigger condition.

`aggregation` collapses to `latest`. Removing it removes a hidden footgun: most users left it as the default anyway, and a "max over evaluation window" semantic was never observable in the UI (no chart).

### D3 — Merge read interval and evaluation window

Operators care about "how often does this fire" — a single "every N minutes" answer. The evaluator runs on each tick over the most recent sample; there is no historical buffer to slide a separate evaluation window over. Setting evaluation window != read interval has no observable effect.

`readIntervalMinutes` stays; `evaluationWindowMinutes` is removed.

### D4 — Zero default rules

The 8 default rules are a tutorial dressed up as configuration. They look like real production rules but they target signals the operator may or may not care about. Default behaviour should be "no alerts" with explicit add. The empty-state UI carries the tutorial role.

A `webhook_default` placeholder webhook is still kept so the rule editor has a deliverable target the moment the operator clicks "新增规则".

### D5 — Drop mentionOnFailure

There is no on-call/owner model anywhere in the codebase. The field is set in UI, persisted, and ignored by delivery. Remove from model + UI to stop the false-promise UI.

### D6 — Checkbox list for target webhooks

`<select multiple>` requires Cmd-click on macOS; nobody discovers this. A vertical checkbox list of webhooks is the obvious replacement and trivially supports keyboard.

### D7 — Frontend module split

`NotificationSettingsPanel` moves out of `App.tsx` into `frontend/src/notifications/` with one file per piece:

- `types.ts` — Webhook / Rule / Settings + signal catalog
- `defaults.ts` — empty-state factories
- `storage.ts` — localStorage hydrate/persist
- `Panel.tsx` — top-level layout + state
- `WebhookEditor.tsx` — receiver list + form
- `RuleEditor.tsx` — rule list + form
- `Summary.tsx` — runtime status panel

`App.tsx` imports `Panel` and renders it.

### D8 — Summary panel becomes runtime, not echo

Re-rendering the same form data on the right side is dead weight. Replace with:

- counts: `N 个 webhook · M 条启用规则 · K 条正在告警`
- last delivery line per webhook (status, time)

When the live data isn't available we render a placeholder. The form already shows configuration; the summary's job is "what is happening right now".

### D9 — Backward compat: tolerate on read, narrow on write

We **tolerate** unknown legacy fields in `GET /notifications/config` hydrate (drop them silently) so users on stale clients see their existing config minus the removed fields, not a 500. We **reject** unknown fields on `PUT` so a stale client posting a `policy` block gets a clear 422.

The SQLite migration is implicit: the next save writes a smaller document; older rows have extra keys we ignore. No schema migration.

## Risks / Trade-offs

- **Users who relied on quiet hours**: lose the feature. Mitigation: rule-level `cooldownMinutes` remains; future change can reintroduce quiet hours per-rule. We accept this regression because the policy block didn't actually suppress delivery in a way the operator could verify.
- **Users with recoveryThreshold set**: lose double-threshold flap suppression. Mitigation: `forMinutes` covers the common case; truly hysteresis-needy signals don't exist in this product yet.
- **Stale clients post old `policy` payload**: get 422. Mitigation: clear error message says which fields are no longer accepted; we ship UI and backend together.
- **Module split touches many imports**: contained to `App.tsx`; the move is mechanical and covered by the existing TypeScript build.

## Migration Plan

1. Land backend model changes first (`NotificationSettings` shrinks, validators reject removed fields).
2. Ship the new frontend module in the same commit so the operator doesn't end up posting now-rejected payloads.
3. On load, old localStorage payloads are passed through a `hydrate()` that picks only the surviving keys. No prompt; silent shrink.
4. Archive this change after spec validates and the new UI is verified in agent-browser.
