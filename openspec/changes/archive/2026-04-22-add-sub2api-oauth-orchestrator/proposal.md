## Why

This project now contains a working local Sub2API OpenAI OAuth orchestration service, but the repository has no source-of-truth spec describing the required workflow and constraints. Capturing the behavior in OpenSpec makes the current implementation reviewable, testable, and easier to extend without breaking the email-driven provisioning contract.

## What Changes

- Document a new `openai-oauth-provisioning` capability for the minimal orchestration service.
- Specify the `POST /provision/start`, `GET /provision/oauth/callback`, and `GET /` behaviors.
- Specify that the entry email is the canonical account name throughout the flow and MUST NOT be replaced by OAuth-returned email data.
- Specify the required Sub2API admin orchestration behaviors, configuration model, flow persistence expectations, and minimal error handling.

## Capabilities

### New Capabilities
- `openai-oauth-provisioning`: Provision a Sub2API-managed OpenAI OAuth flow with manual OAuth handoff, email-based identity continuity, group bindings, and a minimal operator UI.

### Modified Capabilities
- None.

## Impact

- Documents behavior implemented in `app/main.py`, `app/services/provisioning.py`, `app/clients/sub2api.py`, `app/stores/*`, and `app/templates/*`.
- Establishes a baseline spec for future Redis/DB-backed flow storage, API-path adjustments, and end-to-end testing.
- Adds a source-of-truth contract under `openspec/specs/` after archive.
