# orchestration-dashboard Specification

## MODIFIED Requirements

### Requirement: Persist webhook alert configuration server-side
The system SHALL expose authenticated APIs that read and write the webhook alert center configuration document to durable local storage. The configuration document contains only `webhooks` and `rules`; legacy `policy` blocks and per-field properties removed by this change are tolerated on read and rejected on write.

#### Scenario: Operator reads notification configuration
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator requests `GET /notifications/config`
- **THEN** the system returns the saved `webhooks` and `rules`
- **THEN** the response redacts every receiver `secret` value
- **THEN** if no configuration has been saved yet, the system returns a configuration containing one disabled placeholder receiver and an empty `rules` array
- **THEN** the response does not include a `policy` block
- **THEN** each rule omits `recoveryThreshold`, `warningThreshold`, `aggregation`, and `evaluationWindowMinutes`
- **THEN** each webhook omits `mentionOnFailure`

#### Scenario: Operator saves notification configuration
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator submits a configuration document to `PUT /notifications/config`
- **THEN** the system validates that every rule `targetWebhookIds` value references an existing receiver id
- **THEN** the system validates severity, operator, and provider enum values
- **THEN** the system rejects the request with a client error response when validation fails
- **THEN** the system persists the validated document so subsequent `GET /notifications/config` returns the same shape

#### Scenario: Request body containing removed fields is rejected
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator submits a configuration document containing any of `policy`, `rule.recoveryThreshold`, `rule.warningThreshold`, `rule.aggregation`, `rule.evaluationWindowMinutes`, or `webhook.mentionOnFailure`
- **THEN** the system rejects the request with a `422` client error response
- **THEN** the error message names the unsupported field
- **THEN** the system does not persist the document

#### Scenario: Legacy persisted document is tolerated on read
- **GIVEN** an existing saved document contains the now-removed `policy` block or any removed rule/webhook fields
- **WHEN** the system loads the document
- **THEN** the system drops the removed keys silently
- **THEN** the system returns the surviving `webhooks` and `rules`
- **THEN** the system synthesizes default rules only when no rules were saved

#### Scenario: Unauthenticated callers cannot read or write configuration
- **GIVEN** a caller has no valid admin session, access-key header, or bearer token
- **WHEN** the caller calls `GET /notifications/config` or `PUT /notifications/config`
- **THEN** the system returns an authentication error
- **THEN** the system does not return or modify configuration

### Requirement: Configure webhook alert receivers and routing
The React UI SHALL let an authenticated operator define webhook receivers and route operational alert rules to one or more receivers.

#### Scenario: Operator manages webhook receivers
- **GIVEN** the operator opens the notification settings view
- **WHEN** the operator adds or edits a webhook receiver
- **THEN** the UI captures receiver name, provider type (generic, feishu, dingtalk, wecom, slack, or discord), URL, optional secret, and enabled state
- **THEN** the operator can select which receiver is active for editing
- **THEN** the operator can delete a receiver only when at least one receiver remains

#### Scenario: Operator routes a rule to multiple receivers
- **GIVEN** at least one webhook receiver exists
- **AND** an alert rule is selected
- **WHEN** the operator selects target webhooks for the rule
- **THEN** the UI renders the available receivers as a vertical checkbox list
- **THEN** the rule stores the selected receiver ids
- **THEN** the UI summary shows how many rules target each receiver

#### Scenario: Invalid test delivery is rejected in the UI
- **GIVEN** the operator is editing an alert rule
- **WHEN** the operator requests a test notification
- **THEN** the UI requires at least one target receiver
- **THEN** every selected receiver must be enabled and have a non-empty URL
- **THEN** the UI reports a human-readable error instead of pretending delivery succeeded

### Requirement: Configure alert signal thresholds and evaluation cadence
The React UI SHALL let operators configure which operational signals are evaluated, how often they are read, and when notifications repeat or recover. The rule editor uses a minimum set of fields appropriate for discrete operational signals.

#### Scenario: Operator creates an alert rule from supported information classes
- **GIVEN** the operator opens the notification settings view
- **WHEN** the operator creates or edits an alert rule
- **THEN** the UI offers signal choices for platform API key health/quota/expiry/subscription/usage, user balance/API key/subscription usage, admin dashboard/usage/payment/channel/ops anomalies, and AI upstream account health/rate-limit/quota/auth/capacity
- **THEN** each signal choice carries a default source, unit, threshold, operator, severity, and read interval

#### Scenario: Operator configures threshold evaluation
- **GIVEN** an alert rule is selected
- **WHEN** the operator edits threshold settings
- **THEN** the UI captures rule name, enabled state, severity, comparison operator, trigger threshold, read interval minutes, sustained-for minutes, and cooldown minutes
- **THEN** the UI does not expose recovery threshold, warning threshold, aggregation, or evaluation window inputs
- **THEN** the UI allows the rule to send recovery notifications
- **THEN** the UI allows the rule to include a data snapshot in the outbound payload

