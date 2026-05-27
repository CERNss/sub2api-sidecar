## MODIFIED Requirements

### Requirement: Provide local compose runtime assets
The project SHALL support configuring one or more upstream Sub2API instances while keeping upstream admin API keys in environment secrets.

#### Scenario: Single upstream is configured through upstreams
- **GIVEN** deployment config provides `sub2api.upstreams` with one upstream item
- **AND** `.env` provides that upstream item's admin API key environment variable
- **WHEN** the sidecar starts
- **THEN** the system exposes exactly one upstream using the configured upstream id
- **THEN** existing APIs that omit `upstream_id` use that configured upstream as the default

#### Scenario: Multiple upstreams are configured
- **GIVEN** deployment config provides `sub2api.upstreams`
- **AND** each upstream item has an id, display name, base URL, and admin API key environment variable name
- **AND** `.env` provides the named admin API key environment variables
- **WHEN** the sidecar starts
- **THEN** the system loads every configured upstream
- **THEN** upstream ids are stable, non-empty, unique, and safe for URLs
- **THEN** startup fails with a configuration error when an upstream id is duplicated, an admin key env var is missing, or an upstream base URL is empty
- **THEN** API responses never return upstream admin API key values
