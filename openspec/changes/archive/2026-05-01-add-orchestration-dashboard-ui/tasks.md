## 1. Backend Models And Store

- [x] 1.1 Add provisioning event model, event status/type fields, and dashboard-safe flow/event response schemas.
- [x] 1.2 Extend the flow store interface with flow listing, flow counting, event persistence, and event listing methods.
- [x] 1.3 Add SQLite schema initialization for `provision_events`, indexes for flow listing/timeline queries, and implementation of the new store methods.
- [x] 1.4 Add redaction utilities for dashboard-facing nested payloads.

## 2. Backend APIs And Instrumentation

- [x] 2.1 Add authenticated `GET /provision/flows` with pagination and `status`, `assignment_mode`, and `email` filters.
- [x] 2.2 Add authenticated `GET /provision/flows/{flow_id}` returning flow detail and timeline events.
- [x] 2.3 Instrument provisioning start and OAuth completion paths with timeline event writes.
- [x] 2.4 Ensure failure paths write failed timeline events when a flow id is available.
- [x] 2.5 Add authenticated existing user, group, and user API key discovery APIs.
- [x] 2.6 Add existing-user bulk orchestration through upstream `replace-group`, without mutating only `allowed_groups`.
- [x] 2.7 Add single-key orchestration through upstream API key group update.

## 3. React Dashboard UI

- [x] 3.1 Add API client types and helpers for flow list/detail endpoints.
- [x] 3.2 Add app-level navigation between the existing provisioning form and the orchestration dashboard.
- [x] 3.3 Implement dashboard filters, refresh behavior, loading, empty, and error states.
- [x] 3.4 Implement flow detail panel with summary fields, OAuth handoff context, error message, and chronological timeline.
- [x] 3.5 Ensure dashboard UI remains read-only and does not expose redacted secret fields.
- [x] 3.6 Add default existing user/group orchestration workspace with replace-group and single-key execution modes.
- [x] 3.7 Render the orchestration workspace with Ant Design controls and a draggable React Flow relationship graph.

## 4. Verification And Docs

- [x] 4.1 Add backend tests for auth, filtering, pagination, detail lookup, missing flow, timeline events, and redaction.
- [x] 4.2 Update React build verification to cover the new dashboard code path.
- [x] 4.3 Update README with the dashboard entry point and inspection workflow.
- [x] 4.4 Run `npm run build`, backend tests, and OpenSpec validation.
- [x] 4.5 Add backend tests proving existing-user orchestration uses `replace-group` or API key update rather than `allowed_groups`.
