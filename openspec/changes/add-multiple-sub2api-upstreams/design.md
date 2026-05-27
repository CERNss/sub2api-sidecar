## Overview

The first implementation adds an upstream registry and threads `upstream_id` through operator-initiated discovery and OAuth provisioning. Existing background services continue to use the default upstream until their persisted data models can be partitioned safely.

## Configuration Model

`Settings` will expose:

- `sub2api_upstreams`: ordered tuple of upstream definitions.
- `default_sub2api_upstream_id`: first configured upstream.
- `default_sub2api_upstream`: selected upstream object for internal default-upstream consumers.

YAML shape:

```yaml
sub2api:
  upstreams:
    - id: default
      name: Main Sub2API
      base_url: http://sub2api-main:8080
      admin_api_key_env: SUB2API_ADMIN_API_KEY
    - id: backup
      name: Backup Sub2API
      base_url: http://sub2api-backup:8080
      admin_api_key_env: SUB2API_BACKUP_ADMIN_API_KEY
```

Provisioning defaults remain global in V1. If per-upstream defaults become necessary, add them as a later change rather than mixing that concern into the selector plumbing.

## Backend API

- Add `GET /api/upstreams`.
- Add optional `upstream_id` query parameter to orchestration discovery APIs.
- Add optional `upstream_id` to `ProvisionStartRequest`.
- Add `upstream_id` to flow summaries/details and provisioning start/complete responses.
- Add `get_sub2api_client(upstream_id=None)` with an LRU-style registry keyed by upstream id.

Unknown upstream ids should return 422/400-level client errors and must not fall back silently.

## Persistence

Add `upstream_id` to `ProvisionFlow`. Flow payloads that omit an upstream id are treated as using the configured default upstream when routed. The indexed table does not need a dedicated column in V1 because flow lookup remains by flow id/state and the JSON payload carries the upstream id.

## Frontend

Load upstream metadata after authentication and show selectors in:

- Existing orchestration workspace.
- OAuth provisioning form.

For a single upstream, show the selected upstream compactly or omit noisy selector UI. For multiple upstreams, switching upstream clears selected resources and reloads.

## Follow-Up Work

Per-upstream operational data, balance management, group usage, usage segmentation, and automatic rotation require `upstream_id` in their persisted snapshots/settings/audits. That should be a separate change with storage migration and scheduler fan-out.
