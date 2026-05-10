## ADDED Requirements

### Requirement: Periodic rule evaluation
The system SHALL periodically evaluate enabled notification rules at the configured `readIntervalMinutes` cadence.

#### Scenario: Scheduler evaluates each enabled rule on its configured cadence
- **GIVEN** the service is running with notifications configured
- **AND** a rule is enabled with `readIntervalMinutes=5`
- **WHEN** five minutes elapse since the rule's last evaluation
- **THEN** the system runs the collector for that rule's `signalKey`
- **THEN** the system runs the evaluator with the collector sample, the rule, and the prior rule state
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
The system SHALL enforce sustained-for, recovery, and cooldown rules so alerts do not fire on transient blips, do not flap, and do not spam.

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
- **GIVEN** a rule with `recoveryThreshold` set and `includeResolved=true`
- **AND** prior state shows `is_firing=true`
- **WHEN** the evaluator sees a sample that crosses the recovery threshold
- **THEN** the decision is `recover`
- **THEN** the system updates `is_firing=false` and clears `breach_started_at`
- **THEN** the system dispatches a recovery message via the same receivers

#### Scenario: Recovery without includeResolved updates state without sending
- **GIVEN** a rule with `includeResolved=false`
- **AND** prior state shows `is_firing=true`
- **WHEN** the evaluator sees a sample that crosses the recovery threshold
- **THEN** the system updates `is_firing=false`
- **THEN** the system does not dispatch any delivery

### Requirement: Quiet hours suppress outbound but not state
The system SHALL skip delivery during the configured quiet-hours window while still updating rule state.

#### Scenario: Firing rule during quiet hours is suppressed
- **GIVEN** the routing policy has quiet hours enabled with `quietHoursStart=22:00` and `quietHoursEnd=08:00`
- **AND** the current local time is `02:00`
- **WHEN** a rule would otherwise fire
- **THEN** the decision is `suppress`
- **THEN** the system writes a `skipped` audit row tagged `trigger='rule'` for each target receiver
- **THEN** the system does not perform any outbound HTTP request
- **THEN** the rule state still records the breach and `last_alert_at` so resumption is correct after the window

#### Scenario: Recovering rule outside quiet hours sends normally
- **GIVEN** quiet hours are disabled
- **WHEN** a rule recovers
- **THEN** the system delivers the recovery message through the rule's enabled receivers

### Requirement: On-demand rule evaluation
The system SHALL expose an authenticated API for evaluating a single rule once and reporting the decision and outbound deliveries.

#### Scenario: Operator runs an on-demand evaluation
- **GIVEN** an authenticated operator
- **AND** an enabled rule whose targeted receivers include at least one enabled receiver with a non-empty URL
- **WHEN** the operator calls `POST /notifications/evaluate` with that rule id
- **THEN** the system runs the collector for the rule's `signalKey`
- **THEN** the system runs the evaluator with the saved prior state
- **THEN** the system updates the saved rule state
- **THEN** the system dispatches deliveries when the decision is `fire` or `recover`
- **THEN** the response includes the decision, the rule state snapshot, and the per-receiver outcomes

#### Scenario: Evaluate request rejects unknown rule
- **GIVEN** an authenticated operator
- **WHEN** the operator calls `POST /notifications/evaluate` with an id that does not match any saved rule
- **THEN** the system returns a client error response
- **THEN** the system does not perform any collector or delivery work
