## MODIFIED Requirements

### Requirement: Existing user/group orchestration API
The system SHALL expose authenticated APIs for discovering existing Sub2API users, groups, API keys, and moving API key routing between groups.

#### Scenario: Operator transfers admin key ownership by encoded email
- **GIVEN** the operator has a valid admin session
- **AND** an admin user's API key name matches the `service:object:version:email` pattern
- **WHEN** the operator previews or executes the key transfer
- **THEN** the system resolves `<email>` to exactly one existing Sub2API user by normalized email
- **THEN** the system does not create users and does not fuzzy-match email values
- **THEN** the system selects the first available group from the matched user's current or allowed groups
- **THEN** execution calls the Sub2API admin API to update the API key's `user_id`, `group_id`, and `quota`
- **THEN** execution preserves the API key string value
- **THEN** execution sets the API key quota limit to unlimited
- **THEN** the response reports moved, skipped, and failed keys with reasons

#### Scenario: Operator transfers keys discovered across all users
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator previews or executes key transfer with all-user scope
- **THEN** the system lists upstream users through the Sub2API admin users API
- **THEN** the system reads each user's API keys through the existing per-user API keys API
- **THEN** the system applies the same exact-email, first-available-group, preserved-key-value, and unlimited-quota rules as admin key transfer
- **THEN** the response identifies each key's source user and reports moved, skipped, and failed keys with reasons

#### Scenario: Key transfer skips unsafe keys
- **GIVEN** the operator previews or executes the admin key transfer
- **WHEN** a key name does not contain exactly one valid target email, the target user is missing, or the target user has no available group
- **THEN** the system skips that key without making an upstream update call
- **THEN** the response includes a reason for the skipped key

### Requirement: React dashboard renders orchestration state
The React UI SHALL provide an authenticated orchestration workspace for moving existing users or keys between groups, browsing provisioning flows, and configuring webhook alert routing for operational signals.

#### Scenario: Operator previews and runs admin key transfer
- **GIVEN** the operator is using the key transfer tab
- **WHEN** the operator switches between admin-user scope and all-user scope
- **THEN** the UI refreshes the candidate key list from the matching authenticated API
- **WHEN** the operator previews admin key transfer
- **THEN** the UI shows moved/skipped/failed counts and per-key reasons returned by the authenticated API
- **WHEN** the operator executes admin key transfer
- **THEN** the UI submits to the authenticated execution API and refreshes orchestration data after completion
