## Why

Operators need a first-class balance workspace for all Sub2API users. Today the dashboard can move users, keys, groups, and inspect operational signals, but it does not provide a single place to answer the most important billing questions: each user's current credit/balance, recent consumption, and whether the account should be adjusted or automatically topped up.

Without this view, manual credit changes and periodic recharge policy are easy to perform inconsistently outside the sidecar. The new tab should make credit state visible before it lets an operator mutate balances, and every mutation path should be auditable.

## What Changes

- Add a top-level `余额管理` tab to the authenticated React operator UI.
- Add authenticated APIs that fetch every upstream user's current credit/balance and consumption data from Sub2API admin surfaces.
- Add operator controls for one-off balance increases and decreases across one user or a selected user cohort.
- Add automatic recharge policy management with configurable schedule timing, recurrence, target cohort, amount, enablement, and dry-run/preview behavior.
- Persist local automatic recharge policies and recharge run/audit records in SQLite.
- Keep upstream balance mutation behind explicit operator confirmation and record the before/after balance, actor context, scope, reason, and per-user outcome.

## Capabilities

### New Capabilities
- `credit-control`: Operator-facing APIs, UI, persistence, and audit behavior for viewing user balance/consumption, manually adjusting balances, and configuring automatic recharge policies.

### Modified Capabilities
- `orchestration-dashboard`: Add `余额管理` as a top-level authenticated operator tab beside orchestration, OAuth provisioning, and notification settings.

## Impact

- Affected backend code: `app/clients/sub2api.py`, `app/main.py`, `app/models/schemas.py`, `app/stores/base.py`, `app/stores/sqlite.py`, new credit-control service/models as needed.
- Affected frontend code: `frontend/src/App.tsx`, `frontend/src/styles.css`, possibly new feature modules if the dashboard is split further.
- Affected tests: authenticated API tests for credit summary, manual adjustment, policy CRUD, preview/run behavior, scheduler execution, audit persistence, and frontend build coverage.
- Data impact: SQLite gains local tables or documents for recharge policies and recharge/audit runs; upstream Sub2API remains the source of truth for actual user balance and consumption.

## Open Questions

- Which confirmed Sub2API admin endpoint mutates user balance, and does it accept delta adjustments, absolute balance replacement, or both?
- What is the canonical user consumption window for this tab: today/current billing cycle, custom date range, or the same `5h`/`1d`/`7d`/`30d` windows used by rotation?
- Should automatic recharge target cohorts be based only on selected user ids, or also support filters such as groups, balance threshold, status, tags, or recent consumption?
- Should automatic recharge run at the sidecar's local timezone (`Asia/Shanghai` in this workspace) unless explicitly configured otherwise?
