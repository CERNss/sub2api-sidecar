# orchestration-dashboard Specification

## Purpose
Define the authenticated operator dashboard for inspecting provisioning flows and orchestrating existing Sub2API users, API keys, and groups. The dashboard uses effective admin migration endpoints for existing resources, persists and redacts provisioning timeline data, and renders the React workspace used by operators.
## Requirements
### Requirement: Existing user/group orchestration API
The system SHALL expose authenticated APIs for discovering existing Sub2API users, groups, API keys, upstream accounts from the Sub2API admin accounts surface, transferring encoded admin API keys, and moving API key routing between groups.

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

#### Scenario: Operator migrates an existing user from one group to another
- **GIVEN** the operator has selected an existing user, an old group, and a target group
- **WHEN** the operator executes bulk user/group orchestration
- **THEN** the system calls upstream `POST /api/v1/admin/users/{user_id}/replace-group`
- **THEN** the request body includes `old_group_id` and `new_group_id`
- **THEN** the system does not use a user `allowed_groups` update as the effective orchestration operation
- **THEN** the system records the resulting local assignment and rotation event

#### Scenario: Operator moves a single API key to another group
- **GIVEN** the operator has selected an existing user, one API key, and a target group
- **WHEN** the operator executes single-key orchestration
- **THEN** the system calls upstream `PUT /api/v1/admin/api-keys/{key_id}`
- **THEN** the request body includes `group_id`
- **THEN** the system records a local rotation event for the key move

#### Scenario: Operator migrates a group into another group
- **GIVEN** the operator has selected a source group and a different target group
- **WHEN** the operator executes group-to-group orchestration
- **THEN** the system selects users whose direct group is the source group and users that own API keys routed to the source group
- **THEN** the system migrates each selected user's full API key set to the target group
- **THEN** existing users already in the target group remain in place and the run records merge mode when the target group was non-empty

#### Scenario: Subscription groups are not used for bulk replace-group
- **GIVEN** an upstream group is a subscription group
- **WHEN** the operator attempts bulk replace-group orchestration to that group
- **THEN** the system rejects the request with a client error
- **THEN** the operator may still use a supported single-key group update path where appropriate

#### Scenario: Operator transfers admin key ownership by encoded email
- **GIVEN** the operator has a valid admin session
- **AND** an admin user's API key name matches the `service:environment:object:version:email` pattern
- **WHEN** the operator previews or executes the key transfer
- **THEN** the system resolves `<email>` to exactly one existing Sub2API user by normalized email
- **THEN** the system does not create users and does not fuzzy-match email values
- **THEN** the system selects the first available group from the matched user's current or allowed groups
- **THEN** execution calls the Sub2API admin API to update the API key's `user_id`, `group_id`, and `quota`
- **THEN** execution preserves the API key string value
- **THEN** execution sets the API key quota limit to unlimited
- **THEN** the response reports moved, skipped, and failed keys with reasons

#### Scenario: Key transfer skips unsafe keys
- **GIVEN** the operator previews or executes the admin key transfer
- **WHEN** a key name does not contain exactly one valid target email, the target user is missing, or the target user has no available group
- **THEN** the system skips that key without making an upstream update call
- **THEN** the response includes a reason for the skipped key

### Requirement: Authenticated flow inspection API
The system SHALL expose authenticated read-only APIs for listing provisioning flows and retrieving a single flow with its orchestration timeline.

#### Scenario: Unauthenticated callers cannot inspect flows
- **GIVEN** a caller has no valid admin session, access-key header, or bearer token
- **WHEN** the caller requests `GET /provision/flows` or `GET /provision/flows/{flow_id}`
- **THEN** the system returns an authentication error
- **THEN** no flow details are returned

#### Scenario: Operator lists recent provisioning flows
- **GIVEN** the operator has a valid admin session
- **AND** multiple provisioning flows exist in the PostgreSQL store
- **WHEN** the operator requests `GET /provision/flows`
- **THEN** the system returns a success envelope containing flow summary items
- **THEN** each item includes `flow_id`, `email`, `group_id`, `status`, `account_name`, `oauth_account_id`, `error_message`, `created_at`, and `updated_at`
- **THEN** `email` is presented as the external OAuth account email, not as a Sub2API user email
- **THEN** `user_id` is optional and omitted or null for OAuth pre-provisioning flows that did not create a Sub2API user
- **THEN** `assignment_mode` is optional and omitted or null when no managed user assignment exists
- **THEN** the items are ordered by `updated_at` descending and then `created_at` descending
- **THEN** the response includes pagination metadata for `limit`, `offset`, and `total`

