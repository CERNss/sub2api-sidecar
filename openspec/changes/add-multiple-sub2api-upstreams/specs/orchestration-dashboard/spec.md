## MODIFIED Requirements

### Requirement: Existing user/group orchestration API
The system SHALL expose authenticated APIs for discovering configured upstreams and for selecting which upstream Sub2API instance backs existing user, group, account, and API key discovery.

#### Scenario: Operator lists configured upstreams
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator requests the upstream list API
- **THEN** the response includes upstream id, display name, base URL, and whether it is the default upstream
- **THEN** the response does not include any admin API key value

#### Scenario: Operator discovers resources from a selected upstream
- **GIVEN** multiple upstreams are configured
- **WHEN** the operator requests orchestration users, groups, accounts, or user API keys with `upstream_id`
- **THEN** the system sends Sub2API admin requests to the selected upstream client
- **THEN** the response includes the selected `upstream_id`
- **THEN** an unknown `upstream_id` is rejected with a client error before any upstream request is made

### Requirement: React dashboard renders orchestration state
The React UI SHALL let an authenticated operator choose the upstream Sub2API instance used by orchestration discovery views.

#### Scenario: Operator switches orchestration upstream
- **GIVEN** more than one upstream is configured
- **WHEN** the operator opens the orchestration workspace
- **THEN** the UI displays an upstream selector using authenticated upstream metadata
- **WHEN** the operator switches the selected upstream
- **THEN** user, group, account, and API key discovery reload from that upstream
- **THEN** current resource selections are cleared if they do not belong to the newly selected upstream
