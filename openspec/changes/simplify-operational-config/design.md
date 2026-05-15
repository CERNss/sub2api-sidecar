## Context

Operational data now collects Sub2API accounts, groups, users, and usage into SQLite before consumers evaluate rules or policies. The deployment config still presents runtime module switches and old module-specific knobs, including auto-rotation interval/tuning fields and credit-control tick fields. Those fields make it look like each consumer owns its own polling design and force operators to restart the service for routine enable/disable changes, even though the desired model is shared collection first, then local reads.

## Goals / Non-Goals

**Goals:**
- Make the deployment config obvious by removing `auto_rotation`, `credit_control`, and `operational_data` runtime sections entirely.
- Remove parsing and documentation for runtime scheduler switches, scheduler intervals, and automatic-rotation policy fields from deployment config.
- Use a single internal 60 second cadence for background collection and scheduler loops.
- Preserve runtime policy APIs for automatic rotation and add runtime settings APIs for operational data and credit control.
- Make runtime enable/disable and operational data expiration changes take effect without service restart.
- Make operational-data snapshots and metrics the shared read layer for notification evaluation, credit-control reads, and automatic-orchestration reads wherever those features need upstream state.
- Fail fast when removed deployment fields are present instead of keeping compatibility.

**Non-Goals:**
- Removing the authenticated automatic-rotation runtime policy fields such as cooldown, usage window, thresholds, and balance tolerances.
- Changing webhook rule `readIntervalMinutes`, `forMinutes`, or `cooldownMinutes`.
- Replacing SQLite operational data storage.
- Adding a user-configurable collection interval under another name.
- Replacing direct upstream mutation calls such as group replacement, API-key group update, and credit balance update.

## Decisions

### Deployment config does not own runtime switches

Deployment config accepts no `auto_rotation`, `credit_control`, or `operational_data` sections. These names are runtime domains, so putting them in `config.yaml` would imply a restart-bound operator workflow. If any of those sections or their previous environment variables are present, startup fails with a clear configuration error.

Alternative considered: keep old fields but ignore them. That would hide invalid configuration and preserve the exact confusion this change removes, so removed fields must be rejected.

### Runtime settings live in SQLite and API/UI

Operational data runtime settings are persisted in SQLite and edited only through the authenticated runtime API/UI. They are represented as an API document, not as `config.yaml`:

```json
{
  "enabled": true,
  "expiration": null
}
```

`expiration` is optional; if it is unset, local operational data never expires. Operators change it through authenticated API/UI and the next scheduler tick reads the new value.

Credit-control runtime settings are persisted in SQLite and edited only through the authenticated runtime API/UI:

```json
{
  "enabled": true
}
```

Automatic rotation keeps using its existing dynamic orchestration runtime API/UI for execution enabled state and business policy:

```json
{
  "enabled": false,
  "auto_assign_new_users": false,
  "cooldown_minutes": 0,
  "usage_window": "1d"
}
```

Provisioning assignment mode is persisted in SQLite and edited only through the authenticated provisioning API/UI:

```json
{
  "assignment_mode": "dedicated"
}
```

New OAuth flows read the current provisioning runtime setting when `POST /provision/start` is called and persist the selected mode on the flow record. Later changes affect new flows only.

All four are runtime settings, not deployment settings, and none of these documents belongs in `config.yaml`.

### Internal cadence is code-owned

The background collection cadence is a code constant of 60 seconds. Operational data, automatic rotation, and credit-control scheduler loops start with the process and use that internal cadence. At each tick, the scheduler reads its current persisted runtime setting. Disabled runtime settings skip work but do not require stopping the process thread. Status endpoints report `cadence_seconds` as observed process status, but it is not a deployment config contract.

Alternative considered: keep `operational_data.collect_interval_seconds` as the single user-facing interval. The current requirement is simpler: no interval config at all.

### Runtime policy config stays runtime policy

Automatic-rotation policy fields remain in the authenticated runtime config API/UI because they are not deployment boot settings. If no runtime config has been saved, the default automatic-rotation policy uses model defaults, including `enabled=false`.

### Operational data is the shared read layer

The collection stage fetches upstream Sub2API data in a fixed order, persists raw snapshots and derived metric samples, then consumers read local SQLite state:

1. Operational-data collection reads upstream accounts, groups, users, per-user usage windows, per-user API keys, current-day usage, and previous-day usage.
2. Persistence writes raw snapshots, derived metric samples, and per-source status to SQLite.
3. Notification evaluation reads notification config, rule state, and metric samples from SQLite.
4. Credit-control list/filter/detail/target selection reads users and usage from operational snapshots.
5. Automatic rotation discovery, assignment sync, and usage load reads groups, users, API keys, and usage from operational snapshots.

Direct upstream calls remain only for mutating upstream state or for explicit operator actions whose purpose is to mutate upstream state.

## Risks / Trade-offs

- Existing deployments with old keys or runtime sections will fail startup after upgrade -> operators must remove the sections and set runtime switches in the web UI/API.
- A fixed 60 second cadence is less tunable -> it avoids drift and keeps operational data fresh enough for current consumers.
- Status APIs expose `cadence_seconds` as an observed process value -> docs and tests should frame it as status, not a configurable field.
- Runtime settings default records must be created lazily or read with stable defaults -> otherwise fresh installs could report missing settings.
- Using local snapshots means consumers may act on data up to one cadence old -> status APIs surface collection freshness and expiration controls gate stale data.

## Migration Plan

1. Remove `auto_rotation`, `credit_control`, and `operational_data` sections from `config.yaml`, plus their previous environment variables, before deploying.
2. Start the service with only deployment settings such as app, storage, OpenAI OAuth, Sub2API connection, and Sub2API provisioning defaults.
3. Configure provisioning assignment mode, operational data enabled/expiration, credit-control enabled, and automatic-rotation business policy through the authenticated runtime UI/API after startup.
4. Confirm scheduler status endpoints report running process schedulers and current runtime enabled state.
