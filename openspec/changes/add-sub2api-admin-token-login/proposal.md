## Why

Operators want to open the sidecar from the Sub2API admin sidebar without typing a second sidecar password. The sidecar should not become an iframe-mounted app or reuse the browser JWT for its own protected APIs; it only needs a one-time proof that the opener is a Sub2API admin.

## What Changes

- Accept a Sub2API browser JWT at `POST /auth/sub2api-login`.
- Validate the JWT by calling Sub2API `/api/v1/auth/me` with `Authorization: Bearer <token>` and without the admin API key.
- Issue the normal sidecar access-key cookie only when the Sub2API profile has `role=admin`.
- Let the React app exchange an incoming `?token=` value, remove it from the URL, and proceed with the normal sidecar session.

## Capabilities

### New Capabilities
- Sub2API admin token login for the sidecar operator UI.

### Modified Capabilities
- `openai-oauth-provisioning`: Extends existing ephemeral admin auth with an external Sub2API-admin bootstrap path.

## Impact

- Sidecar auth manager and login API
- Sub2API client token validation helper
- React app startup flow for `?token=...`
- Tests around auth exchange and rejected non-admin tokens
