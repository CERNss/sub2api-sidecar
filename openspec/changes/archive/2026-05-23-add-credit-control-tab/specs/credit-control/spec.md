# credit-control Specification

## ADDED Requirements

### Requirement: Discover all user credit and consumption data
The system SHALL expose authenticated APIs for retrieving every Sub2API user's current credit/balance and consumption data for the balance management workspace.

#### Scenario: Operator lists user credit summaries
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator requests the credit-control user summary API
- **THEN** the system queries Sub2API admin user discovery using the configured admin API key
- **THEN** the response includes every discovered user unless explicit filters are provided
- **THEN** each item includes user id, email or display identity, status, current balance or credit value using Sub2API's displayed unit/semantics when upstream provides it, current group context when known, and dashboard-safe raw metadata needed for troubleshooting
- **THEN** each item includes consumption fields for the selected usage window when upstream usage data is available
- **THEN** the supported consumption windows are `5h`, `1d`, `7d`, and `30d`
- **THEN** users missing upstream balance or consumption fields remain visible with those fields set to null instead of being silently omitted

#### Scenario: Operator filters and searches credit summaries
- **GIVEN** the operator is viewing the balance management tab
- **WHEN** the operator filters by search text, user status, group, balance range, consumption range, or usage window
- **THEN** the API and UI return only matching users
- **THEN** the response includes total count and pagination metadata
- **THEN** invalid numeric ranges or unknown enum values are rejected with a client error response

#### Scenario: Unauthenticated callers cannot inspect credit data
- **GIVEN** a caller has no valid admin session, access-key header, or bearer token
- **WHEN** the caller requests credit-control summary, detail, policy, or audit APIs
- **THEN** the system returns an authentication error
- **THEN** no user balance, consumption, recharge policy, or audit details are returned

### Requirement: Present a balance management tab in the operator UI
The React UI SHALL provide a top-level `余额管理` tab for inspecting all user balances, consumption, manual adjustment controls, automatic recharge policies, and recent recharge audit outcomes.

#### Scenario: Authenticated operator opens the balance management tab
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator opens the React UI and selects `余额管理`
- **THEN** the UI loads user credit summaries from the authenticated credit-control API
- **THEN** the first screen prioritizes the all-user credit and consumption table
- **THEN** the UI provides search, filters, refresh, loading, empty, and error states without hiding the application shell
- **THEN** the UI shows aggregate totals for visible users, including user count, total balance when known, and total consumption for the selected window when known

#### Scenario: Operator inspects one user's credit detail
- **GIVEN** the balance table contains at least one user
- **WHEN** the operator selects a user row
- **THEN** the UI opens a detail panel with current balance, recent consumption, current group context, API key usage data when available, and recent credit adjustment audit entries for that user
- **THEN** the detail panel does not expose Sub2API admin credentials, access keys, or provider tokens

### Requirement: Manually adjust user balances
The system SHALL let an authenticated operator increase or decrease user balances through confirmed Sub2API admin balance mutation APIs and SHALL record every requested adjustment locally.

#### Scenario: Operator increases one user's balance
- **GIVEN** the operator has selected a user with a known upstream balance
- **WHEN** the operator submits a positive balance adjustment amount with a reason
- **THEN** the system calls the confirmed Sub2API admin balance mutation endpoint for that user
- **THEN** the system calls `POST /api/v1/admin/users/{id}/balance` with `operation` set to `add`
- **THEN** the request represents an additive balance increase rather than an unintended absolute overwrite
- **THEN** the response includes the user id, requested delta, previous balance when known, new balance when upstream returns it, status, and upstream error message when applicable
- **THEN** the system persists an audit record for the adjustment attempt

#### Scenario: Operator decreases one user's balance
- **GIVEN** the operator has selected a user with a known upstream balance
- **WHEN** the operator submits a negative balance adjustment amount with a reason
- **THEN** the system calls the confirmed Sub2API admin balance mutation endpoint for that user
- **THEN** the system calls `POST /api/v1/admin/users/{id}/balance` with `operation` set to `subtract`
- **THEN** the request represents an additive balance decrease rather than an unintended absolute overwrite
- **THEN** the system follows Sub2API's own balance validation semantics and surfaces upstream rejection messages when the deduction is not allowed
- **THEN** the system persists an audit record for accepted and rejected adjustment attempts

#### Scenario: Operator adjusts a selected cohort
- **GIVEN** the operator has selected multiple users or a saved target cohort
- **WHEN** the operator submits a balance adjustment amount with a reason
- **THEN** the system validates the full target set before mutating upstream users
- **THEN** each upstream user adjustment is reported independently as succeeded, skipped, or failed
- **THEN** partial failures do not hide successful adjustments
- **THEN** the run audit contains the target scope, amount, reason, per-user outcomes, and timestamps

#### Scenario: Invalid manual adjustment is rejected
- **GIVEN** the operator submits a manual adjustment request
- **WHEN** the request has a zero amount, missing reason, empty target set, duplicate user ids, or an amount outside configured bounds
- **THEN** the system returns a client error response
- **THEN** the system does not call any Sub2API balance mutation API

### Requirement: Configure automatic recharge policies
The system SHALL let operators create, update, disable, preview, and delete automatic recharge policies with configurable timing, recurrence, amount, and target user scope.

#### Scenario: Operator creates a scheduled recharge policy
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator creates an automatic recharge policy
- **THEN** the request captures policy name, enabled state, recharge amount using Sub2API's balance unit, target scope, schedule type, timezone, start time, recurrence settings, optional end time, and reason template
- **THEN** the default timezone is `Asia/Shanghai`
- **THEN** the system validates amount, schedule, and target scope before saving
- **THEN** the system persists the policy in local durable storage
- **THEN** subsequent reads return the saved policy without requiring a service restart

