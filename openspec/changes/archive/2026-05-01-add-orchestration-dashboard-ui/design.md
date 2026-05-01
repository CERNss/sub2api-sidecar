## Context

The service already persists provisioning flows in SQLite and exposes a React UI for starting OAuth provisioning and completing pasted callbacks. The first operator-facing orchestration surface must target existing Sub2API users and existing groups: choose a user, choose the source group, choose a target group, and execute an API-key-impacting migration.

Sub2API routing follows API key group assignment. Therefore the UI must not implement existing-user orchestration by only changing user `allowed_groups`. Bulk moves must call upstream `POST /api/v1/admin/users/{user_id}/replace-group`, and single-key moves must call upstream `PUT /api/v1/admin/api-keys/{key_id}`.

The current flow payload can include OAuth exchange data after completion, so any dashboard-facing API must redact token-like fields before returning details to the browser.

## Goals / Non-Goals

**Goals:**
- Provide authenticated list and detail APIs for provisioning flow inspection.
- Provide authenticated existing user, group, and API key discovery APIs.
- Provide execution APIs for bulk user group replacement and single API key group updates.
- Persist a chronological provisioning timeline for each flow.
- Render a React workspace that defaults to existing user/group orchestration and still allows flow inspection.
- Preserve the existing start and paste-back workflow.
- Redact sensitive values from dashboard responses.

**Non-Goals:**
- No multi-user authorization model beyond the existing ephemeral admin auth.
- No WebSocket or server-sent event streaming in V1.
- No retry, edit, delete, or force-complete operation for historical flows.
- No long-term analytics, charts, or external observability integration.
- No `allowed_groups`-only implementation for existing-user group changes.

## Decisions

### Use effective Sub2API admin migration endpoints for existing users

Expose:
- `GET /orchestration/users`
- `GET /orchestration/groups`
- `GET /orchestration/users/{user_id}/api-keys`
- `POST /orchestration/assignments/replace-group`
- `POST /orchestration/api-keys/update-group`

The replace-group endpoint calls upstream `POST /api/v1/admin/users/{user_id}/replace-group` with `old_group_id` and `new_group_id`. This migrates the user's API keys bound to the old group and invalidates upstream auth cache. It is restricted to exclusive non-subscription groups because upstream replace-group currently supports dedicated standard groups only.

The single-key endpoint calls upstream `PUT /api/v1/admin/api-keys/{key_id}` with `group_id`. It records a local rotation event but does not claim to move every key for the user.

Alternatives considered:
- Update `allowed_groups` on the user: rejected because it does not move API key routing and would give operators a false sense of completion.
- Force all existing users through OAuth provisioning: rejected because the user and groups already exist.

### Store flow summaries through SQLite query methods

Add `list_flows(...)` and `count_flows(...)` behavior to the SQLite store rather than reconstructing history from frontend state. The store already has indexed columns for `email`, `status`, `assignment_mode`, and timestamp fields, so list APIs can query those columns and deserialize each row's JSON payload into `ProvisionFlow`.

Alternatives considered:
- Query raw SQLite from the controller: rejected because it leaks persistence details out of the store.
- Maintain an in-memory dashboard cache: rejected because flows must survive restarts.

### Add a provisioning event model and table

Introduce a `ProvisionEvent` model with `event_id`, `flow_id`, `event_type`, `status`, `message`, `details`, and timestamps. Persist events to a new `provision_events` SQLite table with an index on `(flow_id, created_at)`.

The provisioning service records events at durable boundaries:
- flow start requested
- user created
- group assignment resolved
- user bound to group
- OAuth URL generated
- OAuth callback parsed
- OAuth code exchanged
- OAuth account created
- OAuth account bound to group
- flow completed or failed

Alternatives considered:
- Infer events from the final flow payload: rejected because failures and intermediate steps would be invisible.
- Use application logs only: rejected because the dashboard needs structured data tied to flow ids.

### Keep dashboard APIs read-only and authenticated

Expose:
- `GET /provision/flows`
- `GET /provision/flows/{flow_id}`

Both routes use the existing admin auth dependency. Query filters stay simple: `status`, `assignment_mode`, `email`, `limit`, and `offset`. Limit defaults to a conservative value and has a server-side maximum to avoid rendering or loading too much history at once.

### Redact dashboard-facing payloads centrally

Create a response serializer that turns `ProvisionFlow` and `ProvisionEvent` models into dashboard-safe schemas. The serializer redacts token-like keys in nested dictionaries, including `access_token`, `refresh_token`, `id_token`, `api_key`, `authorization`, `password`, and `secret`.

The flow list API returns summaries only. The detail API may return redacted OAuth exchange metadata but never raw credential values.

### Render the dashboard inside the React app shell

The existing React app should grow from a single form into a compact operator workspace:
- A tab or segmented control switches between "用户分组编排", "历史看板", and "OAuth 预配".
- The default view selects existing users, source groups, target groups, and execution mode.
- Ant Design components provide the operator controls, lists, drawers, and feedback states.
- A React Flow graph renders the global relationship from left to right as all API keys, all users, and all group nodes with draggable repositioning, pan, zoom, and minimap controls. Selecting a user or key node syncs the operator controls. Target group selection remains in the operator controls rather than changing the relationship layout.
- The dashboard has filters and a refresh button above a dense flow list.
- Selecting a flow opens a detail panel with fields, OAuth handoff context, error message, and timeline.

This keeps the application operational and scanning-focused instead of turning it into a landing page.

## Risks / Trade-offs

- **Historical flows created before this change have no timeline events** -> The detail API returns an empty timeline and the UI renders a clear empty state.
- **Extra event writes add small overhead to provisioning** -> Events are local SQLite inserts and only happen at coarse orchestration boundaries.
- **Redaction can miss unexpected secret key names** -> Use a denylist of common token/password/secret substrings and keep raw payloads server-side only.
- **Large databases could make list queries slow** -> Add pagination and reuse indexed columns. Additional indexes can be added later if history grows.

## Migration Plan

1. Add the new SQLite table and indexes using the existing automatic schema initialization pattern.
2. Deploy the new backend; existing flow rows remain readable because the flow table schema is unchanged.
3. Existing flows appear in the dashboard without timeline events.
4. Rollback is safe because the new table is additive and existing provisioning endpoints keep their current behavior.

## Open Questions

- Should a later change add explicit retry actions for failed flows?
- Should dashboard filters eventually include date ranges or group id?
