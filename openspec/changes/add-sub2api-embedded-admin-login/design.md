## Context

The sidecar already protects its operator UI and APIs with an in-memory `AuthSession` keyed by an access key. Browser sessions are stored in an `HttpOnly` cookie, while API callers can use `X-Access-Key` or `Authorization: Bearer`. The sidecar also already uses `SUB2API_ADMIN_API_KEY` for server-side Sub2API admin operations.

Sub2API custom iframe pages can pass the current browser JWT as a URL query parameter. The safest minimal integration is to treat that JWT as a short-lived bootstrap credential only:

1. Validate it against Sub2API `/api/v1/auth/me`.
2. Require the returned profile role to be `admin`.
3. Create a normal sidecar session.
4. Remove the JWT from the browser URL.
5. Continue all sidecar API calls using the sidecar session cookie.

## Approach

### Backend SSO Exchange

`Sub2APIClient.validate_admin_jwt(token)` performs a non-admin-session request to `/api/v1/auth/me` with `Authorization: Bearer <token>`.

It must not include the configured `x-api-key` header on this validation request, because this request is proving the browser user identity rather than exercising service-to-service admin power. The method unwraps the standard Sub2API response, requires `role=admin`, and returns a display username derived from the profile.

`POST /auth/sub2api-login` accepts `{ token }`, calls `validate_admin_jwt`, and creates a sidecar session through `EphemeralAdminAuthManager.create_external_session`. The response reuses the existing login response shape and sets the existing `sub2api_access_key` cookie.

### Embedded Route Prefix

The FastAPI app recognizes `/admin/sidecar` and `/admin/sidecar/*` as a mount prefix by rewriting the request path before route dispatch. Redirects with local absolute paths are rewritten back to the same prefix.

When the React index file is served from the prefix, static asset references are rewritten from `/ui-static/...` to `/admin/sidecar/ui-static/...`. This keeps the built Vite app working without requiring a separate build base for embedded deployment.

### Frontend Runtime

The frontend runtime detects whether the page was loaded from `/admin/sidecar`. If so:

- client-side route paths are prefixed with `/admin/sidecar`
- API fetches are sent to `/admin/sidecar/<api-path>`
- the logical route path used by the app remains unchanged

On non-login pages, the app reads `token` from `window.location.search`. If present, it calls `/auth/sub2api-login`, removes the token from the URL with `history.replaceState`, and proceeds with the normal operator workspace. Failed exchange removes the token and falls back to the sidecar login page.

## Decisions

- Keep `SUB2API_ADMIN_API_KEY` as the only credential used for sidecar server-to-server admin mutations.
- Do not store the Sub2API browser JWT in the sidecar session.
- Reuse the existing sidecar session TTL for externally created sessions in this change.
- Support `/admin/sidecar` as a concrete prefix instead of introducing a configurable router base immediately.
- Preserve the existing password login as a fallback and for standalone sidecar deployments.

## Risks

- Query-string JWTs can appear in browser history, access logs, or referrers before the frontend removes them. This is acceptable only as a minimal bridge and should be replaced by a one-time ticket flow when Sub2API can issue tickets.
- Cross-site iframe deployments may not accept the current `SameSite=Lax` cookie. Same-site mounting under `/admin/sidecar` is the intended path for this change.
- The prefix rewrite is intentionally narrow. If future mounted paths are needed, routing should move to a configurable prefix rather than adding more special cases.

## Rollback

- Remove the custom menu iframe URL that points to `/admin/sidecar`.
- Use the standalone sidecar login page and password flow.
- Disable or remove `POST /auth/sub2api-login` without affecting service-to-service Sub2API admin API calls.
