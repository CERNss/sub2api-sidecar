## MODIFIED Requirements

### Requirement: Existing user/group orchestration API
The system SHALL expose authenticated APIs for discovering existing Sub2API users, groups, API keys, upstream accounts from the Sub2API admin accounts surface, and moving API key routing between groups.

#### Scenario: Operator discovers existing users, groups, keys, and upstream accounts
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator requests existing user, group, account, or user API key discovery APIs
- **THEN** the system queries Sub2API admin APIs using the configured admin API key
- **THEN** users include current group context when upstream or local assignment data provides it
- **THEN** groups identify whether they are supported for bulk replace-group orchestration
- **THEN** upstream accounts are discovered from the Sub2API admin accounts API that backs `/admin/accounts`, not from the admin users API
- **THEN** accounts include their upstream account identity and group binding ids when upstream metadata provides them
- **THEN** accounts include normalized availability status, availability reason, schedulability, rate-limit, quota, last-error, and freshness fields when upstream metadata provides them
- **THEN** accounts include concurrency and current concurrency fields when upstream metadata provides them
- **THEN** accounts include 5-hour and 7-day usage percentages from the Sub2API admin accounts metadata when available

### Requirement: React dashboard renders orchestration state
The React UI SHALL provide an authenticated orchestration workspace for moving existing users or keys between groups, browsing provisioning flows, and configuring webhook alert routing for operational signals.

#### Scenario: Authenticated operator opens the dashboard
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator opens the React UI
- **THEN** the default view is existing user/group orchestration
- **THEN** the UI provides access to the existing OAuth account provisioning form and a flow dashboard view
- **THEN** the UI provides a top-level notification settings view beside orchestration and OAuth provisioning
- **THEN** the orchestration view displays a draggable relationship graph ordered left-to-right as all API keys, all users, all groups, and upstream accounts
- **THEN** group nodes display current group capacity by summing bound account concurrency and current concurrency values in addition to their user, key, and account relationship counts
- **THEN** capacity displays use green styling below 80% usage, yellow styling at or above 80%, and red styling at or above 100%
- **THEN** groups are connected to upstream account nodes when account group binding data is available
- **THEN** upstream account nodes display whether the account is unavailable and show current account capacity with compact 5-hour and 7-day usage percentages
- **THEN** selecting a user or key in the graph updates the operator selection controls
- **THEN** the dashboard displays flow summary rows with status, external OAuth account email, group id, account id, and update time
- **THEN** the dashboard does not present OAuth provisioning flows as Sub2API user creation records
