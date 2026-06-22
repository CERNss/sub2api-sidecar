## MODIFIED Requirements

### Requirement: Manual and scheduled balance mutations
The system SHALL apply manual and scheduled credit-control mutations against users selected from current operational data.

#### Scenario: Credit-control execution refreshes before selecting or updating users
- **GIVEN** an operator or scheduler starts a real credit-control execution
- **WHEN** the system is about to resolve filter targets, explicit user targets, a run-now policy target, or a due scheduled policy target
- **THEN** the system refreshes raw operational data from Sub2API before making balance update calls
- **THEN** the system refreshes derived usage segmentation and group usage views from the fresh raw snapshots
- **THEN** target resolution and balance updates use the refreshed local data
- **THEN** if the forced refresh fails, the system does not update user balances

#### Scenario: Credit-control previews remain read-only
- **GIVEN** an operator previews a manual adjustment or policy
- **WHEN** the system calculates the preview
- **THEN** the system does not force a new Sub2API operational-data collection
- **THEN** the preview remains based on the current local snapshot state
