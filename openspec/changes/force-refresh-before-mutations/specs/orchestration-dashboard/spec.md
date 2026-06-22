## MODIFIED Requirements

### Requirement: Existing user/group orchestration API
The system SHALL expose authenticated APIs for discovering existing Sub2API users, groups, API keys, upstream accounts from the Sub2API admin accounts surface, transferring encoded admin API keys, and moving API key routing between groups.

#### Scenario: Mutating orchestration refreshes operational data first
- **GIVEN** the operator or scheduler starts a real orchestration mutation
- **WHEN** the system is about to move users, move API keys, transfer encoded keys, execute automatic rotation, roll back an automatic rotation run, or create a token-authenticated API key
- **THEN** the system refreshes raw operational data from Sub2API before making the mutation call
- **THEN** the system refreshes derived usage segmentation and group usage views from the fresh raw snapshots
- **THEN** the mutation decision uses the refreshed local snapshots and derived views
- **THEN** if the forced refresh fails, the system does not perform the mutation

#### Scenario: Orchestration previews remain read-only
- **GIVEN** the operator starts a dry-run automatic rotation or key transfer preview
- **WHEN** the system calculates the preview
- **THEN** the system does not force a new Sub2API operational-data collection
- **THEN** the preview remains based on the current local snapshot state
