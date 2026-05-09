# orchestration-dashboard Specification

## Purpose
Define the authenticated operator dashboard for inspecting provisioning flows and orchestrating existing Sub2API users, API keys, and groups. The dashboard uses effective admin migration endpoints for existing resources, persists and redacts provisioning timeline data, and renders the React workspace used by operators.
## Requirements
### Requirement: Existing user/group orchestration API
The system SHALL expose authenticated APIs for discovering existing Sub2API users, groups, API keys, and moving API key routing between groups.

#### Scenario: Operator discovers existing users, groups, and keys
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator requests existing user, group, or user API key discovery APIs
- **THEN** the system queries Sub2API admin APIs using the configured admin API key
- **THEN** users include current group context when upstream or local assignment data provides it
- **THEN** groups identify whether they are supported for bulk replace-group orchestration

#### Scenario: Operator migrates an existing user from one group to another
- **GIVEN** the operator has selected an existing user, an old group, and a target group
- **WHEN** the operator executes bulk user/group orchestration
- **THEN** the system calls upstream `POST /api/v1/admin/users/{user_id}/replace-group`
- **THEN** the request body includes `old_group_id` and `new_group_id`
- **THEN** the system does not use a user `allowed_groups` update as the effective orchestration operation
- **THEN** the system records the resulting local assignment and rotation event

#### Scenario: Operator moves a single API key to another group
- **GIVEN** the operator has selected an existing user, one API key, and a target group
- **WHEN** the operator executes single-key orchestration
- **THEN** the system calls upstream `PUT /api/v1/admin/api-keys/{key_id}`
- **THEN** the request body includes `group_id`
- **THEN** the system records a local rotation event for the key move

#### Scenario: Subscription groups are not used for bulk replace-group
- **GIVEN** an upstream group is a subscription group
- **WHEN** the operator attempts bulk replace-group orchestration to that group
- **THEN** the system rejects the request with a client error
- **THEN** the operator may still use a supported single-key group update path where appropriate

### Requirement: Authenticated flow inspection API
The system SHALL expose authenticated read-only APIs for listing provisioning flows and retrieving a single flow with its orchestration timeline.

#### Scenario: Unauthenticated callers cannot inspect flows
- **GIVEN** a caller has no valid admin session, access-key header, or bearer token
- **WHEN** the caller requests `GET /provision/flows` or `GET /provision/flows/{flow_id}`
- **THEN** the system returns an authentication error
- **THEN** no flow details are returned

#### Scenario: Operator lists recent provisioning flows
- **GIVEN** the operator has a valid admin session
- **AND** multiple provisioning flows exist in the SQLite store
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

### Requirement: Persist provisioning timeline events
The system SHALL persist significant provisioning steps as timeline events tied to the flow id.

#### Scenario: Start flow records account and group orchestration progress
- **GIVEN** the operator starts a provisioning flow with a valid external OAuth account email
- **WHEN** the system creates the flow, creates the dedicated group, and generates the OAuth URL
- **THEN** the system persists timeline events for the start request, dedicated group creation, OAuth URL generation, and pending OAuth handoff
- **THEN** the system does not persist user creation or user group binding events for OAuth pre-provisioning flows
- **THEN** each event is tied to the created `flow_id`

#### Scenario: OAuth completion records orchestration progress
- **GIVEN** a provisioning flow is pending OAuth
- **WHEN** the operator submits a valid pasted callback URL
- **THEN** the system persists timeline events for callback parsing, OAuth code exchange, OpenAI OAuth account creation, account group binding, and completion
- **THEN** the flow detail timeline shows the completion path in chronological order

#### Scenario: Failures are recorded on the flow timeline
- **GIVEN** a provisioning step fails after a flow id has been created or loaded
- **WHEN** the system marks the flow as failed or surfaces the provisioning error
- **THEN** the system persists a failed timeline event with a human-readable message
- **THEN** the flow record retains the failure status or error message that the dashboard can display

### Requirement: Dashboard responses redact sensitive values
The system SHALL redact secrets and provider tokens from orchestration dashboard API responses.

#### Scenario: OAuth token fields are not exposed
- **GIVEN** a completed provisioning flow stores an OAuth exchange payload containing `access_token`, `refresh_token`, or similarly sensitive fields
- **WHEN** the operator requests the flow detail API
- **THEN** the system does not return raw secret values for those fields
- **THEN** the system either omits the sensitive fields or replaces their values with a redaction marker

#### Scenario: Dashboard never exposes service credentials
- **GIVEN** the service is configured with Sub2API admin credentials or an ephemeral admin access key
- **WHEN** the operator uses dashboard APIs or the dashboard UI
- **THEN** the response payload and rendered UI do not expose the Sub2API admin API key, default user password, or browser session access key

### Requirement: React dashboard renders orchestration state
The React UI SHALL provide an authenticated orchestration workspace for moving existing users or keys between groups and browsing provisioning flows.

#### Scenario: Authenticated operator opens the dashboard
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator opens the React UI
- **THEN** the default view is existing user/group orchestration
- **THEN** the UI provides access to the existing OAuth account provisioning form and a flow dashboard view
- **THEN** the orchestration view displays a draggable relationship graph ordered left-to-right as all API keys, all users, and all groups
- **THEN** selecting a user or key in the graph updates the operator selection controls
- **THEN** the dashboard displays flow summary rows with status, external OAuth account email, group id, account id, and update time
- **THEN** the dashboard does not present OAuth provisioning flows as Sub2API user creation records

#### Scenario: Operator executes existing user/group orchestration
- **GIVEN** the operator is using the existing user/group orchestration view
- **WHEN** the operator selects bulk replace-group mode and executes
- **THEN** the UI submits the source group and target group to the authenticated replace-group orchestration API
- **WHEN** the operator selects single-key mode and executes
- **THEN** the UI submits the selected API key and target group to the authenticated API key group update API

#### Scenario: Operator filters and refreshes dashboard data
- **GIVEN** the operator is viewing the flow dashboard
- **WHEN** the operator changes status, assignment mode, or email filters and refreshes
- **THEN** the UI reloads data from the authenticated flow list API using those filters
- **THEN** the UI labels the email filter as external OAuth account email
- **THEN** the UI displays empty, loading, and error states without hiding the rest of the application shell

#### Scenario: Operator inspects a flow detail panel
- **GIVEN** the dashboard shows at least one flow
- **WHEN** the operator selects a flow
- **THEN** the UI shows the flow detail panel
- **THEN** the detail panel includes the OAuth handoff URL when present, the callback redirect URI, failure message when present, and the timeline events for that flow
- **THEN** pending flows include enough state context for the operator to construct or verify a pasted callback URL
- **THEN** the detail panel does not require or invent a Sub2API user id for account/group scoped OAuth flows

#### Scenario: Dashboard remains read-only
- **GIVEN** the operator is using the dashboard view
- **WHEN** the operator inspects historical or active flows
- **THEN** the dashboard does not mutate flow records, retry failed flows, or complete OAuth unless the operator uses the existing paste-back completion form
