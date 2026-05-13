## Why

Operators want to open the sidecar from the Sub2API admin dashboard as an embedded admin tool. The sidecar already has its own local session model, while Sub2API already owns the real admin identity. Requiring a second sidecar password prompt inside the admin iframe makes the workflow clumsy, but using the Sub2API browser JWT as the long-lived sidecar credential would expand the token exposure surface.

The sidecar should accept the current Sub2API admin login only as an initial proof, exchange it for a sidecar-owned session cookie, and then continue using its existing access-key based authorization for every protected API.

## What Changes

- Add a Sub2API admin login exchange endpoint that validates a browser JWT through Sub2API `/api/v1/auth/me`.
- Create a sidecar `AuthSession` after the Sub2API profile proves `role=admin`.
- Keep sidecar-to-Sub2API admin API operations on the configured `SUB2API_ADMIN_API_KEY`; the browser JWT is not reused for admin mutations.
- Allow the sidecar React app and APIs to be served under `/admin/sidecar` for same-site iframe mounting.
- Teach the frontend runtime to build prefixed routes and API URLs when loaded from `/admin/sidecar`.
- Remove the Sub2API token from the browser URL after the exchange succeeds or fails.

## Capabilities

### Modified Capabilities
- `openai-oauth-provisioning`: Adds Sub2API admin SSO exchange as an alternate way to create the existing ephemeral admin session.
- `orchestration-dashboard`: Adds prefixed embedded routing and frontend token exchange behavior for the operator dashboard.

## Impact

- Affected backend code: `app/auth.py`, `app/clients/sub2api.py`, `app/main.py`, `app/models/schemas.py`
- Affected frontend code: `frontend/src/App.tsx`, `frontend/src/runtime.ts`, `frontend/src/notifications/api.ts`
- Affected tests: `tests/test_api.py`
- Deployment impact: Same-site mounting under `/admin/sidecar` is supported directly. Cross-site iframe deployments still need cookie attributes and browser third-party-cookie behavior considered separately.

## Open Questions

- Should a future version replace query-token exchange with a one-time SSO ticket issued by Sub2API?
- Should the sidecar route prefix be configurable beyond `/admin/sidecar`?
- Should sidecar sessions created from Sub2API admin SSO have a shorter TTL than password-created sessions?
