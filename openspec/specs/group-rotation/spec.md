# group-rotation Specification

## Purpose
Define manual and automatic rotation of existing Sub2API users between dedicated Landing and Rotation groups, including pool discovery, durable assignment and audit state, dynamic configuration, the per-platform scoping of every binding, and the safety guards that prevent unsafe or no-op rotations.

## Requirements

### Requirement: Scope every user/group binding to a single upstream platform
The system SHALL treat the upstream group's `platform` field as the only source of truth for which platform a group, key, or binding serves, and SHALL enforce that a user holds at most one dedicated group — and the keys routed to it — per platform. The platform value is an opaque upstream string (today `openai` and `grok`), never an enum owned by the sidecar and never parsed out of a group name.

#### Scenario: Platform is read from upstream group metadata
- **GIVEN** the system must decide which platform a group, an API key, or a user binding belongs to
- **WHEN** it resolves that platform
- **THEN** it reads the `platform` field of the upstream group from the group snapshot index
- **THEN** it does not infer the platform from the group name, the group name suffix, or the configured provisioning default
- **THEN** the group snapshot index is loaded unfiltered so groups of every platform are visible
- **THEN** a group whose upstream payload carries no platform is logged and falls back to the legacy single-platform path

#### Scenario: A user's direct group is derived per platform
- **GIVEN** an upstream user exposes only its authorized groups (`allowed_groups` / `group_ids`)
- **WHEN** the system derives that user's current direct group
- **THEN** it buckets the authorized groups by the platform of each group
- **THEN** a bucket holding exactly one group is that user's direct group on that platform
- **THEN** a bucket holding more than one group violates the one-group-per-platform rule, is logged as a warning, and is treated as "no direct group" instead of being guessed
- **THEN** groups whose platform is unknown do not produce a direct group

#### Scenario: Local assignment state is keyed by user and platform
- **GIVEN** a user holds a dedicated group on more than one platform
- **WHEN** the system persists assignment state
- **THEN** the local store holds one row per `(user, platform)` under a composite primary key
- **THEN** each stored assignment carries the platform it belongs to
- **THEN** the bindings of one platform rotate independently from the bindings of another platform
- **THEN** a legacy assignment table keyed by user alone is dropped and rebuilt on the composite key at startup, and its rows are rebuilt from upstream on the next sync
- **THEN** the rotation pool table gains its platform column in place, preserving existing pool rows

#### Scenario: Rotation pool rows remember their platform
- **GIVEN** an operator adds an exclusive group into the Landing or Rotation pool
- **WHEN** the system persists the pool membership
- **THEN** the persisted row stores the upstream group's platform
- **THEN** pool candidate and pool listing responses expose the platform of each group

#### Scenario: Cross-platform group changes are rejected
- **GIVEN** an operator submits a manual replace, an existing-user orchestration, or a group-to-group migration
- **WHEN** the resolved source and target groups serve different platforms
- **THEN** the system rejects the request with a validation error naming both platforms
- **THEN** the system does not call any Sub2API group replacement API

#### Scenario: First assignment on a platform guards the one-group rule
- **GIVEN** an existing-user orchestration request without a source group
- **AND** the target group's platform is known
- **WHEN** the selected user already holds one or more groups on that platform
- **THEN** the system rejects the request and asks the operator to pick the group to replace
- **WHEN** the selected user holds no group on that platform
- **THEN** the system grants the target group as an additional authorization and synchronizes the user's resources into it

#### Scenario: Key moves never cross a platform
- **GIVEN** the system synchronizes a user's API keys into a target group
- **WHEN** a key currently sits in a group of another platform
- **THEN** the system leaves that key where it is and logs the skip
- **THEN** keys that sit in no group at all may follow the user into the target group

#### Scenario: One unbindable account does not sink a rotation
- **GIVEN** the system binds the accounts behind a user's keys to the target group
- **WHEN** one account cannot be resolved or updated upstream
- **THEN** the system logs a warning and skips that account
- **THEN** the remaining accounts are still bound and the rotation continues

### Requirement: Discover and manage dedicated landing and rotation pools
The system SHALL support separate operator-managed Landing and Rotation pools so new-user default placement is distinct from automatic usage-based rotation targets.

#### Scenario: OAuth provisioning uses Landing pool for managed-pool defaults
- **WHEN** the service starts with or without rotation features enabled
- **THEN** OAuth account provisioning does not assign new Sub2API users into the Rotation pool
- **THEN** managed-pool provisioning chooses its default group from the Landing pool
- **THEN** managed-pool provisioning only considers Landing pool rows whose platform matches the platform being provisioned
- **THEN** the Landing pool remains independent from automatic usage-based rotation targets

