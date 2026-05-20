## MODIFIED Requirements

### Requirement: Automatic Rotation Balances Group Usage

Automatic rotation SHALL balance selected rotation pool groups by group-level usage loads.

#### Scenario: Planner uses persisted group usage records

- **GIVEN** selected rotation pool groups have persisted group usage records
- **AND** one selected group has higher configured-window usage than another
- **WHEN** automatic rotation runs
- **THEN** the planner SHALL use persisted group usage loads as the source of truth
- **AND** select a move only if simulating the candidate user move reduces the group load spread by at least the configured improvement delta.

#### Scenario: Planner records group balancing metadata

- **GIVEN** automatic rotation plans or executes a group-balancing move
- **WHEN** the run record is saved
- **THEN** the result metadata SHALL include group load source, load summaries before the move, source load, target load, and decision type.

#### Scenario: Planner falls back when group usage is missing

- **GIVEN** selected rotation pool groups do not all have persisted group usage records
- **WHEN** automatic rotation runs
- **THEN** the planner SHALL fall back to summing candidate user usage for missing groups
- **AND** retain existing dry-run, cooldown, rollback, and pool eligibility behavior.
