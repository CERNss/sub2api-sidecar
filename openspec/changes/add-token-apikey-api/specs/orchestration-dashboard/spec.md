## ADDED Requirements

### Requirement: Token-authenticated API key API
The system SHALL expose a POST-only API key endpoint for external automation at `/api/v1/apikey`, with compatibility for deployments that reach the service at `/sidecar/api/v1/apikey`.

#### Scenario: External caller creates a named encoded key
- **GIVEN** a caller has a valid sidecar API token
- **AND** the request action is `create`
- **AND** the requested key name is in `service:environment:object:version:email` format
- **WHEN** the parsed email matches exactly one Sub2API user
- **THEN** the system creates the upstream API key under that user
- **THEN** the selected `group_id` is the first active group from the user's current or allowed groups
- **THEN** caller-provided quota and other supported API key attributes are forwarded
- **THEN** caller-provided group fields are ignored

#### Scenario: Missing target account falls back to admin
- **GIVEN** a caller has a valid sidecar API token
- **AND** the request action is `create`
- **AND** the key name is in `service:environment:object:version:email` format
- **WHEN** the parsed email does not match exactly one Sub2API user
- **THEN** the system creates the key under the resolved admin user
- **THEN** the selected `group_id` is the first active group available to the admin user

#### Scenario: Old encoded key names are rejected
- **GIVEN** a caller has a valid sidecar API token
- **AND** the request action is `create`
- **WHEN** the requested key name does not match `service:environment:object:version:email`
- **THEN** the system returns `success=false` with status `INVALID_KEY_NAME_FORMAT`
- **THEN** no upstream API key is created

#### Scenario: External caller lists encoded keys
- **GIVEN** a caller has a valid sidecar API token
- **AND** the request action is `list`
- **WHEN** the caller posts to the API key endpoint
- **THEN** the system returns only API keys whose names match `service:environment:object:version:email`
- **THEN** the response includes the parsed service, environment, object, version, email, owner user id, owner email, group id, and safe upstream key metadata
- **THEN** no raw API key value is returned

#### Scenario: External caller filters encoded keys by email
- **GIVEN** a caller has a valid sidecar API token
- **AND** the request action is `list`
- **WHEN** the request includes an email filter
- **THEN** the system returns only encoded API keys whose parsed email matches that email exactly after normalization

#### Scenario: Token generation from key management page
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator generates an API token from the key management page
- **THEN** the system returns a long-lived bearer-compatible sidecar API token
- **THEN** prior long-lived API tokens for the same operator are revoked
- **THEN** the operator's browser session remains valid
- **THEN** the UI shows the token for copying without exposing Sub2API admin credentials