#### Scenario: Operator filters flow list
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator requests `GET /provision/flows` with `status`, `assignment_mode`, or `email` query parameters
- **THEN** the system returns only flows matching the provided filters
- **THEN** the `email` filter matches the external OAuth account email stored on the flow
- **THEN** invalid enum filter values are rejected with a client error response

#### Scenario: Operator retrieves flow detail and timeline
- **GIVEN** the operator has a valid admin session
- **AND** a provisioning flow exists for the requested `flow_id`
- **WHEN** the operator requests `GET /provision/flows/{flow_id}`
- **THEN** the system returns the full dashboard-safe flow detail
- **THEN** the response identifies the stored email as the external OAuth account email
- **THEN** the response does not require a Sub2API user id for the flow
- **THEN** the response includes persisted timeline events for the flow ordered by creation time ascending
- **THEN** each event includes `event_id`, `flow_id`, `event_type`, `status`, `message`, `details`, and `created_at`

#### Scenario: Missing flow detail returns not found
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator requests `GET /provision/flows/{flow_id}` for an unknown flow
- **THEN** the system returns a not-found error response

### Requirement: Persist provisioning timeline events
The system SHALL persist significant provisioning steps as timeline events tied to the flow id.

#### Scenario: Start flow records account and group orchestration progress
- **GIVEN** the operator starts a provisioning flow with a valid external OAuth account email
- **WHEN** the system creates the flow, creates the dedicated group, and generates the OAuth URL
- **THEN** the system persists timeline events for the start request, dedicated group creation, OAuth URL generation, and pending OAuth handoff
- **THEN** the system does not persist user creation or user group binding events for OAuth pre-provisioning flows
- **THEN** each event is tied to the created `flow_id`

#### Scenario: OAuth completion records orchestration progress
- **GIVEN** a provisioning flow is pending OAuth
- **WHEN** the operator submits a valid pasted callback URL
- **THEN** the system persists timeline events for callback parsing, OAuth code exchange, OpenAI OAuth account creation, account group binding, and completion
- **THEN** the flow detail timeline shows the completion path in chronological order

#### Scenario: Failures are recorded on the flow timeline
- **GIVEN** a provisioning step fails after a flow id has been created or loaded
- **WHEN** the system marks the flow as failed or surfaces the provisioning error
- **THEN** the system persists a failed timeline event with a human-readable message
- **THEN** the flow record retains the failure status or error message that the dashboard can display

### Requirement: Dashboard responses redact sensitive values
The system SHALL redact secrets and provider tokens from orchestration dashboard API responses.

#### Scenario: OAuth token fields are not exposed
- **GIVEN** a completed provisioning flow stores an OAuth exchange payload containing `access_token`, `refresh_token`, or similarly sensitive fields
- **WHEN** the operator requests the flow detail API
- **THEN** the system does not return raw secret values for those fields
- **THEN** the system either omits the sensitive fields or replaces their values with a redaction marker

#### Scenario: Dashboard never exposes service credentials
- **GIVEN** the service is configured with Sub2API admin credentials or an ephemeral admin access key
- **WHEN** the operator uses dashboard APIs or the dashboard UI
- **THEN** the response payload and rendered UI do not expose the Sub2API admin API key, default user password, or browser session access key

### Requirement: React dashboard renders orchestration state
The React UI SHALL provide an authenticated orchestration workspace for moving existing users or keys between groups, browsing provisioning flows, managing balances, transferring encoded admin API keys, and configuring webhook alert routing for operational signals.

