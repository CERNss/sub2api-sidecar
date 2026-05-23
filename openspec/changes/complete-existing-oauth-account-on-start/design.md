## Overview

The provisioning start path will branch after group resolution:

1. Resolve or create the dedicated group for the submitted email.
2. Search existing upstream OpenAI accounts by exact account name/email match.
3. If found, update account configuration payload fields derived from `Sub2APIProvisioningDefaults`, bind the account to the dedicated group if needed, save a completed flow, and skip OAuth URL generation.
4. If not found, keep the current pending OAuth flow.

## Account Configuration Update

The update operation should be conservative:

- Preserve existing `raw.credentials`, including OAuth tokens.
- Merge or set only the managed provisioning defaults:
  - `platform`
  - `type`
  - `concurrency`
  - `group_ids`
  - temporary-unschedulable credentials fields
  - model mapping credentials field
  - websocket/context-pool extra fields
- Retain existing `name` when available, but normalize to the submitted email for the update payload so future exact-name lookup remains stable.

The client will send `PUT /api/v1/admin/accounts/{account_id}`. This mirrors existing admin update patterns used for API keys. If a downstream Sub2API uses a different route, the path constant can be adjusted in the client without changing service logic.

## Flow Semantics

Completed-on-start flows use:

- `status=completed`
- `oauth_account_id=<existing account id>`
- `oauth_url=None`
- `oauth_session_id=None`
- events showing group resolution, existing account configuration, account binding/resolution, and completion

`ProvisionStartResponse` gains:

- `status`
- `oauth_required`
- optional `oauth_account_id`
- nullable `oauth_url`

Existing pending flow responses remain valid with `status=pending_oauth`, `oauth_required=true`, and a non-empty OAuth URL.
