# openai-oauth-provisioning Specification

## MODIFIED Requirements

### Requirement: Protect provisioning access with ephemeral admin auth
The system SHALL provide a lightweight admin authentication flow for the local service and SHALL require authenticated access for provisioning APIs. The system SHALL also support creating the same sidecar admin session from a verified Sub2API admin browser login when the sidecar is embedded inside Sub2API.

#### Scenario: Sub2API admin JWT creates a sidecar session
- **GIVEN** the sidecar receives `POST /auth/sub2api-login` with a non-empty Sub2API browser JWT
- **WHEN** the sidecar validates the token by calling Sub2API `/api/v1/auth/me`
- **THEN** the validation request SHALL use `Authorization: Bearer <token>`
- **AND** the validation request SHALL NOT include the configured Sub2API admin API key
- **AND** the returned profile SHALL be required to contain admin role
- **AND** the sidecar SHALL create a normal sidecar admin session
- **AND** the response SHALL set the existing sidecar access-key cookie
- **AND** subsequent protected sidecar APIs SHALL authorize through the sidecar session, not through the Sub2API JWT

#### Scenario: Non-admin Sub2API JWT is rejected
- **GIVEN** the sidecar receives `POST /auth/sub2api-login` with a Sub2API JWT for a non-admin user
- **WHEN** the sidecar validates the token through Sub2API `/api/v1/auth/me`
- **THEN** the sidecar SHALL return an authentication or authorization error
- **AND** the sidecar SHALL NOT create an access key
- **AND** the sidecar SHALL NOT set the sidecar session cookie

#### Scenario: Invalid Sub2API JWT is rejected
- **GIVEN** the sidecar receives `POST /auth/sub2api-login` with an invalid, expired, or empty token
- **WHEN** token validation fails
- **THEN** the sidecar SHALL return an authentication error
- **AND** the sidecar SHALL NOT create a sidecar session

### Requirement: Use centralized admin API integration and pluggable flow storage
The system SHALL centralize Sub2API admin API calls behind a client abstraction, SHALL authenticate service-to-service admin operations with `x-api-key`, and SHALL persist flow context in SQLite by default while preserving a store abstraction for future backend changes.

#### Scenario: Browser token validation does not change admin API credentials
- **GIVEN** a sidecar session was created from a Sub2API admin browser JWT
- **WHEN** the operator performs provisioning or orchestration actions that mutate Sub2API state
- **THEN** the sidecar SHALL continue to call Sub2API admin APIs with the configured admin API key
- **AND** the sidecar SHALL NOT reuse the browser JWT as the credential for those admin mutations