#### Scenario: Authenticated operator opens the dashboard
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator opens the React UI
- **THEN** the default view is existing user/group orchestration
- **THEN** the UI provides access to the existing OAuth account provisioning form and a flow dashboard view
- **THEN** the UI provides a top-level notification settings view beside orchestration and OAuth provisioning
- **THEN** the UI provides `余额管理` as a top-level authenticated operator tab
- **THEN** the orchestration view displays a draggable relationship graph ordered left-to-right as all API keys, all users, all groups, and upstream accounts
- **THEN** group nodes display current group capacity by summing bound account concurrency and current concurrency values in addition to their user, key, and account relationship counts
- **THEN** capacity displays use green styling below 80% usage, yellow styling at or above 80%, and red styling at or above 100%
- **THEN** groups are connected to upstream account nodes when account group binding data is available
- **THEN** upstream account nodes display whether the account is unavailable and show current account capacity with compact 5-hour and 7-day usage percentages
- **THEN** selecting a user or key in the graph updates the operator selection controls
- **THEN** the dashboard displays flow summary rows with status, external OAuth account email, group id, account id, and update time
- **THEN** the dashboard does not present OAuth provisioning flows as Sub2API user creation records

#### Scenario: External Sub2API launch exchanges token before rendering
- **GIVEN** the sidecar is opened as a standalone page with a `token` query parameter
- **WHEN** the React app starts
- **THEN** it calls `POST /auth/sub2api-login`
- **THEN** it removes the `token` query parameter from the URL after success or failure
- **THEN** on success it continues to the normal operator workspace using the sidecar session cookie
- **THEN** on failure it navigates to the normal sidecar login page

#### Scenario: Operator executes existing user/group orchestration
- **GIVEN** the operator is using the existing user/group orchestration view
- **WHEN** the operator selects bulk replace-group mode and executes
- **THEN** the UI submits the source group and target group to the authenticated replace-group orchestration API
- **THEN** the UI treats source-group routed API keys as part of the group migration scope and shows merge mode when the target group already has users
- **WHEN** the operator selects single-key mode and executes
- **THEN** the UI submits the selected API key and target group to the authenticated API key group update API

#### Scenario: Operator filters and refreshes dashboard data
- **GIVEN** the operator is viewing the flow dashboard
- **WHEN** the operator changes status, assignment mode, or email filters and refreshes
- **THEN** the UI reloads data from the authenticated flow list API using those filters
- **THEN** the UI labels the email filter as external OAuth account email
- **THEN** the UI displays empty, loading, and error states without hiding the rest of the application shell

#### Scenario: Operator inspects a flow detail panel
- **GIVEN** the dashboard shows at least one flow
- **WHEN** the operator selects a flow
- **THEN** the UI shows the flow detail panel
- **THEN** the detail panel includes the OAuth handoff URL when present, the callback redirect URI, failure message when present, and the timeline events for that flow
- **THEN** pending flows include enough state context for the operator to construct or verify a pasted callback URL
- **THEN** the detail panel does not require or invent a Sub2API user id for account/group scoped OAuth flows

#### Scenario: Dashboard remains read-only
- **GIVEN** the operator is using the dashboard view
- **WHEN** the operator inspects historical or active flows
- **THEN** the dashboard does not mutate flow records, retry failed flows, or complete OAuth unless the operator uses the existing paste-back completion form

#### Scenario: Operator previews and runs admin key transfer
- **GIVEN** the operator is using the key transfer tab
- **WHEN** the operator previews admin key transfer
- **THEN** the UI shows moved, skipped, and failed counts and per-key reasons returned by the authenticated API
- **WHEN** the operator executes admin key transfer
- **THEN** the UI submits to the authenticated execution API and refreshes orchestration data after completion

### Requirement: Persist webhook alert configuration server-side
The system SHALL expose authenticated APIs that read and write the webhook alert center configuration document to durable local storage. The configuration document contains only `webhooks` and `rules`; any unsupported top-level, rule, or webhook field is rejected on write and is not part of the persisted runtime contract.

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

#### Scenario: Unauthenticated callers cannot read or write configuration
- **GIVEN** a caller has no valid admin session, access-key header, or bearer token
- **WHEN** the caller calls `GET /notifications/config` or `PUT /notifications/config`
- **THEN** the system returns an authentication error
- **THEN** the system does not return or modify configuration

### Requirement: Deliver outbound webhook payloads through provider adapters
The system SHALL deliver webhook payloads to operator-configured receivers using provider-specific payload formatting and signing.

