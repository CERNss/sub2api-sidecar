## MODIFIED Requirements

### Requirement: Provide a minimal manual OAuth handoff page
The system SHALL protect the operator browser experience with ephemeral admin authentication and SHALL reveal the provisioning page at `GET /` only after successful login.

#### Scenario: Unauthenticated browser is redirected to the login page
- **GIVEN** a user opens `GET /` without a valid admin session
- **WHEN** the request is handled
- **THEN** the system redirects the browser to `GET /login`
- **THEN** the login page explains that the username is fixed
- **THEN** the login page explains that the password is generated on each startup and must be copied from the service logs

#### Scenario: Authenticated browser can use the provisioning page
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator opens `GET /`
- **THEN** the page displays the provisioning controls for email entry, OAuth handoff, and localhost callback paste-back
- **THEN** the page allows the operator to log out without restarting the service
- **THEN** the page still reminds the operator that the OAuth provider callback must be pasted back manually

## ADDED Requirements

### Requirement: Protect provisioning access with ephemeral admin auth
The system SHALL provide a lightweight admin authentication flow for the local service and SHALL require authenticated access for provisioning APIs.

#### Scenario: Service startup generates and logs the operator password
- **GIVEN** the service starts without an explicit auth password override
- **WHEN** the auth subsystem initializes
- **THEN** the system generates a random admin password for the configured username
- **THEN** the system logs clear startup guidance telling the operator to copy the password from the service logs
- **THEN** any password issued by a previous startup is no longer valid

#### Scenario: Successful login issues an access key for browser and API clients
- **GIVEN** an operator submits the configured username and current startup password to `POST /auth/login`
- **WHEN** the credentials are valid
- **THEN** the system returns a success response containing an access key and its expiry
- **THEN** the system sets an `HttpOnly` cookie for browser requests
- **THEN** the same access key can be used through `X-Access-Key` or `Authorization: Bearer` for API callers

#### Scenario: Invalid login is rejected
- **GIVEN** an operator submits invalid credentials to `POST /auth/login`
- **WHEN** the system validates the credentials
- **THEN** the system returns an authentication error
- **THEN** it does not issue a session or cookie

#### Scenario: Provisioning APIs reject unauthenticated access
- **GIVEN** a caller does not provide a valid admin session, access key header, or bearer token
- **WHEN** the caller invokes `POST /provision/start` or `POST /provision/oauth/complete`
- **THEN** the system returns an authentication error
- **THEN** the provisioning flow does not start or continue
