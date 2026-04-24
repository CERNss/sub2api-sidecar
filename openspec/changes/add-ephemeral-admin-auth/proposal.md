## Why

The local operator page and provisioning APIs are currently open to any browser or HTTP client that can reach the service. Because this sidecar can create groups, users, and OAuth-linked accounts through Sub2API admin APIs, we need lightweight access control now without introducing a heavier identity system.

## What Changes

- Add an ephemeral admin login with a fixed username and a password generated at service startup.
- Log the startup password clearly so the operator can copy it from the service logs.
- Protect the provisioning UI and the `POST /provision/start` and `POST /provision/oauth/complete` APIs behind authenticated access.
- Issue an access key on successful login, set an `HttpOnly` cookie for browser use, and also allow API callers to authenticate with `X-Access-Key` or `Authorization: Bearer`.
- Add a dedicated login page that explains the fixed username, the startup-generated password, and the existing localhost paste-back OAuth completion flow.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `openai-oauth-provisioning`: Require ephemeral admin authentication for the operator UI and provisioning APIs while preserving the current manual OAuth paste-back design.

## Impact

- Affected code: `app/main.py`, `app/config.py`, new auth module(s), HTML templates, tests, `README.md`, `.env.example`
- APIs: add `GET /login`, `POST /auth/login`, and `POST /auth/logout`; require auth for `POST /provision/start` and `POST /provision/oauth/complete`
- Systems: browser page access, local API caller workflow, startup logging
