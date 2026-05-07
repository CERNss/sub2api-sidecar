## MODIFIED Requirements

### Requirement: Start provisioning flow from email
The system SHALL expose a `POST /provision/start` endpoint that accepts an external OAuth account email, validates it, provisions the required Sub2API group resource, persists flow context, and returns the pending OAuth handoff details. The submitted email MUST NOT be treated as a Sub2API user-system email.

#### Scenario: Valid external email creates a pending OAuth flow
- **GIVEN** the client submits a valid external OAuth account email address to `POST /provision/start`
- **WHEN** the system starts a provisioning flow
- **THEN** the system creates a dedicated group whose name is derived from the configured group prefix and the submitted email
- **THEN** the dedicated group creation request includes the configured OpenAI platform value
- **THEN** the system does not create a Sub2API user for the submitted email
- **THEN** the system does not bind a Sub2API user to the dedicated group
- **THEN** the system generates an OpenAI OAuth login URL through the Sub2API admin API using the configured OAuth provider redirect URI
- **THEN** the system stores a flow record containing `flow_id`, `email`, `group_id`, `state`, and `status=pending_oauth`
- **THEN** the flow record does not require `user_id`
- **THEN** the response includes `success=true`, `flow_id`, `email`, `group_id`, `account_name`, `oauth_url`, and the configured OAuth provider redirect URI
- **THEN** the response does not require `user_id`
- **THEN** `account_name` equals the submitted email value

#### Scenario: Invalid email is rejected before provisioning
- **GIVEN** the client submits an invalid email address to `POST /provision/start`
- **WHEN** request validation runs
- **THEN** the system rejects the request with a client error response
- **THEN** the system does not create a group, user, or flow record

#### Scenario: External email is not reconciled with the Sub2API user system
- **GIVEN** the submitted email matches or does not match an existing Sub2API user
- **WHEN** the system starts a provisioning flow
- **THEN** the system does not look up, create, or mutate a Sub2API user because of that email
- **THEN** the provisioning flow remains scoped to the dedicated group and future OAuth account

### Requirement: Complete OAuth using stored flow context
The system SHALL complete OpenAI OAuth from the stored flow context by accepting a pasted localhost callback URL, and MUST use the original external email as the OpenAI OAuth account name instead of any email returned by the OAuth provider.

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
- **THEN** the page displays an email input field for the external OAuth account email
- **THEN** submitting the email sends a request to `POST /provision/start`
- **THEN** a successful response is rendered back to the page for review without requiring a Sub2API user id
- **THEN** the page reveals a link or button that opens the returned OAuth URL
- **THEN** the page shows where the OAuth provider redirect will land
- **THEN** the page provides a textarea or input for pasting the final localhost callback URL
- **THEN** submitting the pasted callback URL sends a request to `POST /provision/oauth/complete`
- **THEN** the page provides a way to log out without restarting the service
