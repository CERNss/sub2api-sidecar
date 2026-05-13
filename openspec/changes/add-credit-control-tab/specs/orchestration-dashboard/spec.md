# orchestration-dashboard Specification

## MODIFIED Requirements

### Requirement: React dashboard renders orchestration state
The React UI SHALL provide an authenticated orchestration workspace for moving existing users or keys between groups, browsing provisioning flows, configuring webhook alert routing for operational signals, and accessing balance management.

#### Scenario: Authenticated operator opens the dashboard
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator opens the React UI
- **THEN** the default view is existing user/group orchestration
- **THEN** the UI provides access to the existing OAuth account provisioning form and a flow dashboard view
- **THEN** the UI provides a top-level notification settings view beside orchestration and OAuth provisioning
- **THEN** the UI provides a top-level `余额管理` view beside orchestration, OAuth provisioning, and notification settings
- **THEN** the orchestration view displays a draggable relationship graph ordered left-to-right as all API keys, all users, and all groups
- **THEN** selecting a user or key in the graph updates the operator selection controls
- **THEN** the dashboard displays flow summary rows with status, external OAuth account email, group id, account id, and update time
- **THEN** the dashboard does not present OAuth provisioning flows as Sub2API user creation records
