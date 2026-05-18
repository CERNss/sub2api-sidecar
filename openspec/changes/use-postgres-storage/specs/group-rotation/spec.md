## MODIFIED Requirements

### Requirement: Persist current assignment state and rotation audit
The system SHALL persist each managed user's current group assignment and SHALL persist the outcome of every manual or automatic rotation attempt in durable local storage.

#### Scenario: Assignment state survives restart
- **GIVEN** a user has been assigned or rotated into a dedicated rotation-target group
- **WHEN** the service restarts
- **THEN** the system can load the user's current group assignment, assignment mode, last rotation time, and last decision reason from PostgreSQL

#### Scenario: Rotation execution writes an audit record
- **GIVEN** a manual or automatic rotation attempt finishes
- **WHEN** the sidecar persists the result
- **THEN** the system stores an audit record containing the user identity, source group, target group, trigger type, decision reason, execution status, usage snapshot, and timestamps

#### Scenario: Pool membership survives restart
- **GIVEN** an operator has added one or more exclusive groups into the local Landing or Rotation pool
- **WHEN** the service restarts
- **THEN** the system can load each persisted pool membership without reselecting groups manually
