## Context

The sidecar already supports the intended provisioning flow: start from an email address, hand the user an OAuth URL, and finish the workflow only after the operator pastes the final localhost callback URL back into the service. SQLite now persists provisioning flows across restarts, but the browser page and JSON APIs are still unauthenticated even though they drive Sub2API admin operations.

This change needs a lightweight control that is easy to run locally and does not introduce a database-backed identity system or a reverse-proxy dependency. It also needs to preserve the current manual OAuth paste-back constraint: the OAuth provider will not redirect back into this sidecar automatically.

## Goals / Non-Goals

**Goals:**

- Require authentication before showing the provisioning page or allowing provisioning API calls.
- Use a fixed operator username with a password generated on each service startup and printed in the logs.
- Return an access key after login so browser users can rely on an `HttpOnly` cookie and API callers can reuse the same key through headers.
- Keep session state ephemeral and in memory so restart semantics stay simple and prior sessions become invalid automatically.
- Add clear login-page guidance about where the password comes from and that localhost callback URLs still need to be pasted back manually.

**Non-Goals:**

- Building a full user management system, password reset flow, or persistent admin identity store.
- Automatically consuming the OAuth callback from the provider or changing the manual paste-back design.
- Introducing third-party auth libraries, Redis, or a separate reverse-proxy auth layer for this first version.

## Decisions

### 1. Use an in-memory auth manager with a startup-generated password

The service will initialize a small auth manager at startup. It keeps one configured username and either:

- generates a fresh random password for the current process, or
- uses an optional environment override for tests and controlled debugging.

Why this choice:

- it matches the local-admin use case,
- it guarantees restart invalidates prior credentials,
- it avoids adding a persistence dependency just for auth.

Alternative considered:

- Persisting admin credentials in SQLite. Rejected because it weakens the "copy from logs, resets on restart" workflow the operator requested.

### 2. Issue access keys and support both cookie and header auth

`POST /auth/login` will mint a random access key with an expiry timestamp. The browser receives it via an `HttpOnly` cookie so the front-end does not need to manage secrets directly. API callers can use the same key through `X-Access-Key` or `Authorization: Bearer`.

Why this choice:

- browsers get a simple session experience,
- curl/API callers remain usable without scraping cookies,
- one session model covers both UI and API paths.

Alternative considered:

- Cookie-only auth. Rejected because the user explicitly asked for an account/password -> key flow and API testing is part of the project.

### 3. Protect only operator-facing routes and keep health open

The operator page at `GET /`, plus `POST /provision/start` and `POST /provision/oauth/complete`, become authenticated endpoints. `GET /login`, `POST /auth/login`, `POST /auth/logout`, and `GET /health` remain open.

Why this choice:

- health checks stay simple,
- operator access is protected where it matters,
- the surface area of auth logic stays small.

### 4. Keep the login UX explicit about restart semantics and paste-back OAuth

The login page will explain:

- the username is fixed,
- the password is generated on every startup,
- the operator must copy it from logs,
- old passwords die on restart,
- after login the OAuth flow still ends with pasting a localhost callback URL back into the provisioning page.

Why this choice:

- it addresses the operator confusion points directly in the UI,
- it matches the already-agreed manual flow instead of implying auto-redirect support.

## Risks / Trade-offs

- [Generated passwords are visible in logs] -> Acceptable for a local operator tool; keep the message explicit and scoped to startup so the operator can retrieve it easily.
- [In-memory sessions disappear on restart] -> Intentional; login-page copy and README will explain that restart invalidates old passwords and access keys.
- [Access key is returned in the login JSON body] -> Acceptable for the local/admin use case because it enables curl-based testing; browser use still relies on the `HttpOnly` cookie path by default.
- [Optional env password override could be left enabled accidentally] -> Keep it documented as a test/debug override rather than the default production-local path.

## Migration Plan

1. Add the auth manager, settings, login routes, and protected route checks.
2. Add the login template and update the provisioning page with logout and auth-expiry handling.
3. Extend tests to cover unauthenticated failures, login success, cookie/header auth, and the existing provisioning flow after re-login.
4. Update README and `.env.example` to document the new startup/login flow.

Rollback is straightforward: remove the auth gate and new routes, then the existing provisioning flow resumes unchanged.

## Open Questions

- None for this iteration. The remaining flexibility point is whether future deployments should replace the in-memory auth manager with a more persistent or externally managed auth layer.