#### Scenario: Operator configures one-time automatic recharge
- **GIVEN** the operator is editing an automatic recharge policy
- **WHEN** the operator chooses a custom one-time execution timestamp
- **THEN** the system stores that timestamp with timezone context
- **THEN** the scheduler executes the policy only once after the timestamp is reached
- **THEN** the policy is not executed again unless the operator re-enables or reschedules it

#### Scenario: Operator configures recurring automatic recharge
- **GIVEN** the operator is editing an automatic recharge policy
- **WHEN** the operator chooses a recurring schedule
- **THEN** the system supports at least daily, weekly, and monthly recurrence
- **THEN** the system stores the recurrence in a machine-readable form and a dashboard-readable summary
- **THEN** each due occurrence creates a recharge run using the current policy definition at execution time

#### Scenario: Operator defines the target user scope
- **GIVEN** the operator is editing an automatic recharge policy
- **WHEN** the operator selects the policy target scope
- **THEN** the system supports an explicit user-id list
- **THEN** the system supports a dynamic all-users scope
- **THEN** the system supports balance-threshold targeting when upstream balance data is available
- **THEN** the system supports selected-group targeting when upstream group context is available
- **THEN** the preview shows the concrete users that would be targeted at the time of preview
- **THEN** an empty resolved target set is reported as a skipped run rather than a successful recharge

#### Scenario: Invalid automatic recharge policy is rejected
- **GIVEN** the operator submits an automatic recharge policy
- **WHEN** the amount is zero or negative, the schedule is in the past for a one-time policy, the recurrence is malformed, or the target scope cannot be resolved
- **THEN** the system returns a client error response
- **THEN** the invalid policy is not persisted

### Requirement: Execute automatic recharge safely
The system SHALL execute due automatic recharge policies through the same validated balance adjustment path used by manual adjustments and SHALL persist run/audit records.

#### Scenario: Scheduler executes a due recharge policy
- **GIVEN** an enabled automatic recharge policy is due or became overdue while the sidecar was not running
- **WHEN** the scheduler evaluates policies after startup or on its normal interval
- **THEN** the system resolves the target user scope
- **THEN** the system applies the configured additive balance increase to each resolved user
- **THEN** the system records a recharge run with policy id, scheduled time, actual start and finish times, target count, success count, skipped count, failure count, and per-user outcomes
- **THEN** the next scheduled time is advanced for recurring policies

#### Scenario: Missed recharge occurrence is caught up once
- **GIVEN** an enabled automatic recharge policy was due while the sidecar process was stopped
- **WHEN** the sidecar starts and the scheduler evaluates overdue policies
- **THEN** the system creates a run for the missed occurrence
- **THEN** the system applies the configured additive recharge once for that occurrence
- **THEN** the occurrence is marked completed or failed so later scheduler ticks do not apply it again

#### Scenario: Disabled policy does not execute
- **GIVEN** an automatic recharge policy is disabled
- **WHEN** its configured schedule time arrives
- **THEN** the scheduler does not call any Sub2API balance mutation API
- **THEN** no successful recharge run is recorded for that disabled occurrence

#### Scenario: Preview does not mutate balances
- **GIVEN** the operator previews an automatic recharge policy
- **WHEN** the system resolves the target scope
- **THEN** the response lists the concrete target users and the amount that would be added
- **THEN** the system does not call any Sub2API balance mutation API
- **THEN** the preview result is either not persisted or is clearly marked as a dry-run audit entry

#### Scenario: Concurrent recharge runs are deduplicated
- **GIVEN** the scheduler or operator attempts to start the same due policy occurrence more than once
- **WHEN** a run for that policy occurrence is already in progress or completed
- **THEN** the system does not apply duplicate recharge to the same occurrence
- **THEN** the duplicate attempt is skipped or rejected with an auditable reason

### Requirement: Audit and protect credit-control operations
The system SHALL persist audit records for credit summary reads that mutate nothing only when useful, and SHALL always persist manual adjustment attempts, automatic policy changes, and automatic recharge run outcomes.

#### Scenario: Policy changes are audited
- **GIVEN** an authenticated operator creates, updates, disables, enables, or deletes an automatic recharge policy
- **WHEN** the request is accepted
- **THEN** the system persists an audit record with actor context, operation type, policy id, before/after policy summary when available, and timestamp
- **THEN** the audit record does not expose service credentials or secret values

#### Scenario: Operator inspects recent credit-control audit records
- **GIVEN** credit-control audit records exist
- **WHEN** the operator opens the balance management audit view or requests the audit API
- **THEN** the system returns recent records ordered by creation time descending
- **THEN** records can be filtered by user id, policy id, run id, operation type, and status
- **THEN** each record includes enough context to understand who or what changed a user's balance without exposing service credentials

#### Scenario: Sensitive data is redacted
- **GIVEN** a Sub2API response or local run payload contains admin credentials, API keys, bearer tokens, OAuth tokens, or webhook secrets
- **WHEN** the system returns credit-control API responses or renders the balance management tab
- **THEN** those sensitive values are omitted or replaced with a redaction marker
- **THEN** raw upstream response data stored for troubleshooting is sanitized before persistence

#### Scenario: Upstream balance mutation failure is auditable
- **GIVEN** a manual adjustment or automatic recharge reaches the upstream balance mutation API
- **WHEN** the upstream call fails for one or more users
- **THEN** the system records the failure status and sanitized error message for each failed user
- **THEN** the system does not report the run as fully successful
- **THEN** the UI displays the partial failure without losing successful per-user outcomes
