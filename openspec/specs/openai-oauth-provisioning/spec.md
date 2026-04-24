# openai-oauth-provisioning Specification

## Purpose
Define the required behavior for a minimal Sub2API-managed OpenAI OAuth orchestration service, including email-driven provisioning, manual OAuth handoff, callback completion, group bindings, configuration, and replaceable flow persistence.
## Requirements
### Requirement: Start provisioning flow from email
The system SHALL expose a `POST /provision/start` endpoint that accepts an email address, validates it, provisions the required Sub2API resources, persists flow context, and returns the pending OAuth handoff details.

#### Scenario: Valid email creates a pending OAuth flow
- **GIVEN** the client submits a valid email address to `POST /provision/start`
- **WHEN** the system starts a provisioning flow
- **THEN** the system creates a dedicated group whose name is derived from the configured group prefix and the submitted email
- **THEN** the dedicated group creation request includes the configured OpenAI platform value
- **THEN** the system creates a user for the submitted email through the Sub2API admin API
- **THEN** the system binds that user to the dedicated group
- **THEN** the system generates an OpenAI OAuth login URL through the Sub2API admin API using the configured OAuth provider redirect URI
- **THEN** the system stores a flow record containing `flow_id`, `email`, `user_id`, `group_id`, `state`, and `status=pending_oauth`
- **THEN** the response includes `success=true`, `flow_id`, `email`, `user_id`, `group_id`, `account_name`, `oauth_url`, and the configured OAuth provider redirect URI
- **THEN** `account_name` equals the submitted email value

#### Scenario: Invalid email is rejected before provisioning
- **GIVEN** the client submits an invalid email address to `POST /provision/start`
- **WHEN** request validation runs
- **THEN** the system rejects the request with a client error response
- **THEN** the system does not create a group, user, or flow record

### Requirement: Complete OAuth using stored flow context
The system SHALL complete OpenAI OAuth from the stored flow context by accepting a pasted localhost callback URL, and MUST use the original entry email as the OpenAI OAuth account name instead of any email returned by the OAuth provider.

#### Scenario: Pasted callback URL completes a previously started flow
- **GIVEN** a provisioning flow exists in `pending_oauth` status for a generated `state` value
- **WHEN** `POST /provision/oauth/complete` is called with a pasted localhost callback URL containing a valid `code` and `state`
- **THEN** the system parses `code` and `state` from the pasted callback URL
- **THEN** the system loads the matching flow by `state`
- **THEN** the system exchanges the OAuth code through the Sub2API admin API using the configured OAuth provider redirect URI
- **THEN** the system creates an OpenAI OAuth account through the Sub2API admin API
- **THEN** the account creation request uses `flow.email` as the account `name`
- **THEN** the account creation request uses the configured OpenAI account provider, platform, and `oauth` type
- **THEN** the account creation request enables the configured temporary-unschedulable switch
- **THEN** the account creation request includes the configured temporary-unschedulable rules
- **THEN** the account creation request sets the configured workspace mode for context-pool scheduling
- **THEN** the account creation request targets the dedicated group created for the flow
- **THEN** the system binds the created OAuth account to `flow.group_id`
- **THEN** the system updates the flow status to `completed`
- **THEN** the response returns a success payload describing the completed flow

#### Scenario: Callback never trusts OAuth-returned email over flow email
- **GIVEN** the OAuth exchange payload contains provider identity data
- **WHEN** the system creates the OpenAI OAuth account for the flow
- **THEN** the system uses the original entry email stored in the flow as the account name
- **THEN** the system does not replace the account name with an email inferred from the OAuth provider response

#### Scenario: Malformed pasted callback URL fails safely
- **GIVEN** `POST /provision/oauth/complete` is called with a pasted callback URL that does not contain both `code` and `state`
- **WHEN** the system validates and parses the pasted callback input
- **THEN** the system returns a client error response
- **THEN** the system does not create or bind an OAuth account to any group

#### Scenario: Paste-back still works after application restart
- **GIVEN** a provisioning flow has been persisted before the user finishes OAuth
- **WHEN** the application process restarts and the user later submits the pasted localhost callback URL
- **THEN** the system loads the persisted flow from SQLite
- **THEN** the system completes the OAuth binding workflow without requiring the flow to remain in memory

