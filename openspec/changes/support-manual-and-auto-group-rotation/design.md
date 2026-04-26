## Context

The current sidecar is a minimal provisioning orchestrator. `POST /provision/start` always creates a dedicated Sub2API group, creates a user, binds that user to the new group, and stores only flow metadata in SQLite. `POST /provision/oauth/complete` later creates the OAuth account and binds it back to the same `flow.group_id`.

That implementation is intentionally simple, but it assumes "one user, one dedicated group, one bound account flow". The operating model behind this change is different: operators need to move users between a known set of dedicated Sub2API groups manually or automatically based on usage. Those groups are controlled rotation targets; public groups are explicitly out of scope for rotation.

The upstream `infra/sub2api` backend already exposes the important primitives needed by the sidecar:

- authenticated admin APIs via `x-api-key`
- admin group listing with `is_exclusive` classification
- user group replacement with key migration
- user `allowed_groups` updates
- per-user and batch usage queries

The sidecar therefore does not need to invent new backend behavior. It needs to become an orchestration layer that can choose a target group, execute a safe reassignment, and keep enough local state to make that behavior durable and explainable.

## Goals / Non-Goals

**Goals:**

- Preserve today's dedicated-group provisioning as the default path.
- Add a managed-pool provisioning mode for deployments that want new users assigned into a controlled pool of dedicated rotation groups.
- Let operators discover current upstream groups, classify them by exclusivity, and select dedicated groups into the local rotation pool.
- Provide authenticated API-first manual rotation and automatic usage-based rotation.
- Keep manual and automatic rotation on the same execution path so behavior stays consistent.
- Let automatic rotation evaluate a configurable V1 usage window chosen from `5h`, `1d`, `7d`, or `30d`, and schedule users with no created API key last.
- Persist assignment state and rotation audit data locally so restarts do not lose context.
- Prevent unsafe rotations such as no-op moves, cooldown violations, or rotations during pending OAuth flows.

**Non-Goals:**

- Building a full control plane for creating, editing, or deleting upstream groups from the sidecar.
- Replacing Sub2API's own quota, usage, or scheduling logic.
- Real-time per-request rotation decisions.
- Solving every legacy-user migration edge case in the first implementation.

## Decisions

### 1. Keep provisioning mode explicit: `dedicated` by default, `managed_pool` as opt-in

New provisioning behavior should be controlled by a configuration setting rather than inferred from the presence of rotation data. In `dedicated` mode the sidecar continues to create one group per user exactly as it does today. In `managed_pool` mode the sidecar chooses a target group from a configured pool of dedicated rotation groups and records that choice on the flow.

Why this approach:

- keeps current deployments backward compatible
- makes rollout reversible with a single config change
- avoids surprising operators who only want the existing dedicated behavior

Alternative considered: replace dedicated provisioning with pooled provisioning everywhere. Rejected because it would force an operational migration even for installs that do not need rotation.

### 2. Discover candidate groups from upstream and persist rotation-pool selection locally

The sidecar should fetch candidate groups from the upstream admin groups API, use `is_exclusive` to classify them, and let operators select which exclusive groups belong to the local rotation pool. The selected pool membership should be persisted in SQLite. Automatic rotation settings such as enablement, cooldown, interval, and usage-window policy still belong in configuration.

Why this approach:

- matches the desired operating workflow of discovering real groups instead of hardcoding ids up front
- keeps public-group rejection tied to the upstream `is_exclusive` source of truth
- allows pool membership to evolve without redeploying the sidecar
- persists pool selection durably across restarts

Alternative considered: keep the rotation pool entirely in static configuration. Rejected because it makes day-to-day pool selection cumbersome and does not fit the operator workflow of browsing existing groups first.

### 3. Persist assignment and audit as first-class SQLite tables

Flow storage alone is not enough once users can be rotated after provisioning. The sidecar should keep:

- `provision_flows` for pending/completed OAuth orchestration
- `rotation_pool_groups` for the operator-selected exclusive target groups
- `user_group_assignments` for the current effective assignment snapshot per user
- `rotation_events` for append-only manual/automatic rotation outcomes

Flow records should also gain assignment metadata such as `assignment_mode` and decision reason so callback completion can remain deterministic.

Why this approach:

- rotation needs indexed lookups by user, current group, and recent execution time
- rotation pool membership is separate state from per-user assignment and should survive restart
- audit and current-state queries serve different access patterns
- callback completion should not need to recompute routing after a restart

Alternative considered: store every new field inside the existing flow JSON only. Rejected because rotation acts on users long after OAuth completion and needs durable state outside the provisioning flow lifecycle.

### 4. Extend `Sub2APIClient` around confirmed admin APIs, not guessed compatibility paths

The current client uses broad candidate-path fallbacks for a minimal provisioning flow. Rotation should add explicit wrappers for the confirmed upstream APIs used in `infra/sub2api`, especially:

- admin group listing for rotation-pool discovery
- user group replacement with key migration
- user allowed-group updates when needed
- per-user or batch usage retrieval

Why this approach:

- rotation-pool discovery should reuse the upstream exclusivity flag instead of guessing locally
- rotation needs predictable semantics, especially around key migration and auth-cache invalidation
- usage-driven logic depends on consistent payload parsing
- confirmed endpoints are safer than adding more generic fallback guesses

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

### 7. Use a configurable V1 usage-window enum and schedule new users last

Automatic rotation should evaluate users against a configurable V1 window chosen from `5h`, `1d`, `7d`, or `30d`. Users without any created API key should be treated as new users and evaluated after users who already have keys.

Why this approach:

- it matches the requested operating model more closely than a hardcoded 7-day band
- it keeps the first implementation aligned with usage windows that are already easier to map onto upstream data
- it lets operators tune sensitivity without changing the core rotation workflow
- deprioritizing users with no key avoids unnecessary early churn for accounts that have not started using the system

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
- [A user is rotated while OAuth is still pending] -> Treat `pending_oauth` as a hard skip condition for rotation.
- [Rollback after moving users into the managed rotation pool is not instant] -> Make rollback configuration-only for new provisioning, and use the same manual rotation API to move existing users back if needed.

## Migration Plan

1. Ship the code with `dedicated` mode as the default so existing behavior does not change on deployment.
2. Add automatic rotation settings in a disabled state and verify startup validation.
3. Discover current groups through the sidecar API and select a small dedicated rotation pool.
4. Enable `managed_pool` for new provisioning only and verify new users land in the expected exclusive groups.
5. Use manual rotation for a small set of existing users and validate key migration and audit output.
6. Enable automatic rotation on demand first, then turn on interval execution after thresholds and cooldowns are tuned.

Rollback:

- set provisioning mode back to `dedicated`
- disable automatic rotation
- stop invoking manual rotation
- clear or adjust local rotation-pool membership if needed
- rotate affected users back explicitly if managed-pool assignment must be undone

## Open Questions

- Which upstream usage source should be the V1 primary adapter for the supported `5h`, `1d`, `7d`, and `30d` windows: user usage trend, batch user usage, or a fallback order?
