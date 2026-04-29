# group-rotation Specification

## ADDED Requirements

### Requirement: Discover and manage a dedicated rotation-target pool
The system SHALL support an operator-managed pool of dedicated rotation-target groups while preserving dedicated-group provisioning as the default behavior for deployments that do not enable rotation features.

#### Scenario: Dedicated mode remains unchanged by default
- **WHEN** the service starts without enabling managed-pool provisioning or automatic rotation
- **THEN** the service continues to provision dedicated groups for new users
- **THEN** automatic rotation remains disabled
- **THEN** no preselected rotation-pool membership is required

#### Scenario: Operator can discover current groups and inspect exclusivity
- **GIVEN** an authenticated operator calls `GET /rotation/pool/candidates`
- **WHEN** the system queries the upstream Sub2API admin groups API
- **THEN** the system returns current groups with their `is_exclusive` classification
- **THEN** the system returns whether each group is a subscription group when upstream metadata exposes it
- **THEN** the response distinguishes dedicated candidate groups from non-exclusive groups
- **THEN** the response marks which dedicated groups are already selected into the local rotation pool

#### Scenario: Operator can add an exclusive group into the rotation pool
- **GIVEN** an authenticated operator selects a group returned by `GET /rotation/pool/candidates`
- **AND** the selected group is exclusive
- **WHEN** the operator calls `POST /rotation/pool/groups`
- **THEN** the system persists that group as a dedicated rotation target in local storage
- **THEN** later managed-pool provisioning and rotation cycles may use that group as a target

#### Scenario: Non-exclusive groups cannot be added into the rotation pool
- **GIVEN** an authenticated operator selects a non-exclusive group
- **WHEN** the operator calls `POST /rotation/pool/groups`
- **THEN** the system rejects the request
- **THEN** the system does not persist that group into the local rotation pool

#### Scenario: Subscription groups cannot be added into the rotation pool
- **GIVEN** an authenticated operator selects an exclusive subscription group
- **WHEN** the operator calls `POST /rotation/pool/groups`
- **THEN** the system rejects the request because upstream `replace-group` supports only dedicated standard groups
- **THEN** the system does not persist that group into the local rotation pool

#### Scenario: Automatic rotation configuration is validated at startup
- **GIVEN** the service starts
- **WHEN** settings are loaded
- **THEN** the system validates configured usage bands, cooldown windows, and interval settings
- **THEN** the V1 usage window must be one of `5h`, `1d`, `7d`, or `30d`
- **THEN** invalid automatic rotation configuration prevents startup instead of failing later during rotation execution

### Requirement: Persist current assignment state and rotation audit
The system SHALL persist each managed user's current group assignment and SHALL persist the outcome of every manual or automatic rotation attempt in durable local storage.

#### Scenario: Assignment state survives restart
- **GIVEN** a user has been assigned or rotated into a dedicated rotation-target group
- **WHEN** the service restarts
- **THEN** the system can load the user's current group assignment, assignment mode, last rotation time, and last decision reason from SQLite

#### Scenario: Rotation execution writes an audit record
- **GIVEN** a manual or automatic rotation attempt finishes
- **WHEN** the sidecar persists the result
- **THEN** the system stores an audit record containing the user identity, source group, target group, trigger type, decision reason, execution status, usage snapshot, and timestamps

#### Scenario: Rotation-pool membership survives restart
- **GIVEN** an operator has added one or more exclusive groups into the local rotation pool
- **WHEN** the service restarts
- **THEN** the system can load the persisted rotation-pool membership without reselecting groups manually

### Requirement: Execute manual group rotation through authenticated API
The system SHALL expose an authenticated `POST /rotation/manual` API that moves a specified user to a target dedicated rotation group through Sub2API admin APIs and records the outcome locally.

#### Scenario: Manual rotation moves a user to a different dedicated rotation group
- **GIVEN** an authenticated operator submits a valid manual rotation request for a provisioned user
- **AND** the target group differs from the user's current group
- **AND** the target group belongs to the configured dedicated rotation pool
- **WHEN** the system executes the request
- **THEN** the system loads the user's current assignment state
- **THEN** the system calls the Sub2API admin API that replaces the user's effective group and migrates existing keys
- **THEN** the system does not rely only on updating the user's `allowed_groups`
- **THEN** the system updates the stored current assignment to the target group
- **THEN** the response includes the user identity, source group, target group, trigger type `manual`, and execution status

