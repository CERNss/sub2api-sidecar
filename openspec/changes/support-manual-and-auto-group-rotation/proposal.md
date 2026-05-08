## Why

The sidecar needs to orchestrate existing Sub2API users, API keys, and groups after they already exist. Operators need to move those existing users between a small fixed pool of pre-created dedicated groups that each represent a controlled rotation target.

Operators need a safe way to discover existing groups, select dedicated groups into a rotation pool, and move existing users between those dedicated rotation groups manually or automatically without coupling this feature to OAuth account provisioning. Public groups must never appear as rotation targets.

## What Changes

- Add authenticated sidecar APIs to discover upstream groups, classify exclusive vs non-exclusive groups, and add or remove dedicated groups from the local rotation pool.
- Add authenticated sidecar APIs for manual existing-user group rotation and for running automatic usage-based rotation over existing users.
- Persist rotation-pool membership, assignment state, and rotation audit history in SQLite so decisions survive restarts and are inspectable.
- Extend the Sub2API admin client to use confirmed user-group replacement and usage APIs needed for rotation.
- Keep OAuth account provisioning out of this rotation-pool feature; provisioning remains account/group scoped and does not create or assign Sub2API users.
- Let automatic rotation evaluate usage over a configurable V1 window chosen from `5h`, `1d`, `7d`, or `30d`, and evaluate existing users without any created API key after existing key holders.
- Add two stability tunables to dynamic orchestration: `imbalance_epsilon` to skip the rebalance loop when the pool is already within tolerance, and `improvement_delta` to block per-user moves whose imbalance reduction is below the configured floor.

## Capabilities

### New Capabilities
- `group-rotation`: Manage rotation-pool discovery and selection, dedicated rotation-target assignment, manual reassignment, automatic usage-based rotation, and durable audit history.

### Modified Capabilities
- None.

## Impact

- Affected code: `app/config.py`, `app/models/schemas.py`, `app/clients/sub2api.py`, `app/stores/sqlite.py`, `app/main.py`
- New code likely required: rotation service, rotation-pool store/models, assignment/audit models, scheduler or interval runner support, rotation API handlers
- Affected APIs: new rotation pool and rotation endpoints, upstream Sub2API admin group and usage APIs
- Operational impact: new auto-rotation policy configuration, operator-managed dedicated rotation pool membership, cooldown/threshold tuning, rollout plan for moving existing users between selected rotation groups