#### Scenario: Notification configuration persists locally
- **GIVEN** the operator edits webhook receivers or rules
- **WHEN** the operator saves the settings
- **THEN** the current configuration is persisted in browser local storage
- **THEN** re-opening the dashboard restores the saved configuration when it is valid
- **THEN** older locally saved configurations containing removed fields are tolerated by silently dropping those fields

#### Scenario: Empty rule list shows onboarding state
- **GIVEN** the operator opens the notification settings view
- **AND** no rules have been saved
- **WHEN** the UI renders the rule editor area
- **THEN** the UI shows an empty-state card prompting the operator to add their first rule
- **THEN** the UI does not pre-populate any rules
- **THEN** the UI still renders the placeholder webhook so the rule editor has a deliverable target

### Requirement: Periodic rule evaluation
The system SHALL periodically evaluate enabled notification rules at the configured `readIntervalMinutes` cadence. Each tick reads the latest sample from the collector and evaluates the trigger condition against the most recent value; there is no separate evaluation window.

#### Scenario: Scheduler evaluates each enabled rule on its configured cadence
- **GIVEN** the service is running with notifications configured
- **AND** a rule is enabled with `readIntervalMinutes=5`
- **WHEN** five minutes elapse since the rule's last evaluation
- **THEN** the system runs the collector for that rule's `signalKey`
- **THEN** the system runs the evaluator with the collector's latest sample, the rule, and the prior rule state
- **THEN** the system persists the updated rule state regardless of decision

#### Scenario: Disabled rules are skipped
- **GIVEN** a rule is disabled
- **WHEN** the scheduler tick runs
- **THEN** the system does not call the collector for that rule
- **THEN** the system does not dispatch any delivery for that rule

#### Scenario: Collector with no data does not trigger an alert
- **GIVEN** a collector returns no sample for a rule
- **WHEN** the scheduler evaluates the rule
- **THEN** the rule decision is `no_data`
- **THEN** the system records a `no_data` reason on the rule state
- **THEN** the system does not dispatch any delivery
- **THEN** the system does not update `breach_started_at` or `last_alert_at`

### Requirement: Sustained breach, recovery, and cooldown semantics
The system SHALL enforce sustained-for and cooldown rules so alerts do not fire on transient blips and do not spam. Recovery is detected by the inverse of the trigger comparison applied to the latest sample; there is no separate recovery threshold.

#### Scenario: Single-evaluation breach does not fire when sustained-for is configured
- **GIVEN** a rule with `forMinutes=5`
- **AND** prior state shows no active breach
- **WHEN** the evaluator sees a single sample that crosses the threshold
- **THEN** the decision is `hold`
- **THEN** the system records `breach_started_at=now` on the rule state
- **THEN** the system does not dispatch a delivery

#### Scenario: Sustained breach fires after the configured window
- **GIVEN** a rule with `forMinutes=5`
- **AND** prior state shows `breach_started_at` is at least five minutes ago
- **WHEN** the evaluator sees a sample that still crosses the threshold
- **THEN** the decision is `fire`
- **THEN** the system updates `is_firing=true` and `last_alert_at=now`
- **THEN** the system dispatches a delivery to the rule's enabled receivers

#### Scenario: Cooldown suppresses re-firing
- **GIVEN** a rule with `cooldownMinutes=30` is currently firing
- **AND** `last_alert_at` was less than thirty minutes ago
- **WHEN** the evaluator sees another sample that still crosses the threshold
- **THEN** the decision is `hold`
- **THEN** the system does not dispatch a delivery
- **THEN** the rule state remains firing

#### Scenario: Recovery transition fires a recovery delivery
- **GIVEN** a rule with `includeResolved=true`
- **AND** prior state shows `is_firing=true`
- **WHEN** the evaluator sees a sample that no longer crosses the trigger threshold (the inverse comparison holds)
- **THEN** the decision is `recover`
- **THEN** the system updates `is_firing=false` and clears `breach_started_at`
- **THEN** the system dispatches a recovery message via the same receivers

#### Scenario: Recovery without includeResolved updates state without sending
- **GIVEN** a rule with `includeResolved=false`
- **AND** prior state shows `is_firing=true`
- **WHEN** the evaluator sees a sample that no longer crosses the trigger threshold
- **THEN** the system updates `is_firing=false`
- **THEN** the system does not dispatch any delivery

## REMOVED Requirements

### Requirement: Quiet hours suppress outbound but not state
**Reason**: The global `policy` block and quiet-hours toggle are removed. Per-rule `cooldownMinutes` already handles repeat suppression for the common case, and the policy-level suppression was a silent footgun (the scheduler logged `fire` but delivery was suppressed somewhere unrelated). Quiet hours can return at the rule level in a future change if a real need surfaces.
**Migration**: Set per-rule `cooldownMinutes` to the desired silence window. Stale saved documents containing `policy.quietHours*` are loaded with the policy block dropped. Stale clients posting a `policy` block to `PUT /notifications/config` receive a 422 naming the unsupported field.
