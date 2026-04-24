## Why

The project cannot rely on the OAuth provider to redirect back into this sidecar service, because the browser is expected to land on a separate localhost callback target. The spec and implementation therefore need to treat paste-back of the final localhost callback URL as the only supported completion path.

## What Changes

- **BREAKING** Replace direct callback completion assumptions with a manual paste-back completion flow.
- Add an explicit completion API that accepts the pasted localhost callback URL and parses `code` and `state` from it.
- Split the configured OAuth provider redirect URI from the sidecar app URL.
- Update the frontend and tests so the primary workflow is: start -> click OAuth -> paste callback URL -> complete.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `openai-oauth-provisioning`: require a single manual paste-back completion path driven by a configured external localhost redirect URI.

## Impact

- Affects `app/config.py`, `app/main.py`, `app/services/provisioning.py`, `app/models/schemas.py`, `app/templates/index.html`, `.env.example`, `README.md`, and API tests.
- Updates the main OpenSpec contract to remove the direct callback assumption.
