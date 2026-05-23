# credit-control Specification

## MODIFIED Requirements

### Requirement: Discover all user credit and consumption data
The system SHALL expose authenticated APIs for retrieving every Sub2API user's current credit/balance, consumption data, and persisted usage segment metadata for the balance management workspace.

#### Scenario: Operator lists user credit summaries
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator requests the credit-control user summary API
- **THEN** the system queries locally collected Sub2API user discovery data using the configured operational snapshots
- **THEN** the response includes every discovered user unless explicit filters are provided
- **THEN** each item includes user id, email or display identity, status, current balance or credit value using Sub2API's displayed unit/semantics when upstream provides it, current group context when known, and dashboard-safe raw metadata needed for troubleshooting
- **THEN** each item includes consumption fields for the selected usage window when upstream usage data is available
- **THEN** each item includes the latest persisted usage segment metadata when available
- **THEN** the supported consumption windows are `5h`, `1d`, `7d`, and `30d`
- **THEN** users missing upstream balance, consumption, or segment fields remain visible with those fields set to null or unknown instead of being silently omitted

#### Scenario: Operator filters and searches credit summaries
- **GIVEN** the operator is viewing the balance management tab
- **WHEN** the operator filters by search text, user status, group, balance range, consumption range, usage window, or usage segment
- **THEN** the API and UI return only matching users
- **THEN** the response includes total count and pagination metadata
- **THEN** invalid numeric ranges or unknown enum values are rejected with a client error response

### Requirement: Present a balance management tab in the operator UI
The React UI SHALL provide a top-level `余额管理` tab for inspecting all user balances, consumption, usage segments, manual adjustment controls, automatic recharge policies, and recent recharge audit outcomes.

#### Scenario: Authenticated operator opens the balance management tab
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator opens the React UI and selects `余额管理`
- **THEN** the UI loads user credit summaries from the authenticated credit-control API
- **THEN** the first screen prioritizes the all-user credit, segment, and consumption table
- **THEN** the UI provides search, filters, refresh, loading, empty, and error states without hiding the application shell
- **THEN** the UI shows aggregate totals for visible users, including user count, total balance when known, total consumption for the selected window when known, and segment counts when known

#### Scenario: Operator inspects one user's credit detail
- **GIVEN** the balance table contains at least one user
- **WHEN** the operator selects a user row
- **THEN** the UI opens a detail panel with current balance, recent consumption, persisted usage segment metadata, current group context, API key usage data when available, and recent credit adjustment audit entries for that user
- **THEN** the detail panel does not expose Sub2API admin credentials, access keys, or provider tokens
