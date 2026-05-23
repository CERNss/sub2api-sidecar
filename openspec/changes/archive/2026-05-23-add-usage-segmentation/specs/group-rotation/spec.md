# group-rotation Specification

## MODIFIED Requirements

### Requirement: Execute automatic usage-balanced rotation
The system SHALL evaluate eligible users against current Rotation pool usage load and persisted usage segment data, and SHALL rotate users so recent usage is distributed as evenly as possible across dedicated rotation groups.

#### Scenario: On-demand automatic rotation cycle reassigns eligible users
- **GIVEN** automatic rotation is enabled and the dedicated rotation pool contains at least one target group
- **WHEN** an authenticated operator calls `POST /rotation/auto/run`
- **THEN** the system synchronizes existing upstream users whose current direct group can be inferred unambiguously
- **THEN** the system treats only users currently assigned to a selected rotation-pool group as automatic rotation candidates
- **THEN** users without an unambiguous current direct group are skipped instead of guessed from multi-group access data
- **THEN** the system reads each candidate's latest persisted usage segment record when available
- **THEN** the system computes current usage totals per selected Rotation pool group for the configured usage window
- **THEN** the system chooses the lowest-usage target group when a move is needed to reduce usage imbalance
- **THEN** the system skips users when moving them would not reduce usage imbalance
- **THEN** the system executes the same group-replacement workflow used by manual rotation for users whose desired group differs from their current group
- **THEN** the response includes moved, skipped, and failed results for the rotation cycle

#### Scenario: Rotation falls back when segment record is unavailable
- **GIVEN** a rotation candidate has no persisted usage segment record
- **WHEN** automatic rotation computes usage for that candidate
- **THEN** the system falls back to the existing local user usage snapshot for the configured window
- **THEN** the usage snapshot records the source as fallback local usage rather than persisted segmentation