#### Scenario: Generic provider posts a JSON payload with optional HMAC signature
- **GIVEN** a receiver has provider `generic`
- **WHEN** the delivery worker sends a message to that receiver
- **THEN** the request is `POST` with JSON content type and the message rendered as a JSON object
- **THEN** when the receiver has a non-empty secret, the request includes an HMAC-SHA256 signature header derived from the body and the secret

#### Scenario: Feishu and dingtalk receivers use their documented signing schemes
- **GIVEN** a feishu receiver with a non-empty secret
- **WHEN** the delivery worker sends a message
- **THEN** the request body includes a `timestamp` and `sign` field computed from `timestamp + "\n" + secret` using HMAC-SHA256 base64 per Feishu's custom bot documentation
- **GIVEN** a dingtalk receiver with a non-empty secret
- **WHEN** the delivery worker sends a message
- **THEN** the request URL includes `timestamp` and `sign` query parameters computed from `timestamp + "\n" + secret` using HMAC-SHA256 base64 per DingTalk's custom bot documentation

#### Scenario: Slack, discord, and wecom receivers use their native payload shapes
- **GIVEN** a slack receiver
- **WHEN** the delivery worker sends a message
- **THEN** the request body uses Slack's `{"text": ...}` shape
- **GIVEN** a discord receiver
- **WHEN** the delivery worker sends a message
- **THEN** the request body uses Discord's `{"content": ...}` shape
- **GIVEN** a wecom receiver
- **WHEN** the delivery worker sends a message
- **THEN** the request body uses WeCom's `{"msgtype": "text", "text": {"content": ...}}` shape

#### Scenario: Disabled receivers and empty URLs never receive deliveries
- **GIVEN** a receiver is disabled or has an empty URL
- **WHEN** the delivery worker is asked to send a message to that receiver
- **THEN** the system records a skipped delivery audit row
- **THEN** the system does not perform any outbound HTTP request

#### Scenario: Transient delivery failures are retried with backoff
- **GIVEN** the receiver returns a transient HTTP failure
- **WHEN** the delivery worker handles the response
- **THEN** the system retries the request a bounded number of times with exponential backoff
- **THEN** the audit log records each attempt with its `attempt_index`, status, and error
- **THEN** the final outcome is reported as the delivery result

### Requirement: Inspect delivery audit trail and exercise test deliveries
The system SHALL let operators trigger a real test delivery for a saved rule and inspect recent delivery attempts.

#### Scenario: Operator runs a real test delivery for a saved rule
- **GIVEN** an authenticated operator
- **AND** a saved rule whose `targetWebhookIds` reference at least one enabled receiver with a non-empty URL
- **WHEN** the operator calls `POST /notifications/test` with that rule id
- **THEN** the system loads the saved configuration
- **THEN** the system builds a test message labelled with the rule name, severity, and signal key
- **THEN** the system delivers the test message through every targeted enabled receiver using the provider adapter
- **THEN** the response includes the per-receiver outcome with status, attempt count, and error message when applicable

#### Scenario: Test request is rejected when no receiver can be delivered to
- **GIVEN** the requested rule has no enabled receivers, all referenced receivers have empty URLs, or the rule id does not exist
- **WHEN** the operator calls `POST /notifications/test`
- **THEN** the system returns a client error response with a human-readable reason
- **THEN** the system does not perform any outbound HTTP request

#### Scenario: Operator inspects recent deliveries
- **GIVEN** at least one delivery attempt has been audited
- **WHEN** the operator calls `GET /notifications/deliveries`
- **THEN** the system returns recent delivery audit rows ordered by creation time descending
- **THEN** each row includes provider, receiver id, rule id, severity, status, error, attempt index, trigger type, and timestamp
- **THEN** the response respects an optional `limit` query parameter

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

#### Scenario: Operator monitors sustained capacity saturation
- **GIVEN** upstream account capacity fields expose current usage and total capacity
- **WHEN** the operator creates an account or group capacity saturation alert
- **THEN** the rule threshold represents how many accounts or groups are currently full
- **THEN** the rule sustained-for minutes represents how long that full-count condition must remain true before firing
- **THEN** account saturation counts accounts whose current capacity is greater than or equal to total capacity
- **THEN** group saturation counts groups whose grouped account current capacity is greater than or equal to grouped account total capacity

