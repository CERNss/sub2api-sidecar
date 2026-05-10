# orchestration-dashboard Specification

## MODIFIED Requirements

### Requirement: React dashboard renders orchestration state
The React UI SHALL provide an authenticated orchestration workspace for moving existing users or keys between groups, browsing provisioning flows, and configuring webhook alert routing for operational signals.

#### Scenario: Authenticated operator opens the dashboard
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator opens the React UI
- **THEN** the default view is existing user/group orchestration
- **THEN** the UI provides access to the existing provisioning form and a flow dashboard view
- **THEN** the UI provides a top-level notification settings view beside orchestration and OAuth provisioning
- **THEN** the orchestration view displays a draggable relationship graph ordered left-to-right as all API keys, all users, and all groups
- **THEN** selecting a user or key in the graph updates the operator selection controls
- **THEN** the dashboard displays flow summary rows with status, email, user id, group id, assignment mode, account id, and update time

### Requirement: Configure webhook alert receivers and routing
The React UI SHALL let an authenticated operator define webhook receivers and route operational alert rules to one or more receivers.

#### Scenario: Operator manages webhook receivers
- **GIVEN** the operator opens the notification settings view
- **WHEN** the operator adds or edits a webhook receiver
- **THEN** the UI captures receiver name, provider type (generic, feishu, dingtalk, wecom, slack, or discord), URL, optional secret, enabled state, and failure mention behavior
- **THEN** the operator can select which receiver is active for editing
- **THEN** the operator can delete a receiver only when at least one receiver remains

#### Scenario: Operator routes a rule to multiple receivers
- **GIVEN** at least one webhook receiver exists
- **AND** an alert rule is selected
- **WHEN** the operator selects target webhooks for the rule
- **THEN** the rule stores the selected receiver ids
- **THEN** the UI summary shows how many rules target each receiver

#### Scenario: Invalid test delivery is rejected in the UI
- **GIVEN** the operator is editing an alert rule
- **WHEN** the operator requests a test notification
- **THEN** the UI requires at least one target receiver
- **THEN** every selected receiver must be enabled and have a non-empty URL
- **THEN** the UI reports a human-readable error instead of pretending delivery succeeded

### Requirement: Configure alert signal thresholds and evaluation cadence
The React UI SHALL let operators configure which operational signals are evaluated, how often they are read, how values are evaluated, and when notifications repeat or recover.

#### Scenario: Operator creates an alert rule from supported information classes
- **GIVEN** the operator opens the notification settings view
- **WHEN** the operator creates or edits an alert rule
- **THEN** the UI offers signal choices for platform API key health/quota/expiry/subscription/usage, user balance/API key/subscription usage, admin dashboard/usage/payment/channel/ops anomalies, and AI upstream account health/rate-limit/quota/auth/capacity
- **THEN** each signal choice carries a default source, unit, threshold, operator, aggregation, severity, read interval, and evaluation window

#### Scenario: Operator configures threshold evaluation
- **GIVEN** an alert rule is selected
- **WHEN** the operator edits threshold settings
- **THEN** the UI captures rule name, enabled state, severity, aggregation, comparison operator, trigger threshold, optional recovery threshold, read interval minutes, evaluation window minutes, sustained-for minutes, and repeat/cooldown minutes
- **THEN** the UI allows the rule to send recovery notifications
- **THEN** the UI allows the rule to include a data snapshot in the outbound payload

#### Scenario: Operator configures routing noise controls
- **GIVEN** the operator opens the notification settings view
- **WHEN** the operator edits routing controls
- **THEN** the UI captures grouping by severity, signal, or source
- **THEN** the UI captures group-wait minutes and default repeat interval minutes
- **THEN** the UI captures quiet-hours enablement and start/end times

#### Scenario: Notification configuration persists locally
- **GIVEN** the operator edits webhook receivers, rules, or routing policy
- **WHEN** the operator saves the settings
- **THEN** the current configuration is persisted in browser local storage
- **THEN** re-opening the dashboard restores the saved configuration when it is valid
- **THEN** older locally saved receiver-only configuration is tolerated by generating default rules routed to the first receiver
