## Why

Operators can currently start and complete OAuth provisioning from the UI, but the practical orchestration need is to move existing Sub2API users and existing API keys between existing groups. Changing only a user's `allowed_groups` is not sufficient because active key routing follows the API key's group assignment. Operators need a UI that uses the Sub2API admin APIs that actually move traffic: user `replace-group` for bulk key migration, or direct API key group updates for single-key moves.

## What Changes

- Add an authenticated existing user/group orchestration workspace to the React UI.
- Add authenticated APIs for listing existing Sub2API users, groups, and user API keys.
- Add execution APIs that use `POST /api/v1/admin/users/{user_id}/replace-group` or `PUT /api/v1/admin/api-keys/{key_id}`.
- Preserve the provisioning flow dashboard for historical OAuth provisioning inspection.
- Add authenticated read-only APIs for listing provisioning flows and retrieving flow details.
- Add persisted provisioning step events so the dashboard can show a timeline for each flow.
- Redact sensitive OAuth/token data from dashboard-facing responses.
- Keep the existing start and paste-back completion workflow available from the same UI.

## Capabilities

### New Capabilities
- `existing-user-group-orchestration`: Operator-facing APIs and UI for moving existing users or individual API keys between existing groups using effective Sub2API admin migration endpoints.
- `orchestration-dashboard`: Operator-facing APIs and UI for inspecting provisioning flow status, details, timeline events, and failure context.

### Modified Capabilities

## Impact

- Affected backend code: `app/main.py`, `app/clients/sub2api.py`, `app/models/schemas.py`, `app/models/flow.py`, `app/services/provisioning.py`, `app/services/rotation.py`, `app/stores/base.py`, `app/stores/sqlite.py`.
- Affected frontend code: `frontend/src/App.tsx`, `frontend/src/styles.css`.
- Affected tests: API tests for authenticated existing user/group orchestration, flow listing/detail responses, event persistence, and UI config/dashboard behavior.
- Data impact: SQLite schema gains a provisioning event table and list-query support for existing flow records.