#### Scenario: Operator can discover current groups and inspect exclusivity
- **GIVEN** an authenticated operator calls `GET /rotation/pool/candidates`
- **WHEN** the system queries the upstream Sub2API admin groups API
- **THEN** the system returns current groups with their `is_exclusive` classification
- **THEN** the system returns the `platform` of each group so the operator can tell which platform a candidate serves
- **THEN** the system returns whether each group is a subscription group when upstream metadata exposes it
- **THEN** the response distinguishes dedicated candidate groups from non-exclusive groups
- **THEN** the response marks which dedicated groups are selected into the Landing pool
- **THEN** the response marks which dedicated groups are selected into the Rotation pool

#### Scenario: Operator can add an exclusive group into the dynamic rotation target pool
- **GIVEN** an authenticated operator selects a group returned by `GET /rotation/pool/candidates`
- **AND** the selected group is exclusive
- **WHEN** the operator calls `POST /rotation/pool/groups`
- **THEN** the system persists that group as a dedicated rotation target in local storage
- **THEN** later automatic dynamic rotation cycles may use that group as a target for existing users

#### Scenario: Operator can add an exclusive group into the Landing pool
- **GIVEN** an authenticated operator selects a group returned by `GET /rotation/pool/candidates`
- **AND** the selected group is exclusive
- **WHEN** the operator calls `POST /rotation/pool/groups` with `pool_kind` set to `landing`
- **THEN** the system persists that group as a Landing pool entry
- **THEN** managed-pool provisioning and new-user automatic assignment may use that pool as the entry range

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

#### Scenario: Automatic rotation deployment configuration is minimal
- **GIVEN** the service starts
- **WHEN** settings are loaded
- **THEN** deployment config does not contain automatic rotation runtime settings
- **THEN** automatic rotation enablement, cooldown, usage window, and tunables are governed by the authenticated runtime API/UI and local PostgreSQL state
- **THEN** invalid automatic rotation runtime values are rejected when the operator saves them instead of being read from deployment config

### Requirement: Persist current assignment state and rotation audit
The system SHALL persist each managed user's current group assignment per platform and SHALL persist the outcome of every manual or automatic rotation attempt in durable local storage.

#### Scenario: Assignment state survives restart
- **GIVEN** a user has been assigned or rotated into a dedicated rotation-target group
- **WHEN** the service restarts
- **THEN** the system can load the user's current group assignment, assignment mode, last rotation time, and last decision reason from PostgreSQL
- **THEN** the assignment is loaded for the `(user, platform)` pair, so a user with dedicated groups on several platforms keeps one stored assignment per platform

#### Scenario: Rotation execution writes an audit record
- **GIVEN** a manual or automatic rotation attempt finishes
- **WHEN** the sidecar persists the result
- **THEN** the system stores an audit record containing the user identity, source group, target group, trigger type, decision reason, execution status, usage snapshot, and timestamps

#### Scenario: Pool membership survives restart
- **GIVEN** an operator has added one or more exclusive groups into the local Landing or Rotation pool
- **WHEN** the service restarts
- **THEN** the system can load each persisted pool membership without reselecting groups manually

### Requirement: Execute manual group rotation through authenticated API
The system SHALL expose an authenticated `POST /rotation/manual` API that moves a specified user to a target dedicated rotation group through Sub2API admin APIs and records the outcome locally.

#### Scenario: Manual rotation moves an existing user to a different dedicated rotation group
- **GIVEN** an authenticated operator submits a valid manual rotation request for an existing upstream user
- **AND** the target group differs from the user's current group
- **AND** the target group is an upstream dedicated standard group supported by `replace-group`
- **WHEN** the system executes the request
- **THEN** the system loads the user's stored assignment for the target group's platform
- **THEN** the system calls the Sub2API admin API that replaces the user's effective group and migrates existing keys
- **THEN** the system does not rely only on updating the user's `allowed_groups`
- **THEN** the system updates the stored current assignment to the target group
- **THEN** the response includes the user identity, source group, target group, trigger type `manual`, and execution status

#### Scenario: Manual rotation is independent from dynamic rotation pool configuration
- **GIVEN** an authenticated operator submits a manual rotation request
- **AND** the target group is an upstream dedicated standard group supported by `replace-group`
- **AND** the target group is not selected into the dynamic rotation target pool
- **WHEN** the system executes the request
- **THEN** the system allows the manual request
- **THEN** the dynamic Landing and Rotation pools do not gate manual orchestration

#### Scenario: Manual rotation requires authentication
- **GIVEN** the caller does not provide a valid admin session, access key header, or bearer token
- **WHEN** the caller invokes `POST /rotation/manual`
- **THEN** the system returns an authentication error
- **THEN** the system does not call any Sub2API admin API

### Requirement: Execute automatic usage-balanced rotation
The system SHALL evaluate eligible users against current Rotation pool usage load and SHALL rotate users so recent usage is distributed as evenly as possible across dedicated rotation groups.

