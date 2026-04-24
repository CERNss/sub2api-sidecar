## MODIFIED Requirements

### Requirement: Complete OAuth using stored flow context
The system SHALL complete OpenAI OAuth from the stored flow context and MUST use the original entry email as the OpenAI OAuth account name instead of any email returned by the OAuth provider.

#### Scenario: Callback completes a previously started flow
- **GIVEN** a provisioning flow exists in `pending_oauth` status for a generated `state` value
- **WHEN** `GET /provision/oauth/callback` is called with a valid `code` and `state`
- **THEN** the system loads the matching flow by `state`
- **THEN** the system exchanges the OAuth code through the Sub2API admin API
- **THEN** the system creates an OpenAI OAuth account through the Sub2API admin API
- **THEN** the account creation request uses `flow.email` as the account `name`
- **THEN** the system binds the created OAuth account to `flow.group_id`
- **THEN** the system updates the flow status to `completed`
- **THEN** the system returns a success HTML page summarizing the completed flow

#### Scenario: Callback never trusts OAuth-returned email over flow email
- **GIVEN** the OAuth exchange payload contains provider identity data
- **WHEN** the system creates the OpenAI OAuth account for the flow
- **THEN** the system uses the original entry email stored in the flow as the account name
- **THEN** the system does not replace the account name with an email inferred from the OAuth provider response

#### Scenario: Missing or invalid callback state fails safely
- **GIVEN** `GET /provision/oauth/callback` is called without a matching stored flow
- **WHEN** the system validates the callback input
- **THEN** the system returns a failure HTML page
- **THEN** the system does not create or bind an OAuth account to any group

#### Scenario: Callback still works after application restart
- **GIVEN** a provisioning flow has been persisted before the OAuth callback is received
- **WHEN** the application process restarts and `GET /provision/oauth/callback` is later called with the original `state`
- **THEN** the system loads the persisted flow from SQLite
- **THEN** the system completes the OAuth binding workflow without requiring the flow to remain in memory

### Requirement: Use centralized admin API integration and pluggable flow storage
The system SHALL centralize Sub2API admin API calls behind a client abstraction, SHALL authenticate those requests with `x-api-key`, and SHALL persist flow context in SQLite by default while preserving a store abstraction for future backend changes.

#### Scenario: Environment config drives admin API requests and SQLite path selection
- **GIVEN** the service starts with configured environment variables
- **WHEN** it builds the Sub2API admin client and flow store
- **THEN** the client reads the Sub2API base URL and admin API key from environment-backed settings
- **THEN** the client sends admin requests with `x-api-key`
- **THEN** the OAuth callback URL is derived from the configured app base URL and callback path
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

## ADDED Requirements

### Requirement: Automated tests cover provisioning workflows
The system SHALL include automated tests that verify SQLite flow persistence and the primary provisioning HTTP workflows with Sub2API admin interactions mocked.

#### Scenario: Automated tests verify start and callback behavior
- **GIVEN** the test suite runs against the application with mocked Sub2API admin responses
- **WHEN** the suite exercises `POST /provision/start` and `GET /provision/oauth/callback`
- **THEN** the tests verify successful provisioning responses, OAuth completion behavior, and failure handling

#### Scenario: Automated tests verify SQLite persistence behavior
- **GIVEN** the test suite runs against the SQLite flow store
- **WHEN** the suite saves and reloads flow records through separate store instances
- **THEN** the tests verify flow state remains available across instances and updates persist correctly
