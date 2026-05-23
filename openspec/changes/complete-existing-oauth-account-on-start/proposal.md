## Why

Operators often re-run OAuth provisioning for an email whose OpenAI OAuth account already exists in the selected upstream Sub2API. Today the sidecar still starts a new OAuth handoff and asks the operator to authorize again, even though the useful work is to normalize the existing account's runtime configuration and ensure it is bound to the email's dedicated group.

## What Changes

- During `POST /provision/start`, resolve the dedicated group first, then look up an existing OpenAI OAuth account by matching account name/email to the submitted email.
- If a matching account exists, update the account-level provisioning defaults without replacing OAuth credentials or requiring browser login.
- Ensure the existing account is bound to the dedicated group.
- Persist a completed flow and return a completed response from `POST /provision/start` with no OAuth URL required.
- Preserve the current OAuth URL flow when no matching account exists.

## Impact

- Affected backend: `app/clients/sub2api.py`, `app/services/provisioning.py`, schemas, tests.
- Affected frontend: OAuth provisioning form status and disabled callback controls when the start response is already completed.
- Compatibility: callers that only expect pending OAuth still receive `oauth_url` for new accounts. Completed starts expose explicit `status` and `oauth_required=false`.

## Non-Goals

- Do not edit or regenerate existing OAuth access/refresh tokens.
- Do not create or mutate Sub2API users.
- Do not infer matches from partial names; matching remains exact name/email semantics.