#### Scenario: Manual rotation requires authentication
- **GIVEN** the caller does not provide a valid admin session, access key header, or bearer token
- **WHEN** the caller invokes `POST /rotation/manual`
- **THEN** the system returns an authentication error
- **THEN** the system does not call any Sub2API admin API

### Requirement: Execute automatic usage-based rotation
The system SHALL evaluate eligible users against configured usage rules and SHALL rotate users to the appropriate dedicated rotation group automatically through the same execution path used by manual rotation.

#### Scenario: On-demand automatic rotation cycle reassigns eligible users
- **GIVEN** automatic rotation is enabled and the dedicated rotation pool contains at least one target group
- **WHEN** an authenticated operator calls `POST /rotation/auto/run`
- **THEN** the system retrieves candidate user usage data from Sub2API admin usage APIs
- **THEN** the system computes the desired target group for each eligible user from the configured usage policy
- **THEN** the system executes the same group-replacement workflow used by manual rotation for users whose desired group differs from their current group
- **THEN** the response includes moved, skipped, and failed results for the rotation cycle

#### Scenario: Automatic rotation uses a configurable V1 window
- **GIVEN** automatic rotation is enabled with a configured recent usage window
- **AND** the configured V1 window is one of `5h`, `1d`, `7d`, or `30d`
- **WHEN** the system evaluates user usage
- **THEN** the system applies the configured rolling window when computing the user's current usage band
- **THEN** the recorded audit data includes the effective window used for that decision

#### Scenario: Users without any created API key are scheduled after existing key holders
- **GIVEN** an automatic rotation cycle includes users who have never created any API key
- **WHEN** the system orders candidates for evaluation
- **THEN** users with existing API keys are evaluated before users with no created API key
- **THEN** users with no created API key are treated as new users for scheduling purposes

#### Scenario: Interval-based automatic rotation runs without operator input
- **GIVEN** automatic rotation is enabled with a configured execution interval
- **WHEN** the interval elapses while the sidecar process is running
- **THEN** the system runs the same evaluation and execution path used by `POST /rotation/auto/run`
- **THEN** the system records the cycle outcome in rotation audit storage

#### Scenario: Automatic rotation reports empty-pool failure safely
- **GIVEN** automatic rotation is enabled
- **AND** the local dedicated rotation pool is empty
- **WHEN** the system runs `POST /rotation/auto/run` or an interval-based cycle
- **THEN** the system returns or records a failure indicating that no rotation targets are available
- **THEN** the system does not call the Sub2API group replacement API

### Requirement: Prevent unsafe or conflicting rotations
The system SHALL skip or reject rotation when the requested or computed target group is unsafe to apply, and it SHALL avoid corrupting local state when downstream Sub2API operations fail.

#### Scenario: Rotation rejects any public group as a target
- **GIVEN** a manual or automatic rotation attempt resolves a public group id
- **WHEN** the system validates the target group
- **THEN** the system rejects or skips the rotation before calling the Sub2API group replacement API
- **THEN** the system records the result as an invalid target because only dedicated rotation groups are allowed

#### Scenario: Rotation rejects subscription groups as targets
- **GIVEN** a manual or automatic rotation attempt resolves a subscription group id
- **WHEN** the system validates the target group
- **THEN** the system rejects or skips the rotation before calling the Sub2API group replacement API
- **THEN** the system records the result as an invalid target because only dedicated standard groups are allowed

#### Scenario: Rotation is skipped when the target group matches the current group
- **GIVEN** a manual or automatic rotation attempt resolves the same group that the user is already assigned to
- **WHEN** the system evaluates the request
- **THEN** the system does not call the Sub2API group replacement API
- **THEN** the system records the result as a skipped no-op rotation

#### Scenario: Rotation is skipped while a provisioning flow is still pending
- **GIVEN** a user has a provisioning flow in `pending_oauth` state
- **WHEN** a manual or automatic rotation attempt targets that user
- **THEN** the system does not change the user's group assignment until the pending flow is resolved
- **THEN** the system records the skipped reason in the rotation audit

#### Scenario: Failed downstream rotation does not advance local assignment state
- **GIVEN** a rotation attempt reaches the Sub2API admin API
- **WHEN** the upstream group replacement call fails
- **THEN** the system records the failure in the rotation audit
- **THEN** the system leaves the stored current assignment unchanged
