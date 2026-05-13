## Context

`余额管理` needs to sit next to the existing operator workbench, but its domain is distinct from group orchestration. Upstream Sub2API remains the source of truth for user balance and consumption. The sidecar should own operator UX, local policy configuration, scheduling, deduplication, and audit records.

The most important implementation risk is the balance mutation API shape. The existing code confirms user listing and usage stats surfaces, and user listing is already used by alert collectors to read `balance` when present. The exact upstream endpoint for increasing/decreasing balance is not confirmed in this repository, so implementation should isolate that uncertainty inside `Sub2APIClient` and tests should pin the confirmed contract once known.

## Approach

Add a credit-control backend slice:

- `Sub2APIClient` methods for listing users with balance fields, fetching usage/consumption for selected windows, and mutating user balance through confirmed candidate endpoints.
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
- `target_scope` as a typed JSON document (`all_users`, `explicit_user_ids`, `balance_threshold`)
- `schedule` as a typed JSON document (`once`, `daily`, `weekly`, `monthly`)
- `timezone`, `next_run_at`, `last_run_at`
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

- Manual and automatic recharges use additive deltas. Absolute balance replacement is out of scope unless the upstream API only supports replacement and the UX explicitly confirms that behavior.
- Automatic recharge only increases balances. Manual adjustment supports increase and decrease with validation.
- Preview is mandatory for automatic policy target resolution and available for manual cohort adjustments.
- A due policy occurrence must be deduplicated so a restart or double trigger cannot apply the same scheduled recharge twice.
- Balance and consumption reads are best-effort per user; missing fields are surfaced as null/unknown rather than hiding users.

## Open Questions

- Confirm the upstream balance mutation endpoint and payload semantics.
- Confirm whether consumption should default to `1d`, current calendar month, or a billing-cycle range.
- Confirm the first version target scopes beyond explicit user ids and all users.
- Confirm whether negative manual adjustments may create negative balances or must stop at zero.
