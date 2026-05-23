## Why

Operators need one sidecar instance to manage more than one upstream Sub2API admin surface. Today the sidecar has exactly one `sub2api.base_url` and one `SUB2API_ADMIN_API_KEY`, so every discovery action, OAuth provisioning flow, and admin-token login is implicitly tied to that single upstream. Adding another upstream requires another sidecar deployment or manual config switching, which makes operations noisy and risks applying a user/group/account action to the wrong Sub2API instance.

## What Changes

- Add a named upstream configuration model with stable `upstream_id`, display name, base URL, admin API key environment variable, timeout, and provisioning defaults.
- Preserve the existing single-upstream config/env contract by treating `sub2api.base_url` plus `SUB2API_ADMIN_API_KEY` as the default upstream when no list is configured.
- Expose authenticated upstream discovery so the UI can show available upstreams without returning secrets.
- Let operator-facing orchestration discovery APIs accept `upstream_id` and execute reads against the selected upstream.
- Let OAuth provisioning start flows accept `upstream_id`, persist it on the flow, and complete OAuth against the same upstream selected at start time.
- Keep existing background automation services on the default upstream in this first change; full per-upstream operational data, credit-control, and rotation partitioning are out of scope for V1.

## Capabilities

### Modified Capabilities

- `deployment-tooling`: Configure one or more upstream Sub2API instances without exposing admin keys in YAML.
- `orchestration-dashboard`: Operators can select which upstream to inspect for users, groups, accounts, and API keys.
- `openai-oauth-provisioning`: OAuth provisioning flows are bound to a selected upstream and use that upstream throughout start and completion.

## Impact

- Affected backend: `app/config.py`, `app/main.py`, `app/models/flow.py`, `app/models/schemas.py`, provisioning service wiring, tests.
- Affected frontend: upstream selector in orchestration and provisioning views, request parameter/body changes, flow display labels.
- Affected docs/config: `config.example.yaml`, `.env.example`, README notes.
- Compatibility: existing single-upstream config continues to work.

## Non-Goals

- Do not redesign all persisted runtime data tables to be multi-tenant in this change.
- Do not run operational-data collection, credit-control schedulers, usage segmentation, group usage refresh, or automatic rotation independently for every upstream yet.
- Do not allow admin API keys to be entered through the UI; keys remain environment secrets.
