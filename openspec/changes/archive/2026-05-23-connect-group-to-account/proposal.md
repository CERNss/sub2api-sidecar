## Why

The orchestration workbench shows API keys, users, and groups, but it does not show which upstream OpenAI accounts are bound to each group. Operators cannot safely reason about a group move if the canvas hides the account capacity and health context that sits behind the group.

## What Changes

- Add an authenticated orchestration accounts discovery API backed by Sub2API admin account listing.
- Parse upstream account group bindings from common `group_id`, `group_ids`, and `groups` response shapes.
- Render upstream account nodes on the right side of the React Flow canvas and connect groups to their bound accounts.
- Keep the existing user/group/key orchestration actions unchanged.

## Impact

- Affected code: `app/clients/sub2api.py`, `app/main.py`, `app/models/schemas.py`, `frontend/src/App.tsx`, tests.
- Affected API: new `GET /orchestration/accounts`.
