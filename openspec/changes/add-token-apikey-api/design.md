## Design

The sidecar already treats `AuthSession.access_key` as both browser session credential and bearer-compatible API credential. The new UI button creates a long-lived API token with the same auth manager and returns the raw token once. API token generation is a rotation operation: prior long-lived API tokens for the same operator are revoked, while browser/login sessions keep their normal TTL and remain valid.

The external API uses one POST endpoint because the requested public URL is fixed. Requests include an `action` field:

- `create`: create an API key with the requested `name` and optional upstream key attributes.
- `list`: return keys whose name matches `service:object:version:email`, optionally filtered by parsed email.

Creation resolves the target user by parsing the final segment of the name. If the email maps to exactly one upstream user, the target group is the first active group from that user's current or allowed groups, matching key management behavior. If the email is absent or no unique account exists, the key is created under the resolved admin user, using the admin user's first available group.

The Sub2API client gets a thin wrapper around upstream `POST /api/v1/admin/users/{user_id}/api-keys`. It forwards optional request attributes while forcing `user_id` and selected `group_id` from sidecar routing logic so callers cannot bypass group selection.
