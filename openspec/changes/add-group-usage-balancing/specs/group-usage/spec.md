## ADDED Requirements

### Requirement: Persist Latest Group Usage Profiles

The system SHALL persist one latest usage profile per upstream group using local PostgreSQL storage.

#### Scenario: Refresh stores group usage records

- **GIVEN** operational snapshots include upstream groups and dashboard group usage distribution
- **WHEN** group usage refresh runs
- **THEN** each group with an id SHALL have a latest group usage record
- **AND** the record SHALL include per-window usage values, daily averages, trend ratios, member counts, source metadata, observed timestamp, and refreshed timestamp.

#### Scenario: Missing dashboard data falls back to local signals

- **GIVEN** a group is present in the group snapshot but has no dashboard aggregate for a window
- **WHEN** group usage refresh runs
- **THEN** the group SHALL still receive a record
- **AND** missing windows SHALL be represented as zero or unknown according to the available local data.

### Requirement: Read Group Usage Profiles

The system SHALL expose authenticated APIs to read latest group usage profiles and manually refresh them.

#### Scenario: Operator lists group usage records

- **GIVEN** group usage records are stored
- **WHEN** an authenticated operator requests the group usage list API
- **THEN** the response SHALL include items, pagination fields, and total count
- **AND** each item SHALL include group identity, usage windows, daily averages, source metadata, and refresh timestamps.

#### Scenario: Operator refreshes group usage records

- **GIVEN** operational snapshots exist
- **WHEN** an authenticated operator requests manual group usage refresh
- **THEN** the system SHALL recompute latest group usage records
- **AND** return refreshed count and aggregate window availability.
