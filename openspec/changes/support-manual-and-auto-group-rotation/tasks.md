## 1. Settings and persistence

- [x] 1.1 Add managed-pool and auto-rotation settings parsing, including interval, cooldown, and V1 usage-window enum validation (`5h` / `1d` / `7d` / `30d`) in `app/config.py`
- [x] 1.2 Extend flow models and persistence to record assignment metadata needed by provisioning callbacks
- [x] 1.3 Add SQLite storage for rotation-pool membership, current user-group assignments, and append-only rotation audit events

## 2. Upstream Sub2API integration

- [x] 2.1 Update `app/clients/sub2api.py` to wrap the confirmed admin APIs for group discovery, group replacement, allowed-group updates, and usage queries
- [x] 2.2 Add response parsing and error handling needed for manual and automatic rotation workflows
- [x] 2.3 Harden rotation to use key-migrating `replace-group` semantics instead of `allowed_groups`, and add single API-key group update wrapper

## 3. Provisioning mode support

- [x] 3.1 Update `app/services/provisioning.py` so `POST /provision/start` resolves either a newly created dedicated group or a selected dedicated rotation-target group from the local pool
- [x] 3.2 Keep `POST /provision/oauth/complete` bound to the flow's recorded assignment and persist completed assignment state for later rotation

## 4. Rotation pool APIs

- [x] 4.1 Add authenticated APIs to list upstream group candidates and manage local rotation-pool membership
- [x] 4.2 Enforce exclusive-group-only pool membership and persist pool changes durably
- [x] 4.3 Reject subscription groups from the rotation pool because upstream `replace-group` only supports dedicated standard groups

## 5. Rotation execution and APIs

- [x] 5.1 Add rotation request/response schemas and authenticated FastAPI endpoints for manual rotation and automatic rotation runs
- [x] 5.2 Implement a shared rotation executor with dedicated-target validation, same-group no-op handling, pending-flow protection, cooldown checks, and audit logging
- [x] 5.3 Implement automatic rotation evaluation from Sub2API usage data, V1 window support for `5h` / `1d` / `7d` / `30d`, new-user-last ordering, and optional interval-based execution

## 6. Verification and rollout

- [x] 6.1 Add tests for config validation, pool membership persistence, assignment persistence, and manual rotation success, skip, and failure cases
- [x] 6.2 Add tests for candidate group classification, public-target rejection, managed-pool provisioning, and automatic rotation evaluation/execution behavior
- [x] 6.3 Document rotation-pool selection APIs, staged rollout steps, and rollback guidance for operators
