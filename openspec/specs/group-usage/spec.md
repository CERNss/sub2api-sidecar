# group-usage Specification

## Purpose
Define the persisted group usage substrate used by dynamic orchestration to understand current load across selected rotation groups.

## Requirements

### Requirement: Persist latest group usage profiles
The system SHALL persist one latest usage profile per upstream group using local PostgreSQL storage.

#### Scenario: Refresh stores group usage records
- **GIVEN** operational snapshots include upstream groups and dashboard group usage distribution
- **WHEN** group usage refresh runs
- **THEN** each group with an id has a latest group usage record
- **THEN** the record includes per-window usage values, daily averages, trend ratios, member counts, source metadata, observed timestamp, and refreshed timestamp

#### Scenario: Missing dashboard data falls back to local signals
- **GIVEN** a group is present in the group snapshot but has no dashboard aggregate for a window
- **WHEN** group usage refresh runs
- **THEN** the group still receives a record
- **THEN** missing windows are represented as zero or unknown according to the available local data

### Requirement: Read group usage profiles
The system SHALL expose authenticated APIs to read latest group usage profiles and manually refresh them.

#### Scenario: Operator lists group usage records
- **GIVEN** group usage records are stored
- **WHEN** an authenticated operator requests the group usage list API
- **THEN** the response includes items, pagination fields, and total count
- **THEN** each item includes group identity, usage windows, daily averages, source metadata, and refresh timestamps

#### Scenario: Operator refreshes group usage records
- **GIVEN** operational snapshots exist
- **WHEN** an authenticated operator requests manual group usage refresh
- **THEN** the system recomputes latest group usage records
- **THEN** the response returns refreshed count and aggregate window availability
