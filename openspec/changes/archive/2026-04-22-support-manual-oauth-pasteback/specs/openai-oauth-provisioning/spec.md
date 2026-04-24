## MODIFIED Requirements

### Requirement: Start provisioning flow from email
The system SHALL expose a `POST /provision/start` endpoint that accepts an email address, validates it, provisions the required Sub2API resources, persists flow context, and returns the pending OAuth handoff details.

#### Scenario: Valid email creates a pending OAuth flow
- **GIVEN** the client submits a valid email address to `POST /provision/start`
- **WHEN** the system starts a provisioning flow
- **THEN** the system creates a dedicated group whose name is derived from the configured group prefix and the submitted email
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
The system SHALL expose a minimal HTML operator page at `GET /` so a user can enter an email, start provisioning, open the OAuth link, and paste the resulting localhost callback URL back into the service to finish the flow.

#### Scenario: Operator completes the flow from the browser page
- **GIVEN** a user opens `GET /`
- **WHEN** the page loads
- **THEN** the page displays an email input field and a start button
- **THEN** submitting the email sends a request to `POST /provision/start`
- **THEN** a successful response is rendered back to the page for review
- **THEN** the page reveals a link or button that opens the returned OAuth URL
- **THEN** the page shows where the OAuth provider redirect will land
- **THEN** the page provides a textarea or input for pasting the final localhost callback URL
- **THEN** submitting the pasted callback URL sends a request to `POST /provision/oauth/complete`

### Requirement: Use centralized admin API integration and pluggable flow storage
The system SHALL centralize Sub2API admin API calls behind a client abstraction, SHALL authenticate those requests with `x-api-key`, and SHALL persist flow context in SQLite by default while preserving a store abstraction for future backend changes.

#### Scenario: Environment config drives admin API requests and OAuth redirect selection
- **GIVEN** the service starts with configured environment variables
- **WHEN** it builds the Sub2API admin client and flow store
- **THEN** the client reads the Sub2API base URL and admin API key from environment-backed settings
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

### Requirement: Automated tests cover provisioning workflows
The system SHALL include automated tests that verify SQLite flow persistence and the primary provisioning HTTP workflows with Sub2API admin interactions mocked.

#### Scenario: Automated tests verify start and paste-back completion behavior
- **GIVEN** the test suite runs against the application with mocked Sub2API admin responses
- **WHEN** the suite exercises `POST /provision/start` and `POST /provision/oauth/complete`
- **THEN** the tests verify successful provisioning responses, paste-back OAuth completion behavior, and failure handling

#### Scenario: Automated tests verify SQLite persistence behavior
- **GIVEN** the test suite runs against the SQLite flow store
- **WHEN** the suite saves and reloads flow records through separate store instances
- **THEN** the tests verify flow state remains available across instances and updates persist correctly
