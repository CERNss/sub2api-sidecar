## 1. Settings and persistence

- [x] 1.1 Add auto-rotation settings parsing, including interval, cooldown, and V1 usage-window enum validation (`5h` / `1d` / `7d` / `30d`) in `app/config.py`
- [x] 1.2 Keep rotation assignment metadata separate from OAuth provisioning flow identity
- [x] 1.3 Add SQLite storage for rotation-pool membership, current user-group assignments, and append-only rotation audit events

## 2. Upstream Sub2API integration

- [x] 2.1 Update `app/clients/sub2api.py` to wrap the confirmed admin APIs for group discovery, group replacement, allowed-group updates, and usage queries
- [x] 2.2 Add response parsing and error handling needed for manual and automatic rotation workflows
- [x] 2.3 Harden rotation to use key-migrating `replace-group` semantics instead of `allowed_groups`, and add single API-key group update wrapper

## 3. Existing-user rotation scope

- [x] 3.1 Ensure rotation candidates come from existing upstream users, explicit operator input, or persisted assignment state
- [x] 3.2 Ensure OAuth provisioning flows are not treated as Sub2API user identities or automatic rotation candidates

## 4. Rotation pool APIs

- [x] 4.1 Add authenticated APIs to list upstream group candidates and manage local rotation-pool membership
- [x] 4.2 Enforce exclusive-group-only pool membership and persist pool changes durably
- [x] 4.3 Reject subscription groups from the rotation pool because upstream `replace-group` only supports dedicated standard groups

## 5. Rotation execution and APIs

- [x] 5.1 Add rotation request/response schemas and authenticated FastAPI endpoints for manual rotation and automatic rotation runs
- [x] 5.2 Implement a shared rotation executor with dedicated-target validation, same-group no-op handling, pending-flow protection, cooldown checks, and audit logging
- [x] 5.3 Implement automatic rotation evaluation from Sub2API usage data, V1 window support for `5h` / `1d` / `7d` / `30d`, new-user-last ordering, and optional interval-based execution
- [x] 5.4 Add dynamic allocation preview and synchronize automatic rotation candidates from current upstream user/group relationships
- [x] 5.5 Add separate dynamic orchestration UI/API configuration for pool, enablement, window, cooldown, and thresholds
- [x] 5.6 Add account scheduling range and automatic new-user assignment into dynamic orchestration
- [x] 5.7 Add usage-balance dead band (`imbalance_epsilon`) and per-move improvement threshold (`improvement_delta`) tunables to dynamic orchestration config, runtime API, and React UI; persist `dead_band_skipped` on the orchestration run record

## 6. Verification and rollout

- [x] 6.1 Add tests for config validation, pool membership persistence, assignment persistence, and manual rotation success, skip, and failure cases
- [x] 6.2 Add tests for candidate group classification, public-target rejection, existing-user rotation scope, and automatic rotation evaluation/execution behavior
- [x] 6.3 Document rotation-pool selection APIs, staged rollout steps, and rollback guidance for operators
- [x] 6.4 Add tests for dry-run dynamic allocation, upstream candidate synchronization, and ambiguous current-group skipping
- [x] 6.5 Add tests for runtime dynamic configuration persistence and execution gating
- [x] 6.6 Add tests for new-user auto-assignment only within the configured scheduling range
- [x] 6.7 Add tests for `imbalance_epsilon` dead-band skip and `improvement_delta` blocking marginal swaps
