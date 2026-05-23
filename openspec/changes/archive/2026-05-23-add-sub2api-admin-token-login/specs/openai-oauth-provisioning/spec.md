# openai-oauth-provisioning Specification

## MODIFIED Requirements

### Requirement: Protect provisioning access with ephemeral admin auth
The system SHALL provide a lightweight admin authentication flow for the local service and SHALL require authenticated access for provisioning APIs. The system SHALL also support creating the same sidecar admin session from a verified Sub2API admin browser login when opened as a standalone external service.

#### Scenario: Sub2API admin JWT creates a sidecar session
- **GIVEN** the sidecar receives `POST /auth/sub2api-login` with a non-empty Sub2API browser JWT
- **WHEN** the sidecar validates the token by calling Sub2API `/api/v1/auth/me`
- **THEN** the validation request SHALL use `Authorization: Bearer <token>`
- **AND** the validation request SHALL NOT use the configured Sub2API admin API key
- **AND** the returned Sub2API profile SHALL have `role=admin`
- **AND** the sidecar SHALL create a normal sidecar admin session
- **AND** the response SHALL set the existing sidecar access-key cookie
- **AND** subsequent protected sidecar APIs SHALL authorize through the sidecar session, not through the Sub2API JWT

#### Scenario: Non-admin Sub2API JWT is rejected
- **GIVEN** the sidecar receives `POST /auth/sub2api-login` with a Sub2API JWT for a non-admin user
- **WHEN** the sidecar validates the token through Sub2API `/api/v1/auth/me`
- **THEN** the sidecar SHALL return an authorization error
- **AND** it SHALL NOT create a sidecar session
- **AND** it SHALL NOT set the sidecar session cookie

#### Scenario: Invalid Sub2API JWT is rejected
- **GIVEN** the sidecar receives `POST /auth/sub2api-login` with an invalid, expired, or empty token
- **WHEN** token validation fails
- **THEN** the sidecar SHALL return an authentication error
- **AND** it SHALL NOT create a sidecar session

### Requirement: React dashboard renders orchestration state
The React UI SHALL provide an authenticated orchestration workspace for moving existing users or keys between groups, browsing provisioning flows, and configuring webhook alert routing for operational signals.

#### Scenario: External Sub2API launch exchanges token before rendering
- **GIVEN** the sidecar is opened as a standalone page with a `token` query parameter
- **WHEN** the React app starts
- **THEN** it SHALL call `POST /auth/sub2api-login`
- **AND** it SHALL remove the `token` query parameter from the URL after success or failure
- **AND** on success it SHALL continue to the normal operator workspace using the sidecar session cookie
- **AND** on failure it SHALL navigate to the normal sidecar login page
