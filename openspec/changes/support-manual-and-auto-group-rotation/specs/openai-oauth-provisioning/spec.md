# openai-oauth-provisioning Specification

## MODIFIED Requirements

### Requirement: Start provisioning flow from email
The system SHALL expose a `POST /provision/start` endpoint that accepts an email address, validates it, resolves the target Sub2API group assignment for the user, provisions the required Sub2API resources, persists flow context, and returns the pending OAuth handoff details.

#### Scenario: Dedicated mode creates a pending OAuth flow
- **GIVEN** the client submits a valid email address to `POST /provision/start`
- **AND** the configured provisioning assignment mode is `dedicated`
- **WHEN** the system starts a provisioning flow
- **THEN** the system creates a dedicated group whose name is derived from the configured group prefix and the submitted email
- **THEN** the dedicated group creation request includes the configured OpenAI platform value
- **THEN** the system creates a user for the submitted email through the Sub2API admin API
- **THEN** the system binds that user to the dedicated group
- **THEN** the system generates an OpenAI OAuth login URL through the Sub2API admin API using the configured OAuth provider redirect URI
- **THEN** the system stores a flow record containing `flow_id`, `email`, `user_id`, `group_id`, `state`, `assignment_mode=dedicated`, and `status=pending_oauth`
- **THEN** the response includes `success=true`, `flow_id`, `email`, `user_id`, `group_id`, `account_name`, `oauth_url`, and the configured OAuth provider redirect URI
- **THEN** `account_name` equals the submitted email value

#### Scenario: Managed-pool mode assigns a pending OAuth flow to an existing dedicated rotation group
- **GIVEN** the client submits a valid email address to `POST /provision/start`
- **AND** the configured provisioning assignment mode is `managed_pool`
- **AND** at least one dedicated rotation-target group has been selected into the local rotation pool
- **WHEN** the system starts a provisioning flow
- **THEN** the system selects a target group from the persisted dedicated rotation-group pool using the current assignment policy
- **THEN** the selected target group belongs to the configured dedicated rotation pool and is not a public group
- **THEN** the system does not create a new dedicated group for the flow
- **THEN** the system creates a user for the submitted email through the Sub2API admin API
- **THEN** the system binds that user to the selected dedicated rotation group
- **THEN** the system generates an OpenAI OAuth login URL through the Sub2API admin API using the configured OAuth provider redirect URI
- **THEN** the system stores a flow record containing `flow_id`, `email`, `user_id`, `group_id`, `state`, `assignment_mode=managed_pool`, and `status=pending_oauth`
- **THEN** the response includes the selected `group_id` instead of a newly created dedicated group id
- **THEN** `account_name` equals the submitted email value

#### Scenario: Managed-pool mode rejects provisioning when no rotation target is available
- **GIVEN** the client submits a valid email address to `POST /provision/start`
- **AND** the configured provisioning assignment mode is `managed_pool`
- **AND** the local dedicated rotation pool is empty
- **WHEN** the system starts a provisioning flow
- **THEN** the system returns an error response indicating that no dedicated rotation target is available
- **THEN** the system does not create a user, group, or flow record

#### Scenario: Invalid email is rejected before provisioning
- **GIVEN** the client submits an invalid email address to `POST /provision/start`
- **WHEN** request validation runs
- **THEN** the system rejects the request with a client error response
- **THEN** the system does not create a group, user, or flow record

### Requirement: Complete OAuth using stored flow context
The system SHALL complete OpenAI OAuth from the stored flow context by accepting a pasted localhost callback URL, and MUST use the original entry email as the OpenAI OAuth account name and the group assignment recorded on the flow instead of recalculating the target group during callback handling.

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
- **THEN** the account creation request targets the group recorded on the flow at provisioning start
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
