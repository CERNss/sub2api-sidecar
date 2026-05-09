## Why

The current provisioning contract incorrectly treats the submitted email as a Sub2API user-system email and starts the OAuth flow by creating and binding a Sub2API user. The desired flow uses an external OAuth account email only: create a dedicated group, complete OpenAI OAuth, and bind the OAuth account to that group without creating or mutating a Sub2API user.

## What Changes

- **BREAKING**: `POST /provision/start` no longer creates a Sub2API user for the submitted email.
- **BREAKING**: provisioning flow records and responses no longer require a `user_id` for OAuth pre-provisioning.
- Treat the submitted `email` as an external OAuth account identifier used for group naming, flow lookup, and OAuth account naming.
- Keep dedicated group creation and OAuth handoff behavior, but remove user-group binding from the provisioning start path.
- Keep existing user/key/group orchestration separate from OAuth provisioning; orchestration continues to operate only on existing upstream users, API keys, and groups.
- Update dashboard flow inspection and timeline language so provisioning flows are account/group scoped, not user scoped.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `openai-oauth-provisioning`: Decouple OAuth pre-provisioning from Sub2API user creation and user group binding.
- `orchestration-dashboard`: Display provisioning flow data without assuming every OAuth flow has a Sub2API user id, while preserving existing user/key/group orchestration.

## Impact

- Affected code: `app/services/provisioning.py`, `app/models/flow.py`, `app/models/schemas.py`, `app/stores/sqlite.py`, `app/clients/sub2api.py`, `app/main.py`, frontend dashboard components, tests, and mock Sub2API fixtures.
- Affected APIs: `POST /provision/start`, `POST /provision/oauth/complete`, `GET /provision/flows`, `GET /provision/flows/{flow_id}`.
- Operational impact: operators must understand the provisioning email as an external OAuth account email, not as a Sub2API user account. Existing user/key/group orchestration remains available through the separate orchestration APIs.
