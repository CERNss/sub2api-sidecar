## MODIFIED Requirements

### Requirement: Discover and manage dedicated landing and rotation pools
The system SHALL support separate operator-managed Landing and Rotation pools so new-user default placement is distinct from automatic usage-based rotation targets.

#### Scenario: OAuth provisioning uses Landing pool for managed-pool defaults
- **WHEN** the service starts with or without rotation features enabled
- **THEN** OAuth account provisioning does not assign new Sub2API users into the Rotation pool
- **THEN** managed-pool provisioning chooses its default group from the Landing pool
- **THEN** the Landing pool remains independent from automatic usage-based rotation targets

#### Scenario: Operator can discover current groups and inspect exclusivity
- **GIVEN** an authenticated operator calls `GET /rotation/pool/candidates`
- **WHEN** the system reads the latest operational-data group snapshot
- **THEN** the system returns current groups with their `is_exclusive` classification
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
- **THEN** deployment config does not accept an `auto_rotation` section
- **THEN** automatic rotation runtime policy fields are loaded from persisted runtime config or model defaults
- **THEN** removed deployment fields such as enabled, interval, cooldown, usage window, thresholds, and balance tolerances prevent startup instead of being ignored

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
- **WHEN** a dynamic orchestration cycle discovers an upstream user whose current direct group is in the Landing pool but not in the Rotation pool
- **THEN** the system treats that user as a new-user assignment candidate
- **THEN** preview reports the planned assignment without mutating upstream Sub2API state
- **THEN** real execution moves that user into the configured rotation target range through the same key-migrating `replace-group` API
- **THEN** the target group is selected from the lowest current usage load in the Rotation pool for the configured usage window
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
- **THEN** the system synchronizes existing users from the latest operational-data user snapshot whose current direct group can be inferred unambiguously
- **THEN** the system treats only users currently assigned to a selected rotation-pool group as automatic rotation candidates
- **THEN** users without an unambiguous current direct group are skipped instead of guessed from multi-group access data
- **THEN** the system computes current usage totals per selected Rotation pool group from local operational-data API-key and usage snapshots for the configured usage window
- **THEN** the system chooses the lowest-usage target group when a move is needed to reduce usage imbalance
- **THEN** the system skips users when moving them would not reduce usage imbalance
- **THEN** the system executes the same group-replacement workflow used by manual rotation for users whose desired group differs from their current group
- **THEN** the response includes moved, skipped, and failed results for the rotation cycle

#### Scenario: Operator previews dynamic allocation without mutating upstream
- **GIVEN** automatic rotation is enabled and the dedicated rotation pool contains at least one target group
- **WHEN** an authenticated operator calls `POST /rotation/auto/run` with `dry_run` set to `true`
- **THEN** the system evaluates the current locally collected user/group relationship and Rotation pool usage load
- **THEN** the response includes planned, skipped, and failed decisions
- **THEN** the system does not call the Sub2API group replacement API
- **THEN** the system does not write preview-only assignment or audit changes to local storage

#### Scenario: Interval-based automatic rotation runs without operator input
- **GIVEN** automatic rotation is enabled in persisted runtime dynamic orchestration config
- **WHEN** the internal 60 second operational cadence elapses while the sidecar process is running
- **THEN** the system runs the same evaluation and execution path used by `POST /rotation/auto/run`
- **THEN** the system records the cycle outcome in rotation audit storage
- **THEN** there is no deployment config field for changing the interval

#### Scenario: Automatic rotation reports empty-pool failure safely
- **GIVEN** automatic rotation is enabled
- **AND** the local dedicated rotation pool is empty
- **WHEN** the system runs `POST /rotation/auto/run` or an interval-based cycle
- **THEN** the system returns or records a failure indicating that no rotation targets are available
- **THEN** the system does not call the Sub2API group replacement API
