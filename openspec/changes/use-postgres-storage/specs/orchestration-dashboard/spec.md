## MODIFIED Requirements

### Requirement: Authenticated flow inspection API
The system SHALL expose authenticated read-only APIs for listing provisioning flows and retrieving a single flow with its orchestration timeline.

#### Scenario: Unauthenticated callers cannot inspect flows
- **GIVEN** a caller has no valid admin session, access-key header, or bearer token
- **WHEN** the caller requests `GET /provision/flows` or `GET /provision/flows/{flow_id}`
- **THEN** the system returns an authentication error
- **THEN** no flow details are returned

#### Scenario: Operator lists recent provisioning flows
- **GIVEN** the operator has a valid admin session
- **AND** multiple provisioning flows exist in the PostgreSQL store
- **WHEN** the operator requests `GET /provision/flows`
- **THEN** the system returns a success envelope containing flow summary items
- **THEN** each item includes `flow_id`, `email`, `group_id`, `status`, `account_name`, `oauth_account_id`, `error_message`, `created_at`, and `updated_at`
- **THEN** `email` is presented as the external OAuth account email, not as a Sub2API user email
- **THEN** `user_id` is optional and omitted or null for OAuth pre-provisioning flows that did not create a Sub2API user
- **THEN** `assignment_mode` is optional and omitted or null when no managed user assignment exists
- **THEN** the items are ordered by `updated_at` descending and then `created_at` descending
- **THEN** the response includes pagination metadata for `limit`, `offset`, and `total`

#### Scenario: Operator filters flow list
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator requests `GET /provision/flows` with `status`, `assignment_mode`, or `email` query parameters
- **THEN** the system returns only flows matching the provided filters
- **THEN** the `email` filter matches the external OAuth account email stored on the flow
- **THEN** invalid enum filter values are rejected with a client error response

#### Scenario: Operator retrieves flow detail and timeline
- **GIVEN** the operator has a valid admin session
- **AND** a provisioning flow exists for the requested `flow_id`
- **WHEN** the operator requests `GET /provision/flows/{flow_id}`
- **THEN** the system returns the full dashboard-safe flow detail
- **THEN** the response identifies the stored email as the external OAuth account email
- **THEN** the response does not require a Sub2API user id for the flow
- **THEN** the response includes persisted timeline events for the flow ordered by creation time ascending
- **THEN** each event includes `event_id`, `flow_id`, `event_type`, `status`, `message`, `details`, and `created_at`

#### Scenario: Missing flow detail returns not found
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator requests `GET /provision/flows/{flow_id}` for an unknown flow
- **THEN** the system returns a not-found error response