#### Scenario: Operator configures dynamic orchestration separately from manual orchestration
- **GIVEN** an authenticated operator opens the React UI
- **WHEN** the operator navigates to the dynamic orchestration view
- **THEN** the UI displays dynamic orchestration on its own route and top-level tab
- **THEN** the manual existing-user/key orchestration controls remain separate from dynamic execution controls
- **THEN** the dynamic view lets the operator manage the Landing pool separately from the Rotation pool
- **THEN** the dynamic view lets the operator enable or disable real execution and set cooldown minutes
- **THEN** the dynamic view lets the operator choose the usage window used for balancing
- **THEN** the dynamic view lets the operator enable or disable automatic assignment for newly detected users
- **THEN** saving dynamic configuration persists it without requiring a service restart

#### Scenario: Newly detected users are auto-assigned only from Landing pool
- **GIVEN** automatic new-user assignment is enabled
- **AND** the operator has configured a Landing pool
- **AND** the dedicated rotation pool contains at least one target group
- **WHEN** a dynamic orchestration cycle discovers an upstream user whose current direct group on a platform is in the Landing pool but not in the Rotation pool
- **THEN** the system treats that `(user, platform)` binding as a new-user assignment candidate
- **THEN** preview reports the planned assignment without mutating upstream Sub2API state
- **THEN** real execution moves that user into the configured rotation target range through the same key-migrating `replace-group` API
- **THEN** the target group is selected from the lowest current usage load among the Rotation pool groups on that binding's platform for the configured usage window
- **THEN** a binding whose platform has no Rotation pool group is skipped with a reason naming that platform
- **THEN** users outside the Landing pool are skipped and not assigned
- **THEN** an empty Landing pool does not implicitly mean all groups

#### Scenario: Operator configures the usage window for dynamic balancing
- **GIVEN** an authenticated operator opens the dynamic orchestration view
- **WHEN** the operator selects `5h`, `1d`, `7d`, or `30d` as the usage window and saves configuration
- **THEN** subsequent preview and execution cycles use that saved window to compute user usage
- **THEN** invalid usage-window values are rejected by the authenticated API

#### Scenario: Dynamic configuration gates real execution but not preview
- **GIVEN** the dynamic configuration is saved with real execution disabled
- **WHEN** an authenticated operator previews dynamic allocation
- **THEN** the system evaluates the current policy without mutating upstream Sub2API state
- **WHEN** an authenticated operator attempts real dynamic execution
- **THEN** the system rejects the run before calling Sub2API group replacement APIs

#### Scenario: On-demand automatic rotation cycle reassigns eligible users
- **GIVEN** automatic rotation is enabled and the dedicated rotation pool contains at least one target group
- **WHEN** an authenticated operator calls `POST /rotation/auto/run`
- **THEN** the system synchronizes existing upstream users whose current direct group on a platform can be inferred unambiguously, emitting one assignment row per `(user, platform)`
- **THEN** the system treats only bindings currently assigned to a selected rotation-pool group as automatic rotation candidates
- **THEN** bindings without an unambiguous current direct group are skipped instead of guessed from multi-group access data
- **THEN** the sync summary counts bindings skipped because a platform bucket held more than one group and bindings skipped because the group's platform is unknown
- **THEN** the system buckets the Rotation pool by platform and selects targets only inside the bucket of the candidate's own platform
- **THEN** the system computes current usage totals per selected Rotation pool group for the configured usage window using persisted group usage records when all selected groups have records
- **THEN** the system falls back to summing candidate user usage when group usage records are missing
- **THEN** the system chooses the lowest-usage target group in that platform bucket when a move is needed to reduce usage imbalance
- **THEN** the system skips users when moving them would not reduce usage imbalance
- **THEN** the system executes the same group-replacement workflow used by manual rotation for users whose desired group differs from their current group
- **THEN** the response includes moved, skipped, and failed results for the rotation cycle

#### Scenario: Planner uses persisted group usage records
- **GIVEN** selected rotation pool groups have persisted group usage records
- **AND** one selected group has higher configured-window usage than another
- **WHEN** automatic rotation runs
- **THEN** the planner uses persisted group usage loads as the source of truth
- **THEN** the planner selects a move only if simulating the candidate user move reduces the group load spread by at least the configured improvement delta

#### Scenario: Planner records group balancing metadata
- **GIVEN** automatic rotation plans or executes a group-balancing move
- **WHEN** the run record is saved
- **THEN** the result metadata includes group load source, load summaries before the move, source load, target load, and decision type

#### Scenario: Planner falls back when group usage is missing
- **GIVEN** selected rotation pool groups do not all have persisted group usage records
- **WHEN** automatic rotation runs
- **THEN** the planner falls back to summing candidate user usage for missing groups
- **THEN** dry-run, cooldown, rollback, and pool eligibility behavior remain unchanged