#### Scenario: Notification configuration persists locally
- **GIVEN** the operator edits webhook receivers or rules
- **WHEN** the operator saves the settings
- **THEN** the current configuration is persisted in browser local storage
- **THEN** re-opening the dashboard restores the saved configuration when it is valid

#### Scenario: Empty rule list shows onboarding state
- **GIVEN** the operator opens the notification settings view
- **AND** no rules have been saved
- **WHEN** the UI renders the rule editor area
- **THEN** the UI shows an empty-state card prompting the operator to add their first rule
- **THEN** the UI does not pre-populate any rules
- **THEN** the UI still renders the placeholder webhook so the rule editor has a deliverable target

### Requirement: Periodic rule evaluation
The system SHALL periodically evaluate enabled notification rules at the configured `readIntervalMinutes` cadence. The system SHALL use a neutral operational data pipeline that first collects upstream data on the persisted operational-data runtime interval, then persists normalized local snapshots and metric samples, then evaluates rules from local samples. Rule evaluation SHALL read the latest local metric sample for each rule instead of calling upstream Sub2API collectors per rule.

#### Scenario: Operational data runtime settings control collection
- **GIVEN** the service starts
- **WHEN** operational data runtime settings are loaded
- **THEN** the system reads operational data enabled state, collection interval, optional expiration, retention window, and storage-size guard from PostgreSQL runtime settings
- **THEN** deployment config cannot set operational data runtime switches
- **THEN** an unset operational data expiration means persisted local data does not expire for consumer reads

#### Scenario: Scheduler samples upstream data before evaluating local rules
- **GIVEN** the service is running with operational data enabled
- **WHEN** a scheduler tick begins
- **THEN** the collection stage fetches Sub2API accounts from `Sub2APIClient.list_openai_accounts()`
- **THEN** the collection stage fetches Sub2API groups from `Sub2APIClient.list_groups(platform="openai")`
- **THEN** the collection stage fetches Sub2API users from `Sub2APIClient.list_users()`
- **THEN** the collection stage fetches per-user API keys and user usage where needed by consumers
- **THEN** the collection stage fetches current-day and previous-day usage from `Sub2APIClient.list_usage_logs(...)`
- **THEN** the collection stage fetches dashboard group usage stats where needed by group usage balancing
- **THEN** the persistence stage stores raw source snapshots in PostgreSQL operational data snapshot tables
- **THEN** the persistence stage stores derived metric samples in PostgreSQL operational metric sample tables
- **THEN** the persistence stage stores per-source collection status in PostgreSQL source-status tables
- **THEN** the evaluation stage evaluates due enabled rules using PostgreSQL notification config, local metric samples, and notification rule state
- **THEN** the system persists the updated rule state regardless of decision

#### Scenario: Account invalid whitelist suppresses known manual closures
- **GIVEN** deployment config lists account ids, names, or emails under `notifications.account_invalid_whitelist`
- **WHEN** operational data derives the `account_invalid` metric from upstream accounts
- **THEN** accounts matching the whitelist are excluded from the `account_invalid` count and snapshot
- **THEN** the whitelist does not suppress other account signals such as rate limit, reauth, or capacity alerts

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
- **THEN** the response includes the configured collection interval, expiration, cleanup guards, and current operational-data storage footprint
- **THEN** the response includes the last sampling start and finish timestamps
- **THEN** the response includes the last sampling error, sampled signal count, and per-source status details

#### Scenario: Pipeline stage data sources are explicit
- **GIVEN** a maintainer reads the operational data pipeline specification or status output
- **WHEN** they inspect how data moves through the pipeline
- **THEN** the collection stage identifies Sub2API accounts, groups, users, user API keys, user usage, global usage logs, and group usage stats as upstream data sources
- **THEN** the persistence stage identifies local PostgreSQL snapshot, metric sample, and source-status tables as storage destinations
- **THEN** the evaluation stage identifies local PostgreSQL notification config, metric sample, and rule-state tables as its only data sources

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
- **THEN** the system does not perform any collector or delivery work
