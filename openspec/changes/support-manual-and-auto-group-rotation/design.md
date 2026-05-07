## Context

The current sidecar has both OAuth account provisioning and existing-resource orchestration concerns. OAuth provisioning creates a dedicated Sub2API group and later binds an OpenAI OAuth account to that group, while existing Sub2API users, API keys, and groups need a separate orchestration path.

The operating model behind this change is specific to existing upstream users: operators need to move users between a known set of dedicated Sub2API groups manually or automatically based on usage. Those groups are controlled rotation targets; public groups are explicitly out of scope for rotation. This change does not make OAuth provisioning create, assign, or rotate Sub2API users.

The upstream `infra/sub2api` backend already exposes the important primitives needed by the sidecar:

- authenticated admin APIs via `x-api-key`
- admin group listing with `is_exclusive` classification
- user group replacement with key migration
- user `allowed_groups` updates
- per-user and batch usage queries

The sidecar therefore does not need to invent new backend behavior. It needs to become an orchestration layer that can choose a target group, execute a safe reassignment, and keep enough local state to make that behavior durable and explainable.

## Goals / Non-Goals

**Goals:**

- Keep OAuth account provisioning separate from existing user/key/group rotation.
- Let operators discover current upstream groups, classify them by exclusivity, and select dedicated groups into the local rotation pool.
- Provide authenticated API-first manual rotation and automatic usage-based rotation.
- Keep manual and automatic rotation on the same execution path so behavior stays consistent.
- Let automatic rotation evaluate a configurable V1 usage window chosen from `5h`, `1d`, `7d`, or `30d`, and schedule users with no created API key last.
- Persist assignment state and rotation audit data locally so restarts do not lose context.
- Prevent unsafe rotations such as no-op moves, cooldown violations, or treating pending OAuth account flows as user identities.

**Non-Goals:**

- Building a full control plane for creating, editing, or deleting upstream groups from the sidecar.
- Replacing Sub2API's own quota, usage, or scheduling logic.
- Real-time per-request rotation decisions.
- Solving every legacy-user migration edge case in the first implementation.

## Decisions

### 1. Keep rotation scoped to existing upstream users

Rotation behavior should be controlled independently from OAuth account provisioning. Manual rotation requires an explicit existing upstream user id. Automatic rotation should discover or load existing upstream users and evaluate those users against usage data. Pending OAuth account flows are not user identities and should not be used as rotation candidates.

Why this approach:

- matches the corrected provisioning model where the submitted OAuth email is external to the Sub2API user system
- prevents the sidecar from inventing Sub2API users just to support rotation
- keeps existing-user migration semantics tied to confirmed upstream admin APIs

Alternative considered: assign new OAuth provisioning flows into the rotation pool. Rejected because OAuth provisioning is account/group scoped and must not create or mutate Sub2API users.

### 2. Discover candidate groups from upstream and persist rotation-pool selection locally

The sidecar should fetch candidate groups from the upstream admin groups API, use `is_exclusive` to classify them, and let operators select which exclusive groups belong to the local rotation pool. The selected pool membership should be persisted in SQLite. Automatic rotation settings such as enablement, cooldown, interval, and usage-window policy still belong in configuration.

Why this approach:

- matches the desired operating workflow of discovering real groups instead of hardcoding ids up front
- keeps public-group rejection tied to the upstream `is_exclusive` source of truth
- allows pool membership to evolve without redeploying the sidecar
- persists pool selection durably across restarts

Alternative considered: keep the rotation pool entirely in static configuration. Rejected because it makes day-to-day pool selection cumbersome and does not fit the operator workflow of browsing existing groups first.

### 3. Persist assignment and audit as first-class SQLite tables

Flow storage alone is not enough once existing users can be rotated. The sidecar should keep:

- `provision_flows` for pending/completed OAuth orchestration
- `rotation_pool_groups` for the operator-selected exclusive target groups
- `user_group_assignments` for the current effective assignment snapshot per user
- `rotation_events` for append-only manual/automatic rotation outcomes

Provisioning flow records remain useful for OAuth account lifecycle visibility, but rotation assignment state should be stored independently from those flows.

Why this approach:

- rotation needs indexed lookups by user, current group, and recent execution time
- rotation pool membership is separate state from per-user assignment and should survive restart
- audit and current-state queries serve different access patterns
- callback completion should not be coupled to existing-user rotation state

Alternative considered: store every new field inside the existing flow JSON only. Rejected because rotation acts on users long after OAuth completion and needs durable state outside the provisioning flow lifecycle.

### 4. Extend `Sub2APIClient` around confirmed admin APIs, not guessed compatibility paths

The current client uses broad candidate-path fallbacks for a minimal provisioning flow. Rotation should add explicit wrappers for the confirmed upstream APIs used in `infra/sub2api`, especially:

