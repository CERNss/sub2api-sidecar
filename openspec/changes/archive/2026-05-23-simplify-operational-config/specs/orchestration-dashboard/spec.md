## MODIFIED Requirements

### Requirement: Periodic rule evaluation
The system SHALL periodically evaluate enabled notification rules at the configured `readIntervalMinutes` cadence. The system SHALL use a neutral operational data pipeline that first collects upstream data on the persisted operational-data runtime interval, then persists normalized local snapshots and metric samples, then evaluates rules from local samples. Rule evaluation SHALL read the latest local metric sample for each rule instead of calling upstream Sub2API collectors per rule.

#### Scenario: Operational data configuration controls collection
- **GIVEN** the service starts after this change
- **WHEN** operational-data runtime settings are loaded
- **THEN** the system reads collection enabled state, collection interval seconds, optional expiration seconds, optional retention seconds, and optional maximum storage MB from PostgreSQL runtime settings
- **THEN** the collection interval defaults to 60 seconds when no runtime setting has been saved
- **THEN** unset expiration means persisted local data does not expire
- **THEN** unset retention seconds means no time-based local data cleanup
- **THEN** unset maximum storage MB means no size-based local data cleanup
- **THEN** deployment config does not accept an `operational_data` section
- **THEN** removed fields such as `operational_data.enabled`, `operational_data.expiration`, and `operational_data.collect_interval_seconds` prevent startup instead of being ignored

#### Scenario: Scheduler samples upstream data before evaluating local rules
- **GIVEN** the service is running with operational data enabled
- **WHEN** a scheduler tick begins
- **THEN** the collection stage fetches Sub2API accounts from `Sub2APIClient.list_openai_accounts()`
- **THEN** the collection stage fetches Sub2API groups from `Sub2APIClient.list_groups(platform="openai")`
- **THEN** the collection stage fetches Sub2API users from `Sub2APIClient.list_users()`
- **THEN** the collection stage fetches per-user usage windows from `Sub2APIClient.get_user_usage(...)`
- **THEN** the collection stage fetches per-user API keys from `Sub2APIClient.get_user_api_keys(...)`
- **THEN** the collection stage fetches current-day and previous-day usage from `Sub2APIClient.get_usage_stats(...)`
- **THEN** the persistence stage stores raw source snapshots in PostgreSQL operational data snapshot tables
- **THEN** the persistence stage stores derived metric samples in PostgreSQL operational metric sample tables
- **THEN** the persistence stage stores per-source collection status in PostgreSQL source-status tables
- **THEN** the cleanup stage deletes local snapshot and metric sample records older than the configured retention window when retention seconds is set
- **THEN** the cleanup stage deletes oldest local snapshot and metric sample records when the operational-data payload size exceeds the configured maximum storage MB
- **THEN** size-based cleanup keeps the newest record for each source and each metric key
- **THEN** the evaluation stage evaluates due enabled rules using PostgreSQL notification config, local metric samples, and notification rule state
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
- **THEN** the response includes the effective collection interval as status
- **THEN** the response includes cleanup settings and current operational-data payload size
- **THEN** the response includes the last sampling start and finish timestamps
- **THEN** the response includes the last sampling error, sampled signal count, and per-source status details

#### Scenario: Pipeline stage data sources are explicit
- **GIVEN** a maintainer reads the operational data pipeline specification or status output
- **WHEN** they inspect how data moves through the pipeline
- **THEN** the collection stage identifies Sub2API accounts, groups, users, current-day usage, and previous-day usage as upstream data sources
- **THEN** the collection stage identifies per-user usage and per-user API keys as upstream data sources for credit control and automatic orchestration
- **THEN** the persistence stage identifies local PostgreSQL snapshot, metric sample, and source-status tables as storage destinations
- **THEN** the evaluation stage identifies local PostgreSQL notification config, metric sample, and rule-state tables as its only data sources

#### Scenario: Operator updates operational-data runtime settings without restart
- **GIVEN** an authenticated operator opens the web UI
- **WHEN** the operator enables or disables operational-data collection, changes collection interval, changes expiration, changes retention seconds, or changes maximum storage MB
- **THEN** the setting is saved to PostgreSQL through a global settings control backed by an authenticated runtime settings API
- **THEN** the next scheduler wait or tick uses the updated setting without restarting the service
- **THEN** scheduler status reports the persisted enabled state, effective collection interval, expiration, cleanup settings, storage size, and source freshness

### Requirement: Credit-control scheduling uses operational runtime data
The system SHALL run credit-control due-policy execution from the shared operational runtime model instead of a separate deployment tick configuration.

#### Scenario: Credit-control deployment configuration is minimal
- **GIVEN** the service starts
- **WHEN** settings are loaded
- **THEN** deployment config does not accept a `credit_control` section
- **THEN** removed fields such as `credit_control.enabled` and `credit_control.recharge_tick_seconds` prevent startup instead of being ignored

#### Scenario: Enabled credit-control scheduler uses internal cadence
- **GIVEN** persisted credit-control runtime settings have `enabled=true`
- **WHEN** the internal 60 second operational cadence elapses
- **THEN** the scheduler checks locally stored credit policies for due recharge work
- **THEN** the scheduler executes due policies through the existing credit-control service
- **THEN** there is no deployment config field for changing the tick interval

#### Scenario: Operator updates credit-control runtime settings without restart
- **GIVEN** an authenticated operator opens the web UI
- **WHEN** the operator enables or disables credit-control background execution
- **THEN** the setting is saved to PostgreSQL through an authenticated runtime settings API
- **THEN** the next scheduler tick uses the updated setting without restarting the service
