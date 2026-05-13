## 1. Backend Authentication

- [x] 1.1 Add a Sub2API admin JWT validation path that calls `/api/v1/auth/me` with browser bearer auth only.
- [x] 1.2 Reject missing, invalid, or non-admin Sub2API tokens without issuing a sidecar session.
- [x] 1.3 Add external sidecar session creation that reuses the existing `AuthSession` model and cookie.
- [x] 1.4 Expose `POST /auth/sub2api-login` and `GET /auth/status`.

## 2. Embedded Routing

- [x] 2.1 Serve the sidecar under `/admin/sidecar` by rewriting the request path before FastAPI route dispatch.
- [x] 2.2 Rewrite local redirects back under `/admin/sidecar`.
- [x] 2.3 Serve React static asset URLs correctly when the app is loaded from the prefix.

## 3. Frontend Integration

- [x] 3.1 Add runtime helpers for logical routes, prefixed frontend routes, and prefixed API paths.
- [x] 3.2 Exchange `token` query parameters for sidecar sessions on non-login pages.
- [x] 3.3 Remove the Sub2API token from the URL after exchange succeeds or fails.
- [x] 3.4 Update notification API calls to use the shared runtime API path helper.

## 4. Verification

- [x] 4.1 Add backend tests for JWT validation without admin API key leakage.
- [x] 4.2 Add backend tests for Sub2API login success and non-admin rejection.
- [x] 4.3 Add backend tests for prefixed `/admin/sidecar` entry, redirects, cookie auth, and auth status.
- [x] 4.4 Run backend tests.
- [x] 4.5 Run frontend production build.