### Requirement: Provide a minimal manual OAuth handoff page
The system SHALL protect the operator browser experience with ephemeral admin authentication and SHALL reveal the provisioning page at `GET /` only after successful login.

#### Scenario: Unauthenticated browser is redirected to the login page
- **GIVEN** a user opens `GET /` without a valid admin session
- **WHEN** the request is handled
- **THEN** the system redirects the browser to `GET /login`
- **THEN** the login page explains that the username is fixed
- **THEN** the login page explains that the password is generated on each startup and must be copied from the service logs

#### Scenario: Authenticated browser can complete the flow from the provisioning page
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator opens `GET /`
- **THEN** the page displays an email input field and a start button
- **THEN** submitting the email sends a request to `POST /provision/start`
- **THEN** a successful response is rendered back to the page for review
- **THEN** the page reveals a link or button that opens the returned OAuth URL
- **THEN** the page shows where the OAuth provider redirect will land
- **THEN** the page provides a textarea or input for pasting the final localhost callback URL
- **THEN** submitting the pasted callback URL sends a request to `POST /provision/oauth/complete`
- **THEN** the page provides a way to log out without restarting the service

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

### Requirement: Use centralized admin API integration and pluggable flow storage
The system SHALL centralize Sub2API admin API calls behind a client abstraction, SHALL authenticate those requests with `x-api-key`, and SHALL persist flow context in SQLite by default while preserving a store abstraction for future backend changes.

#### Scenario: Environment config drives admin API requests and OAuth redirect selection
- **GIVEN** the service starts with configured environment variables
- **WHEN** it builds the Sub2API admin client and flow store
- **THEN** the client reads the Sub2API base URL and admin API key from environment-backed settings
- **THEN** the client reads the configured OpenAI group/account defaults and temporary-unschedulable rules from environment-backed settings
- **THEN** the client sends admin requests with `x-api-key`
- **THEN** the OAuth login URL generation and OAuth code exchange both use the configured OAuth provider redirect URI
- **THEN** the flow store reads the SQLite database path from configuration

#### Scenario: SQLite persistence survives new store instances
- **GIVEN** the provisioning service stores a flow record in SQLite
- **WHEN** a new store instance opens the same SQLite database file
- **THEN** the new store instance can load the saved flow by `flow_id`
- **THEN** the new store instance can load the saved flow by `state`
- **THEN** the stored status and orchestration context remain intact

#### Scenario: Database schema initializes automatically
- **GIVEN** the configured SQLite database file does not yet contain the flow table
- **WHEN** the application initializes the SQLite flow store
- **THEN** the required schema is created automatically before the store is used

#### Scenario: Store abstraction remains replaceable
- **GIVEN** the provisioning service stores and loads flow records through a store interface
- **WHEN** the project later switches from SQLite to Redis or another database backend
- **THEN** the orchestration flow can be preserved by replacing the store implementation without rewriting the controller or provisioning workflow requirements

### Requirement: Surface basic provisioning failures to the caller
The system SHALL log key orchestration steps and SHALL return basic error responses when validation, configuration, or Sub2API admin calls fail.

#### Scenario: Downstream admin failure is surfaced
- **GIVEN** a Sub2API admin API call fails during start or callback processing
- **WHEN** the failure is handled by the service
- **THEN** the system logs the failed orchestration step
- **THEN** JSON API endpoints return an error response for API callers
- **THEN** the manual handoff page can surface completion errors returned by the JSON APIs for browser users

### Requirement: Automated tests cover provisioning workflows
The system SHALL include automated tests that verify SQLite flow persistence, ephemeral admin authentication, and the primary provisioning HTTP workflows with Sub2API admin interactions mocked.

#### Scenario: Automated tests verify login, protected APIs, and paste-back completion behavior
- **GIVEN** the test suite runs against the application with mocked Sub2API admin responses
- **WHEN** the suite exercises login, protected API access, `POST /provision/start`, and `POST /provision/oauth/complete`
- **THEN** the tests verify successful login, unauthenticated failure handling, provisioning responses, paste-back OAuth completion behavior, and failure handling

#### Scenario: Automated tests verify SQLite persistence behavior
- **GIVEN** the test suite runs against the SQLite flow store
- **WHEN** the suite saves and reloads flow records through separate store instances
- **THEN** the tests verify flow state remains available across instances and updates persist correctly
