## Context

The sidecar already has its own ephemeral `AuthSession` model and stores browser sessions in the existing `sub2api_access_key` HttpOnly cookie. Sub2API already owns the real admin identity through its browser JWT.

The integration uses the Sub2API JWT only as a bootstrap credential:

1. The operator clicks an external custom menu item in Sub2API.
2. Sub2API opens the sidecar URL with `token=<jwt>`.
3. The sidecar validates the JWT against Sub2API `/api/v1/auth/me`.
4. If the profile role is `admin`, the sidecar creates its own normal session.
5. The frontend removes `token` from the URL and continues with the sidecar cookie.

## Decisions

### 1. Validate with the browser auth endpoint, not admin API key

`Sub2APIClient.validate_admin_jwt(token)` calls the current-user endpoint with only `Authorization: Bearer <token>`. This proves the browser JWT without leaking or requiring `SUB2API_ADMIN_API_KEY` for validation.

### 2. Keep sidecar sessions local

The sidecar creates an `AuthSession` and sets the existing access-key cookie. Protected sidecar APIs continue to require that local session, `X-Access-Key`, or sidecar `Authorization: Bearer` access key.

### 3. Reject non-admin users

The sidecar only creates sessions for profiles whose `role` is `admin`; normal Sub2API users and invalid tokens receive auth errors and no cookie.

### 4. No iframe route support

This change does not add `/admin/sidecar` route prefixes, iframe mounting, static asset rewriting, or alternate router bases. The sidecar remains a standalone app opened externally.

## Risks / Trade-offs

- The JWT appears in the initial URL. The frontend removes it after success or failure, and the sidecar does not store it in the local session.
- This depends on Sub2API `/api/v1/auth/me` continuing to return a role field in its data envelope.
- If a deployment hosts sidecar cross-site, the sidecar cookie still belongs to the sidecar origin, which is acceptable for a standalone tab.

## Migration Plan

Existing password login remains available. Operators can add an external Sub2API custom menu item pointing to the sidecar URL; the sidecar will exchange the incoming token when present.

Rollback is to remove the external menu item or ignore `?token=` and use the existing password login flow.
