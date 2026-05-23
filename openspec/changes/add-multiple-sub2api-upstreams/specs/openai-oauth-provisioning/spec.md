## MODIFIED Requirements

### Requirement: Start provisioning flow from email
The system SHALL let an authenticated operator choose the upstream Sub2API instance used for an OAuth provisioning flow and SHALL persist that choice on the flow.

#### Scenario: Valid external email creates a pending OAuth flow on selected upstream
- **GIVEN** multiple upstreams are configured
- **AND** the client submits a valid external OAuth account email and `upstream_id` to `POST /provision/start`
- **WHEN** the system starts a provisioning flow
- **THEN** group creation and OAuth URL generation use the selected upstream client
- **THEN** the persisted flow stores the selected `upstream_id`
- **THEN** the response includes the selected `upstream_id`

#### Scenario: Missing upstream id uses the default upstream
- **GIVEN** one or more upstreams are configured
- **WHEN** the client starts provisioning without `upstream_id`
- **THEN** the system uses the configured default upstream
- **THEN** the response includes that default upstream id

### Requirement: Complete OAuth using stored flow context
The system SHALL complete OAuth against the same upstream selected when the flow was started.

#### Scenario: OAuth completion uses the flow upstream
- **GIVEN** a pending provisioning flow stores `upstream_id`
- **WHEN** `POST /provision/oauth/complete` is called with a valid callback URL for that flow state
- **THEN** the system loads the flow by state
- **THEN** OAuth code exchange, OAuth account lookup/creation, and account group binding use the flow's upstream client
- **THEN** the completion response includes the flow's upstream id
