# Proposal: Transfer Admin API Keys To Email Users

## Summary
Add an authenticated orchestration action that scans either the admin user's API keys or all users' API keys, detects keys named in the `service:object:version:email` style, and transfers each key to the exact matching existing Sub2API user while preserving the key string.

## Motivation
Some keys were created under an admin-owned user with the destination user encoded in the key name. Operators need a safe bulk transfer that moves each key to the matching email user, uses one of that user's available groups, and removes the per-key quota limit without regenerating the key.

## Scope
- Add backend client support for admin key owner/group/quota updates.
- Add an authenticated orchestration endpoint for previewing or executing the transfer.
- Resolve users by exact normalized email only.
- Pick the first available user group when multiple groups are present.
- Keep the API key value unchanged and set quota to unlimited.
- Surface per-key skipped and failed reasons.
- Add UI controls to preview and execute the transfer from a dedicated key transfer tab.
- Support an all-users scan mode by listing users and then listing each user's API keys through the existing upstream endpoints.

## Non-Goals
- Do not create missing users.
- Do not fuzzy-match emails.
- Do not rotate or regenerate API key strings.
- Do not transfer arbitrary key naming schemes outside the `service:object:version:email` format.

## Risks
- The upstream Sub2API admin update endpoint must support owner and quota fields for full execution. If it does not, execution should fail loudly per key rather than reporting success.
- The all-users scan uses an N+1 upstream request pattern, so operators should preview before executing and avoid treating it as a cheap page-refresh operation on very large installations.
