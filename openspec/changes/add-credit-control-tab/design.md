## Context

`余额管理` needs to sit next to the existing operator workbench, but its domain is distinct from group orchestration. Upstream Sub2API remains the source of truth for user balance and consumption. The sidecar should own operator UX, local policy configuration, scheduling, deduplication, and audit records.

The existing code confirms user listing and usage stats surfaces, and user listing is already used by alert collectors to read `balance` when present. The Sub2API balance mutation surface is `POST /api/v1/admin/users/{id}/balance` with an `operation` field. The sidecar uses `add` for recharge/increase and `subtract` for deduction. `set` exists upstream but is intentionally outside this workflow so operators do not accidentally replace a balance while trying to add credit.

## Approach

Add a credit-control backend slice:

- `Sub2APIClient` methods for listing users with balance fields, fetching usage/consumption for the existing `5h`, `1d`, `7d`, and `30d` windows, and mutating user balance through `POST /api/v1/admin/users/{id}/balance`.
- Pydantic schemas for credit summary rows, manual adjustment requests/responses, policy CRUD, preview, run records, and audit entries.
- A service that resolves target cohorts, validates adjustment bounds, invokes upstream mutations, redacts/sanitizes payloads, and records audit entries.
- SQLite persistence for automatic recharge policies and recharge run/audit records.
- Scheduler integration that evaluates due policies and reuses the same service path as manual adjustments.

Add a React `余额管理` workspace:

- Top-level navigation entry and route.
- Dense all-user table first, with filters/search/window selection and aggregate totals.
- Detail drawer for one user.
- Manual adjustment workflow with explicit amount, direction, reason, target preview, and per-user result display.
- Automatic recharge policy list/editor with one-time or recurring schedule, target scope, amount, enablement, preview, and run history.

## Data Model Sketch

Local policy fields:

- `policy_id`, `name`, `enabled`
- `amount`
- `target_scope` as a typed JSON document (`all_users`, `explicit_user_ids`, `balance_threshold`, `group_ids`)
- `schedule` as a typed JSON document (`once`, `daily`, `weekly`, `monthly`)
- `timezone` defaulting to `Asia/Shanghai`, `next_run_at`, `last_run_at`
- `reason_template`
- `created_at`, `updated_at`

Local run/audit fields:

- `run_id`, `policy_id` when automatic, operation type, status
- actor/session metadata when manual
- target scope snapshot and resolved target ids
- per-user outcomes with previous/new balance when known
- sanitized upstream error details
- `scheduled_for`, `started_at`, `finished_at`, `created_at`

## Decisions

- Manual and automatic recharges use additive deltas via `operation=add` and deductions use `operation=subtract`. Absolute balance replacement is out of scope.
- Balance/credit labels, units, and precision follow Sub2API response fields; the sidecar does not invent a separate currency or credit unit.
- Consumption windows follow the existing rotation windows: `5h`, `1d`, `7d`, and `30d`.
- Automatic recharge only increases balances. Manual adjustment supports increase and decrease with validation.
- Preview is mandatory for automatic policy target resolution and available for manual cohort adjustments.
- A due policy occurrence must be deduplicated so a restart or double trigger cannot apply the same scheduled recharge twice.
- Missed due occurrences are caught up after restart according to `Asia/Shanghai` scheduling, then marked completed/failed to prevent duplicate application.
- Balance and consumption reads are best-effort per user; missing fields are surfaced as null/unknown rather than hiding users.