#### Scenario: Operator previews dynamic allocation without mutating upstream
- **GIVEN** automatic rotation is enabled and the dedicated rotation pool contains at least one target group
- **WHEN** an authenticated operator calls `POST /rotation/auto/run` with `dry_run` set to `true`
- **THEN** the system evaluates the current upstream user/group relationship and Rotation pool usage load
- **THEN** the response includes planned, skipped, and failed decisions
- **THEN** the system does not call the Sub2API group replacement API
- **THEN** the system does not write preview-only assignment or audit changes to local storage

#### Scenario: Cadence-based automatic rotation runs without operator input
- **GIVEN** automatic rotation is enabled
- **WHEN** the internal operational cadence elapses while the sidecar process is running
- **THEN** the system runs the same evaluation and execution path used by `POST /rotation/auto/run`
- **THEN** the system records the cycle outcome in rotation audit storage

#### Scenario: Automatic rotation reports empty-pool failure safely
- **GIVEN** automatic rotation is enabled
- **AND** the local dedicated rotation pool is empty
- **WHEN** the system runs `POST /rotation/auto/run` or a cadence-based cycle
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

#### Scenario: Automatic execution refuses a target on another platform
- **GIVEN** an automatic rotation candidate whose stored binding is on one platform
- **WHEN** the resolved target pool group serves a different platform
- **THEN** the system records the attempt as failed with a reason naming both platforms
- **THEN** the system does not call the Sub2API group replacement API

#### Scenario: Rotation is skipped when the target group matches the current group
- **GIVEN** a manual or automatic rotation attempt resolves the same group that the user is already assigned to
- **WHEN** the system evaluates the request
- **THEN** the system does not call the Sub2API group replacement API
- **THEN** the system records the result as a skipped no-op rotation

#### Scenario: Rotation does not infer candidates from OAuth provisioning flows
- **GIVEN** an OAuth account provisioning flow exists in `pending_oauth` state
- **WHEN** a manual or automatic rotation cycle selects candidates
- **THEN** the system does not treat the provisioning flow email as a Sub2API user identity
- **THEN** the system rotates only existing upstream users selected from user discovery, explicit operator input, or persisted assignment state

#### Scenario: Failed downstream rotation does not advance local assignment state
- **GIVEN** a rotation attempt reaches the Sub2API admin API
- **WHEN** the upstream group replacement call fails
- **THEN** the system records the failure in the rotation audit
- **THEN** the system leaves the stored current assignment unchanged

### Requirement: Tunable balance dead band and per-move improvement threshold
The system SHALL expose two non-negative tunables, `imbalance_epsilon` and `improvement_delta`, that gate automatic usage-balanced rotation so that already-balanced pools and marginally-improving moves do not trigger churn.

#### Scenario: Dead band skips the rebalance loop when load spread is within epsilon
- **GIVEN** automatic rotation is enabled and the configured `imbalance_epsilon` is greater than zero
- **WHEN** a dynamic orchestration cycle computes per-group usage loads for the Rotation pool groups on a candidate's platform
- **AND** the spread between the highest and lowest group load inside that platform bucket is less than or equal to `imbalance_epsilon`
- **THEN** the system does not rebalance the candidates of that platform
- **THEN** candidates on another platform whose bucket is still imbalanced are evaluated normally
- **THEN** the system reports the cycle as dead-band skipped on the run record and response
- **THEN** the system still performs new-user assignment from the Landing pool when configured

#### Scenario: Improvement delta blocks marginal per-user moves
- **GIVEN** automatic rotation is enabled and the configured `improvement_delta` is greater than zero
- **WHEN** the system evaluates moving a candidate from its current group to the lowest-loaded Rotation pool group on the candidate's own platform
- **AND** the post-move imbalance gap is not strictly less than the pre-move gap minus `improvement_delta`
- **THEN** the system keeps the user in the current group instead of moving
- **THEN** the system records the result as a skipped no-improvement rotation

#### Scenario: Default tunables preserve prior behavior
- **GIVEN** neither `imbalance_epsilon` nor `improvement_delta` is configured
- **WHEN** automatic rotation runs
- **THEN** the dead band does not skip any candidate iteration
- **THEN** the per-move gate uses strict imbalance reduction without any delta
- **THEN** the response and run record indicate that the dead band was not triggered

#### Scenario: Operator configures tunables from the dynamic orchestration UI and API
- **GIVEN** an authenticated operator opens the dynamic orchestration view
- **WHEN** the operator sets `imbalance_epsilon` and `improvement_delta` to non-negative numeric values and saves configuration
- **THEN** the authenticated configuration API persists both fields without requiring a service restart
- **THEN** subsequent preview and execution cycles apply the saved values
- **THEN** invalid or negative values are rejected by the authenticated API
