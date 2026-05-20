## MODIFIED Requirements

### Requirement: Persist Latest User Usage Profiles

The system SHALL treat persisted user usage profiles as shared substrate data for downstream balance management and orchestration.

#### Scenario: Dynamic orchestration consumes user profiles

- **GIVEN** persisted user usage segment records exist
- **WHEN** automatic orchestration evaluates candidate users
- **THEN** it SHALL use the persisted user's configured-window usage and segment metadata as the candidate move weight
- **AND** it SHALL fall back to existing local user usage snapshots only when the persisted profile is missing.
