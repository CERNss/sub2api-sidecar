## Why

Operators need a first-class balance workspace for all Sub2API users. Today the dashboard can move users, keys, groups, and inspect operational signals, but it does not provide a single place to answer the most important billing questions: each user's current credit/balance, recent consumption, and whether the account should be adjusted or automatically topped up.

Without this view, manual credit changes and periodic recharge policy are easy to perform inconsistently outside the sidecar. The new tab should make credit state visible before it lets an operator mutate balances, and every mutation path should be auditable.

## What Changes

- Add a top-level `余额管理` tab to the authenticated React operator UI.
- Add authenticated APIs that fetch every upstream user's current credit/balance and consumption data from Sub2API admin surfaces, preserving the units and display semantics provided by Sub2API.
- Add operator controls for one-off balance increases and decreases across one user or a selected user cohort.
- Add automatic recharge policy management with configurable schedule timing in `Asia/Shanghai`, recurrence, target cohort, amount, enablement, missed-run catch-up, and dry-run/preview behavior.
- Persist local automatic recharge policies and recharge run/audit records in PostgreSQL.
- Keep upstream balance mutation behind explicit operator confirmation and record the before/after balance, actor context, scope, reason, and per-user outcome.
- Use the confirmed Sub2API admin balance mutation surface `POST /api/v1/admin/users/{id}/balance` with `operation=add` for recharge/increase and `operation=subtract` for deduction.

## Capabilities

### New Capabilities
- `credit-control`: Operator-facing APIs, UI, persistence, and audit behavior for viewing user balance/consumption, manually adjusting balances, and configuring automatic recharge policies.

### Modified Capabilities
- `orchestration-dashboard`: Add `余额管理` as a top-level authenticated operator tab beside orchestration, OAuth provisioning, and notification settings.

## Impact

- Affected backend code: `app/clients/sub2api.py`, `app/main.py`, `app/models/schemas.py`, `app/stores/base.py`, `app/stores/postgres.py`, new credit-control service/models as needed.
- Affected frontend code: `frontend/src/App.tsx`, `frontend/src/styles.css`, possibly new feature modules if the dashboard is split further.
- Affected tests: authenticated API tests for credit summary, manual adjustment, policy CRUD, preview/run behavior, scheduler execution, audit persistence, and frontend build coverage.
- Data impact: PostgreSQL gains local tables or documents for recharge policies and recharge/audit runs; upstream Sub2API remains the source of truth for actual user balance and consumption.

## Confirmed Decisions

- Balance mutation endpoint: `POST /api/v1/admin/users/{id}/balance`; `add` is recharge/increase, `subtract` is deduction, and `set` is not part of this sidecar workflow.
- Balance/credit units and labels follow Sub2API's own user display fields instead of introducing sidecar-specific units.
- Consumption windows follow the existing rotation usage windows: `5h`, `1d`, `7d`, and `30d`.
- Automatic recharge target scopes in V1: explicit users, all users, users below a balance threshold, and users in selected groups.
- Automatic recharge schedules use `Asia/Shanghai` by default.
- Missed due runs are caught up after the sidecar restarts, with occurrence-level deduplication preventing double recharge.
- Audit records are required for manual balance changes, automatic recharge policy changes, and automatic recharge executions.
