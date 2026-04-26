## Why

The current sidecar always creates one dedicated Sub2API group and one OAuth account flow per user. That model does not fit the operating model where users need to be reassigned over time across a small fixed pool of pre-created dedicated groups that each represent a controlled rotation target.

Operators need a safe way to discover existing groups, select dedicated groups into a rotation pool, and move users between those dedicated rotation groups manually or automatically without rewriting the existing provisioning flow or breaking the current dedicated-group behavior. Public groups must never appear as rotation targets.

## What Changes

- Add a managed rotation-pool mode so provisioning can assign new users to an existing dedicated rotation group instead of always creating a brand-new dedicated group.
- Add authenticated sidecar APIs to discover upstream groups, classify exclusive vs non-exclusive groups, and add or remove dedicated groups from the local rotation pool.
- Add authenticated sidecar APIs for manual group rotation and for running automatic usage-based rotation.
- Persist rotation-pool membership, assignment state, and rotation audit history in SQLite so decisions survive restarts and are inspectable.
- Extend the Sub2API admin client to use confirmed user-group replacement and usage APIs needed for rotation.
- Keep dedicated provisioning as the default behavior so existing deployments remain backward compatible until managed-pool mode is enabled.
- Let automatic rotation evaluate usage over a configurable V1 window chosen from `5h`, `1d`, `7d`, or `30d`, and schedule users without any created API key last.

## Capabilities

### New Capabilities
- `group-rotation`: Manage rotation-pool discovery and selection, dedicated rotation-target assignment, manual reassignment, automatic usage-based rotation, and durable audit history.

### Modified Capabilities
- `openai-oauth-provisioning`: Provisioning must resolve the target group from either dedicated or managed-pool assignment mode and complete OAuth against the assigned dedicated group recorded in the flow.

## Impact

- Affected code: `app/config.py`, `app/models/flow.py`, `app/models/schemas.py`, `app/services/provisioning.py`, `app/clients/sub2api.py`, `app/stores/sqlite.py`, `app/main.py`
- New code likely required: rotation service, rotation-pool store/models, assignment/audit models, scheduler or interval runner support, rotation API handlers
- Affected APIs: `POST /provision/start`, `POST /provision/oauth/complete`, new rotation pool and rotation endpoints, upstream Sub2API admin group and usage APIs
- Operational impact: new auto-rotation policy configuration, operator-managed dedicated rotation pool membership, cooldown/threshold tuning, rollout plan for moving existing users into the managed rotation pool
