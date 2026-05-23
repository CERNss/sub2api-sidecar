## MODIFIED Requirements

### Requirement: Start provisioning flow from email
The system SHALL avoid OAuth handoff when the selected upstream already has an OpenAI OAuth account whose account name or account email exactly matches the submitted external email.

#### Scenario: Existing OAuth account is configured without browser authorization
- **GIVEN** an authenticated client submits a valid external OAuth account email to `POST /provision/start`
- **AND** the selected upstream contains an OpenAI OAuth account whose name or email exactly matches that email
- **WHEN** the system starts provisioning
- **THEN** the system resolves or creates the dedicated group for the email
- **THEN** the system updates the existing account's managed account configuration to the configured provisioning defaults
- **THEN** the system preserves existing OAuth credential tokens
- **THEN** the system binds the existing account to the dedicated group when the binding is missing
- **THEN** the system stores a flow record with `status=completed` and the existing `oauth_account_id`
- **THEN** the response includes `status=completed`, `oauth_required=false`, and no OAuth URL is required
- **THEN** the system does not generate an OAuth login URL

#### Scenario: New OAuth account still requires OAuth handoff
- **GIVEN** an authenticated client submits a valid external OAuth account email to `POST /provision/start`
- **AND** the selected upstream does not contain an exact matching OpenAI OAuth account
- **WHEN** the system starts provisioning
- **THEN** the system continues to create a pending OAuth flow
- **THEN** the response includes `status=pending_oauth`, `oauth_required=true`, and an OAuth URL

## ADDED Requirements

### Requirement: Update existing OAuth account configuration safely
The system SHALL configure existing OAuth accounts using the same account-level provisioning defaults used for newly-created OAuth accounts, while preserving OAuth credentials.

#### Scenario: Existing account update preserves credentials and applies managed defaults
- **GIVEN** a matching existing OAuth account has existing credential tokens
- **WHEN** provisioning configures the existing account
- **THEN** the update request keeps the existing credential token fields
- **THEN** the update request sets the configured account platform, account type, concurrency, group binding list, temporary-unschedulable settings, model mapping, and workspace mode
- **THEN** no OAuth code exchange request is made
