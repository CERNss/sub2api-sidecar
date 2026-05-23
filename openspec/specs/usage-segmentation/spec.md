# usage-segmentation Specification

## Purpose
Define the shared user usage profile substrate used by balance management and dynamic orchestration. The substrate derives stable, explainable user segments from local operational data snapshots and stores one latest segment record per upstream user.

## Requirements

### Requirement: Persist latest user usage profiles
The system SHALL maintain a latest persisted usage segment record for each discovered Sub2API user.

#### Scenario: Segment refresh stores one latest record per user
- **GIVEN** operational data snapshots contain Sub2API users and user usage for one or more supported windows
- **WHEN** the usage segmentation refresh runs
- **THEN** the system computes a segment record for each discovered user
- **THEN** the system persists the record keyed by user id in PostgreSQL
- **THEN** a later refresh for the same user replaces the latest record instead of creating an ambiguous duplicate latest record

#### Scenario: Missing usage does not hide a user
- **GIVEN** a discovered user has no usable consumption value in any supported window
- **WHEN** the usage segmentation refresh runs
- **THEN** the system still persists a segment record for that user
- **THEN** the record includes zero known windows
- **THEN** the record classifies the user as `idle`

#### Scenario: Dynamic orchestration consumes user profiles
- **GIVEN** persisted segment records exist for rotation candidates
- **WHEN** dynamic orchestration computes a candidate user's move weight
- **THEN** it uses the persisted segment usage value for the configured usage window when available
- **THEN** it falls back to existing local usage snapshots when a segment record is missing

### Requirement: Calculate explainable usage metrics
The system SHALL derive explainable metrics from the supported usage windows `5h`, `1d`, `7d`, and `30d`.

#### Scenario: Window values are normalized into daily averages
- **GIVEN** a user has usage values for `5h`, `1d`, `7d`, and `30d`
- **WHEN** the system computes that user's segment
- **THEN** the record includes the raw per-window consumption values
- **THEN** the record includes daily-average values normalized by each window length
- **THEN** the record includes short-term and medium-term ratios when the required denominator values are non-zero

#### Scenario: Balance runway is derived when possible
- **GIVEN** a user has a known balance and a positive daily average consumption
- **WHEN** the system computes that user's segment
- **THEN** the record includes estimated runway days
- **THEN** missing balance or zero daily average leaves runway days as null

### Requirement: Classify users into stable segments
The system SHALL classify each user into a stable segment label using the computed usage metrics.

#### Scenario: Heavy usage user is classified
- **GIVEN** a user's 30-day daily average or 7-day daily average is high
- **WHEN** the segmentation refresh classifies the user
- **THEN** the user receives the `heavy` segment
- **THEN** the record includes reasons that explain the decision

#### Scenario: Active and light users are distinguished
- **GIVEN** a user has non-zero usage that does not meet heavy thresholds
- **WHEN** the segmentation refresh classifies the user
- **THEN** the user receives `active` or `light` based on lower daily-average thresholds
- **THEN** the record includes the metric values used for the decision

#### Scenario: Short-term spike is identified
- **GIVEN** a user's 5-hour dailyized usage is significantly higher than the user's 30-day daily average
- **WHEN** the segmentation refresh classifies the user
- **THEN** the user receives the `spike` segment unless the user already qualifies as `heavy`
- **THEN** the record includes the short-term ratio as a decision reason

### Requirement: Refresh segmentation on a cadence
The system SHALL run usage segmentation refreshes automatically on the operational cadence while the sidecar process is running.

#### Scenario: Scheduler refreshes segments without operator input
- **GIVEN** the sidecar process is running
- **WHEN** the internal operational cadence elapses
- **THEN** the usage segmentation scheduler refreshes segment records from local operational snapshots
- **THEN** scheduler status includes enabled state, running state, tick count, last refresh count, and last error when one occurs

#### Scenario: Manual refresh is available to operators
- **GIVEN** an authenticated operator wants fresh segment data
- **WHEN** the operator calls the usage segmentation refresh API
- **THEN** the system runs the same refresh path used by the scheduler
- **THEN** the response includes refreshed count and per-segment counts

### Requirement: Expose authenticated segment read APIs
The system SHALL expose authenticated APIs for reading persisted user usage segment records.

#### Scenario: Operator lists segment records
- **GIVEN** usage segment records exist
- **WHEN** an authenticated operator calls the segment list API
- **THEN** the response returns recent latest records ordered by segment and usage intensity
- **THEN** the response includes total count and aggregate segment counts

#### Scenario: Unauthenticated callers cannot read segment records
- **GIVEN** a caller has no valid admin session, access-key header, or bearer token
- **WHEN** the caller requests usage segmentation APIs
- **THEN** the system returns an authentication error
- **THEN** no segment records are returned