- admin group listing for rotation-pool discovery
- user group replacement with key migration via `POST /api/v1/admin/users/{user_id}/replace-group`
- single API key group updates via `PUT /api/v1/admin/api-keys/{key_id}` when a future flow needs one-key migration
- user allowed-group updates only for user-management flows that explicitly need them
- per-user or batch usage retrieval

Why this approach:

- rotation-pool discovery should reuse the upstream exclusivity flag instead of guessing locally
- rotation needs predictable semantics, especially around key migration and auth-cache invalidation
- existing-user rotation must not rely only on updating `allowed_groups`, because existing API keys would keep their old effective group until explicitly migrated
- usage-driven logic depends on consistent payload parsing
- confirmed endpoints are safer than adding more generic fallback guesses
- upstream `replace-group` currently supports dedicated standard groups only, so subscription groups must be filtered out of the rotation pool

Alternative considered: keep expanding candidate path guessing. Rejected because rotation failures would become harder to diagnose and test.

### 5. Implement one shared rotation executor for manual and automatic paths

Manual rotation and automatic rotation should call the same internal executor:

1. load current assignment and pending-flow state
2. resolve the desired target group
3. enforce safety rules such as same-group no-op and cooldown
4. call Sub2API group replacement
5. update assignment state and write an audit event

Automatic rotation adds only an evaluation phase that computes the desired target group from usage data before calling the executor.

Why this approach:

- keeps side effects consistent across triggers
- reduces duplicated failure handling
- makes tests easier because both trigger types share the same core invariants
- centralizes target validation so public groups cannot slip into one path but not the other

Alternative considered: separate manual and automatic implementations. Rejected because they would drift on safety checks, response shape, and persistence behavior.

### 6. Support both on-demand and interval-based automatic rotation

Automatic rotation should be callable through an authenticated API such as `POST /rotation/auto/run`, and the same cycle should also be runnable on a configured in-process interval when auto-rotation is enabled.

Why this approach:

- API-triggered execution is easier to verify in staging and tests
- interval execution satisfies the "automatic" operational requirement
- both trigger styles can share the same evaluator and executor

Alternative considered: only rely on external cron. Rejected because the feature should be usable from the sidecar alone and because the user explicitly asked for automatic rotation inside this service.

### 7. Use a configurable V1 usage-window enum and schedule no-key users last

Automatic rotation should evaluate existing users against a configurable V1 window chosen from `5h`, `1d`, `7d`, or `30d`. Existing users without any created API key should be evaluated after users who already have keys.

Why this approach:

- it matches the requested operating model more closely than a hardcoded 7-day band
- it keeps the first implementation aligned with usage windows that are already easier to map onto upstream data
- it lets operators tune sensitivity without changing the core rotation workflow
- deprioritizing users with no key avoids unnecessary early churn for existing users that have not started using the system

Alternative considered: support arbitrary hour/day windows in V1. Rejected because the upstream usage interfaces do not cleanly expose every rolling window shape, which would add adapter complexity before the first usable release.

### 8. Keep the contract API-first

The existing sidecar has an HTML operator page, but this change should define the primary interface as authenticated JSON APIs. A UI can call those APIs later, but the implementation should not depend on browser-only workflows.

Why this approach:

- the user has already said operations are mainly API-driven
- API-first behavior is easier to automate and test
- it prevents the rotation design from becoming tangled with a single page's form state

Alternative considered: implement rotation only in the HTML page. Rejected because it would not meet the operational API requirement.

## Risks / Trade-offs

- [Usage data arrives late or at inconsistent granularity] -> Use a configurable V1 window enum and store the snapshot used for each decision in the audit event.
- [The upstream usage APIs do not expose the exact desired rolling window directly] -> Limit V1 to `5h`, `1d`, `7d`, and `30d`, and implement a usage adapter that selects the best available upstream source for those windows.
- [A scheduled rotation cycle overlaps with a manual rotation request] -> Use a single-process execution lock so only one rotation executor mutates assignments at a time.
- [Operators add the wrong exclusive group into the pool] -> Expose candidate discovery with exclusivity metadata and persist auditable pool membership changes.
- [A pending OAuth flow is mistaken for a Sub2API user] -> Keep rotation candidates sourced from upstream user discovery, explicit operator input, or persisted assignment state only.
- [Rollback after moving users across rotation groups is not instant] -> Disable automatic rotation and use the same manual rotation API to move existing users back if needed.

## Migration Plan

1. Ship automatic rotation settings in a disabled state and verify startup validation.
2. Discover current groups through the sidecar API and select a small dedicated rotation pool.
3. Use manual rotation for a small set of existing users and validate key migration and audit output.
4. Enable automatic rotation on demand first, then turn on interval execution after thresholds and cooldowns are tuned.

Rollback:

- disable automatic rotation
- stop invoking manual rotation
- clear or adjust local rotation-pool membership if needed
- rotate affected users back explicitly if assignment changes must be undone

## Open Questions

- Which upstream usage source should be the V1 primary adapter for the supported `5h`, `1d`, `7d`, and `30d` windows: user usage trend, batch user usage, or a fallback order?
