## MODIFIED Requirements

### Requirement: Complete OAuth using stored flow context
The system SHALL complete OpenAI OAuth from the stored flow context by accepting a pasted localhost callback URL, and MUST use the original external email as the OpenAI OAuth account name instead of any email returned by the OAuth provider.

#### Scenario: Pasted callback URL completes a previously started flow
- **GIVEN** a provisioning flow exists in `pending_oauth` status for a generated `state` value
- **WHEN** `POST /provision/oauth/complete` is called with a pasted localhost callback URL containing a valid `code` and `state`
- **THEN** the system parses `code` and `state` from the pasted callback URL
- **THEN** the system loads the matching flow by `state`
- **THEN** the system exchanges the OAuth code through the Sub2API admin API without sending a redirect URI because the upstream callback is fixed
- **THEN** the system creates an OpenAI OAuth account through the Sub2API admin API
- **THEN** the account creation request uses `flow.email` as the account `name`
- **THEN** the account creation request uses the configured OpenAI account provider, platform, and `oauth` type
- **THEN** the account creation request enables the configured temporary-unschedulable switch
- **THEN** the account creation request includes the configured temporary-unschedulable rules
- **THEN** the account creation request sets the configured workspace mode for context-pool scheduling
- **THEN** the account creation request targets the dedicated group created for the flow
- **THEN** the system binds the created OAuth account to `flow.group_id`
- **THEN** the system does not create, look up, bind, or mutate a Sub2API user during OAuth completion
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
- **THEN** the system loads the persisted flow from PostgreSQL
- **THEN** the system completes the OAuth binding workflow without requiring the flow to remain in memory

### Requirement: Use centralized admin API integration and PostgreSQL flow storage
The system SHALL centralize Sub2API admin API calls behind a client abstraction, SHALL authenticate those requests with `x-api-key`, and SHALL persist flow context in PostgreSQL.

#### Scenario: File config and environment secrets drive admin API requests and OAuth redirect selection
- **GIVEN** the service starts with configured `config.yaml` settings and environment secrets
- **WHEN** it builds the Sub2API admin client and flow store
- **THEN** the client reads the Sub2API base URL from config-backed settings and the admin API key from environment-backed settings
- **THEN** the client reads the configured OpenAI group/account defaults and temporary-unschedulable rules from config-backed settings
- **THEN** the client sends admin requests with `x-api-key`
- **THEN** the OAuth login URL generation and OAuth code exchange do not send a redirect URI to Sub2API
- **THEN** the flow store receives an internally assembled PostgreSQL connection string from structured `database` config and `POSTGRES_PASSWORD`

#### Scenario: PostgreSQL persistence survives new store instances
- **GIVEN** the provisioning service stores a flow record in PostgreSQL
- **WHEN** a new store instance connects to the same PostgreSQL database
- **THEN** the new store instance can load the saved flow by `flow_id`
- **THEN** the new store instance can load the saved flow by `state`
- **THEN** the stored status and orchestration context remain intact

#### Scenario: Database schema initializes automatically
- **GIVEN** the configured PostgreSQL database does not yet contain the flow table
- **WHEN** the application initializes the PostgreSQL flow store
- **THEN** the required schema is created automatically before the store is used

#### Scenario: Removed database configuration is not accepted
- **GIVEN** a deployment still provides `storage.sqlite_db_path`, `SQLITE_DB_PATH`, `DATABASE_URL`, `POSTGRES_DB`, or `POSTGRES_USER`
- **WHEN** the application loads settings
- **THEN** startup fails with a configuration error
- **THEN** the system does not fall back to SQLite

### Requirement: Automated tests cover provisioning workflows
The system SHALL include automated tests that verify PostgreSQL flow persistence, ephemeral admin authentication, and the primary provisioning HTTP workflows with Sub2API admin interactions mocked.

#### Scenario: Automated tests verify login, protected APIs, and paste-back completion behavior
- **GIVEN** the test suite runs against the application with mocked Sub2API admin responses
- **WHEN** the suite exercises login, protected API access, `POST /provision/start`, and `POST /provision/oauth/complete`
- **THEN** the tests verify successful login, unauthenticated failure handling, provisioning responses, paste-back OAuth completion behavior, and failure handling

#### Scenario: Automated tests verify PostgreSQL persistence behavior
- **GIVEN** the test suite runs against the PostgreSQL flow store
- **WHEN** the suite saves and reloads flow records through separate store instances
- **THEN** the tests verify flow state remains available across instances and updates persist correctly
