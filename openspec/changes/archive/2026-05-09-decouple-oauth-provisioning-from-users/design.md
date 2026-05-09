## Context

The current OpenSpec contract and implementation describe OAuth provisioning as a user-level workflow: accept an email, create a Sub2API user, bind that user to a group, then create an OpenAI OAuth account in that same group. That model is wrong for the desired operation.

The submitted email comes from outside the Sub2API user system. It is the external OAuth account identifier used by the operator to create a dedicated group and name the OpenAI OAuth account. Existing Sub2API users, API keys, and groups are still orchestrated by the separate dashboard/rotation APIs, but OAuth pre-provisioning should not create or mutate a Sub2API user.

## Goals / Non-Goals

**Goals:**

- Treat `POST /provision/start` input `email` as an external OAuth account email.
- Create the dedicated group and OAuth handoff without creating a Sub2API user.
- Complete OAuth by creating and binding the OpenAI account to the recorded group.
- Make `user_id` optional or absent for OAuth provisioning flows.
- Update dashboard flow inspection so account/group scoped flows are not displayed as user creation records.
- Keep existing user/key/group orchestration APIs focused on upstream resources that already exist.

**Non-Goals:**

- Removing the existing user/key/group orchestration feature.
- Changing the upstream `replace-group` or API-key group update semantics.
- Building a new external identity system for the source of the OAuth email.
- Migrating historical flow rows beyond making reads tolerant of missing `user_id`.

## Decisions

### 1. Keep the API field name `email`, but redefine its domain meaning

`POST /provision/start` should continue accepting `email` for compatibility with current operators and UI forms. The API and UI copy should define it as the external OAuth account email, not as a Sub2API user email.

Why this approach:

- avoids needless API churn for callers that already send `email`
- matches the user's operating language of "input email, create dedicated group, then OAuth"
- keeps a clear boundary between external account identity and Sub2API user identity

Alternative considered: rename the field to `account_email`. Rejected for this change because it would force more client churn while the semantic correction can be made with documentation, labels, and response shape changes.

### 2. Remove Sub2API user creation from OAuth pre-provisioning

The start flow should create only the dedicated group and OAuth handoff state. It must not call upstream user creation, user lookup, or user group binding APIs as a side effect of the external email.

Why this approach:

- the email is not guaranteed to belong to the Sub2API user system
- creating a Sub2API user would invent an identity that operators did not ask to manage
- user/group orchestration has a separate model based on existing upstream users, keys, and groups

Alternative considered: create a shadow Sub2API user for every OAuth account. Rejected because it preserves the incorrect coupling and causes later orchestration views to show false user relationships.

### 3. Make provisioning flow storage account/group scoped

Flow records should still store `flow_id`, `email`, `group_id`, `state`, status, account name, OAuth URL, OAuth account id, error state, and timestamps. `user_id` should be nullable or removed from response contracts for new OAuth pre-provisioning flows.

Why this approach:

- callback completion only needs the stored state, external email, and group id
- dashboards can render historical flows even if older rows contain a user id
- future migrations can clean old data without blocking this correction

Alternative considered: split OAuth provisioning into a new table immediately. Rejected because the current flow table already models pending/completed OAuth lifecycle and can support this with a narrower schema adjustment.

### 4. Keep rotation/orchestration separate from OAuth provisioning

The dashboard may show provisioning flows near existing user/key/group orchestration, but the two workflows should not share identity assumptions. Bulk user/group moves still require an existing upstream `user_id` and must call `replace-group`; single-key moves still require an existing key id and must call the API-key group update endpoint.

Why this approach:

- preserves the user's requested canvas model of key -> user -> group for existing resources
- prevents OAuth provisioning from manufacturing users just to fit the orchestration graph
- keeps the confirmed upstream migration semantics intact

Alternative considered: automatically attach new OAuth accounts to Sub2API users when emails match. Rejected because matching by email would be ambiguous and would quietly reintroduce user-layer coupling.

## Risks / Trade-offs

- [Existing clients expect `user_id` in provisioning responses] -> Keep reads tolerant and document that `user_id` is optional or absent for new OAuth pre-provisioning flows.
- [Old flow rows contain user ids] -> Render them as legacy metadata without implying new flows create users.
- [Operator confusion around "email"] -> Label the UI and API docs as external OAuth account email.
- [Future rotation work reintroduces provisioning coupling] -> Keep rotation specs scoped to existing users and keep OAuth pre-provisioning account/group scoped.

## Migration Plan

1. Update OpenSpec requirements so new provisioning flows are account/group scoped.
2. Remove user creation and user group binding from `POST /provision/start`.
3. Make provisioning response models, SQLite reads, and dashboard flow views tolerant of missing `user_id`.
4. Update timeline events and tests to expect dedicated group creation, OAuth handoff, account creation, and account binding only.
5. Keep existing user/key/group orchestration APIs unchanged except for UI labels that distinguish them from OAuth provisioning.

Rollback:

- Restore the previous user-coupled flow only if operators explicitly decide OAuth provisioning should create Sub2API users again.
- Existing account/group scoped flow records remain valid because callback completion depends on `state`, `email`, and `group_id`, not `user_id`.

## Open Questions

- Should the public API eventually rename `email` to `account_email` with a compatibility alias, or is labeling the existing field enough for V1?
