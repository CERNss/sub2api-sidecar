## MODIFIED Requirements

### Requirement: Periodic rule evaluation
The system SHALL periodically evaluate enabled notification rules at the configured `readIntervalMinutes` cadence. The system SHALL replace the previous per-rule live upstream collector design with an operational data pipeline that first collects upstream data, then persists normalized local snapshots and metric samples, then evaluates rules from local samples. Rule evaluation SHALL read the latest local metric sample for each rule instead of calling upstream Sub2API collectors per rule.

#### Scenario: Operational data runtime settings control collection
- **GIVEN** the service starts after this change
- **WHEN** operational data runtime settings are loaded
- **THEN** the system reads operational data enabled state and optional expiration seconds from SQLite runtime settings
- **THEN** there is no deployment config field for changing the collection interval
- **THEN** an unset operational data expiration means persisted local data does not expire

#### Scenario: Scheduler samples upstream data before evaluating local rules
- **GIVEN** the service is running with notification scheduling enabled
- **WHEN** a scheduler tick begins
- **THEN** the collection stage fetches Sub2API accounts from `Sub2APIClient.list_openai_accounts()`
- **THEN** the collection stage fetches Sub2API groups from `Sub2APIClient.list_groups(platform="openai")`
- **THEN** the collection stage fetches Sub2API users from `Sub2APIClient.list_users()`
- **THEN** the collection stage fetches current-day and previous-day usage from `Sub2APIClient.get_usage_stats(...)`
- **THEN** the persistence stage stores raw source snapshots in SQLite operational data snapshot tables
- **THEN** the persistence stage stores derived metric samples in SQLite operational metric sample tables
- **THEN** the persistence stage stores per-source collection status in SQLite notification source-status tables
- **THEN** the evaluation stage evaluates due enabled rules using SQLite notification config, local metric samples, and notification rule state
- **THEN** the system persists the updated rule state regardless of decision

#### Scenario: Rule cadence reads local samples
- **GIVEN** the service is running with notifications configured
- **AND** a rule is enabled with `readIntervalMinutes=5`
- **WHEN** five minutes elapse since the rule's last evaluation
- **THEN** the system reads the latest stored sample for that rule's `signalKey`
- **THEN** the system runs the evaluator with the stored sample, the rule, and the prior rule state
- **THEN** the system does not perform a per-rule upstream Sub2API fetch

#### Scenario: Disabled rules are skipped
- **GIVEN** a rule is disabled
- **WHEN** the scheduler tick runs
- **THEN** the system does not evaluate that rule
- **THEN** the system does not dispatch any delivery for that rule

#### Scenario: Missing or expired local data does not trigger an alert
- **GIVEN** no local sample exists for a rule or the latest sample is expired by the configured operational data expiration
- **WHEN** the scheduler evaluates the rule
- **THEN** the rule decision is `no_data`
- **THEN** the system records a `no_data` reason on the rule state
- **THEN** the system does not dispatch any delivery
- **THEN** the system does not update `breach_started_at` or `last_alert_at`

#### Scenario: Scheduler status exposes sampling freshness
- **GIVEN** an authenticated operator
- **WHEN** the operator requests `GET /api/operational-data/status`
- **THEN** the response includes whether the scheduler is enabled and running
- **THEN** the response includes the last sampling start and finish timestamps
- **THEN** the response includes the last sampling error, sampled signal count, and per-source status details

#### Scenario: Pipeline stage data sources are explicit
- **GIVEN** a maintainer reads the operational data pipeline specification or status output
- **WHEN** they inspect how data moves through the pipeline
- **THEN** the collection stage identifies Sub2API accounts, groups, users, current-day usage, and previous-day usage as upstream data sources
- **THEN** the persistence stage identifies local SQLite snapshot, metric sample, and source-status tables as storage destinations
- **THEN** the evaluation stage identifies local SQLite notification config, metric sample, and rule-state tables as its only data sources

### Requirement: On-demand rule evaluation
The system SHALL expose an authenticated API for evaluating a single rule once and reporting the decision and outbound deliveries. On-demand evaluation SHALL refresh the local operational data first, then evaluate the requested rule from the same local metric sample store used by scheduled evaluation.

#### Scenario: Operator runs an on-demand evaluation
- **GIVEN** an authenticated operator
- **AND** an enabled rule whose targeted receivers include at least one enabled receiver with a non-empty URL
- **WHEN** the operator calls `POST /notifications/evaluate` with that rule id
- **THEN** the system refreshes the local operational data from Sub2API
- **THEN** the system reads the latest stored sample for the requested rule's `signalKey`
- **THEN** the system runs the evaluator with the saved prior state
- **THEN** the system updates the saved rule state
- **THEN** the system dispatches deliveries when the decision is `fire` or `recover`
- **THEN** the response includes the decision, the rule state snapshot, and the per-receiver outcomes

#### Scenario: Evaluate request rejects unknown rule
- **GIVEN** an authenticated operator
- **WHEN** the operator calls `POST /notifications/evaluate` with an id that does not match any saved rule
- **THEN** the system returns a client error response
- **THEN** the system does not dispatch any delivery work
