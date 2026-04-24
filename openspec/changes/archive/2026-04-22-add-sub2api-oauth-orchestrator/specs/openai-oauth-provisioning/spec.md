## ADDED Requirements

### Requirement: Start provisioning flow from email
The system SHALL expose a `POST /provision/start` endpoint that accepts an email address, validates it, provisions the required Sub2API resources, persists flow context, and returns the pending OAuth handoff details.

#### Scenario: Valid email creates a pending OAuth flow
- **GIVEN** the client submits a valid email address to `POST /provision/start`
- **WHEN** the system starts a provisioning flow
- **THEN** the system creates a dedicated group whose name is derived from the configured group prefix and the submitted email
- **THEN** the system creates a user for the submitted email through the Sub2API admin API
- **THEN** the system binds that user to the dedicated group
- **THEN** the system generates an OpenAI OAuth login URL through the Sub2API admin API
- **THEN** the system stores a flow record containing `flow_id`, `email`, `user_id`, `group_id`, `state`, and `status=pending_oauth`
- **THEN** the response includes `success=true`, `flow_id`, `email`, `user_id`, `group_id`, `account_name`, and `oauth_url`
- **THEN** `account_name` equals the submitted email value

#### Scenario: Invalid email is rejected before provisioning
- **GIVEN** the client submits an invalid email address to `POST /provision/start`
- **WHEN** request validation runs
- **THEN** the system rejects the request with a client error response
- **THEN** the system does not create a group, user, or flow record

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

### Requirement: Provide a minimal manual OAuth handoff page
The system SHALL expose a minimal HTML operator page at `GET /` so a user can enter an email, start provisioning, review the returned payload, and manually continue the OAuth step.

#### Scenario: Operator starts the flow from the browser page
- **GIVEN** a user opens `GET /`
- **WHEN** the page loads
- **THEN** the page displays an email input field and a start button
- **THEN** submitting the email sends a request to `POST /provision/start`
- **THEN** a successful response is rendered back to the page for review
- **THEN** the page reveals a link or button that opens the returned OAuth URL

### Requirement: Use centralized admin API integration and pluggable flow storage
The system SHALL centralize Sub2API admin API calls behind a client abstraction, SHALL authenticate those requests with `x-api-key`, and SHALL persist flow context through a replaceable store interface with an in-memory implementation available by default.

#### Scenario: Environment config drives admin API requests
- **GIVEN** the service starts with configured environment variables
- **WHEN** it builds the Sub2API admin client
- **THEN** the client reads the Sub2API base URL and admin API key from environment-backed settings
- **THEN** the client sends admin requests with `x-api-key`
- **THEN** the OAuth callback URL is derived from the configured app base URL and callback path

#### Scenario: Flow persistence can be replaced without rewriting orchestration logic
- **GIVEN** the provisioning service stores and loads flow records through a store interface
- **WHEN** the project later switches from in-memory storage to Redis or a database
- **THEN** the orchestration flow can be preserved by replacing the store implementation without rewriting the controller or provisioning workflow requirements

### Requirement: Surface basic provisioning failures to the caller
The system SHALL log key orchestration steps and SHALL return basic error responses when validation, configuration, or Sub2API admin calls fail.

#### Scenario: Downstream admin failure is surfaced
- **GIVEN** a Sub2API admin API call fails during start or callback processing
- **WHEN** the failure is handled by the service
- **THEN** the system logs the failed orchestration step
- **THEN** JSON API endpoints return an error response for API callers
- **THEN** the OAuth callback route returns an error HTML page for browser callers
