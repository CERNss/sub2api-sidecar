## Why
External automation needs a token-authenticated API for creating and discovering Sub2API API keys that follow the `service:environment:object:version:email` naming convention. Operators also need an easy way to generate a long-lived token from the key management page without exposing Sub2API admin credentials.

## What Changes
- Add a POST-only `/api/v1/apikey` sidecar API for token-authenticated `create` and `list` actions.
- Reuse key management email parsing and first-available-user-group selection when creating keys.
- Fall back to the admin user when the key name contains no matching existing email account.
- Add key management UI affordance for generating a long-lived sidecar API token.

## Impact
- API callers use the sidecar access token through `Authorization: Bearer <token>` or `X-Access-Key`.
- Upstream Sub2API admin APIs remain the only mutation path.
- Existing operator session behavior is unchanged.
