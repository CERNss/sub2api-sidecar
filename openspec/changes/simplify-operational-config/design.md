## Context

Operational data now collects Sub2API accounts, groups, users, and usage into PostgreSQL before consumers evaluate rules or policies. The deployment config still presents runtime module switches and old module-specific knobs, including auto-rotation interval/tuning fields and credit-control tick fields. Those fields make it look like each consumer owns its own polling design and force operators to restart the service for routine enable/disable changes, even though the desired model is shared collection first, then local reads.

## Goals / Non-Goals

**Goals:**
- Make the deployment config obvious by removing `auto_rotation`, `credit_control`, and `operational_data` runtime sections entirely.
- Remove parsing and documentation for runtime scheduler switches, scheduler intervals, and automatic-rotation policy fields from deployment config.
- Keep scheduler intervals out of deployment config; operational-data collection uses a PostgreSQL runtime interval that defaults to 60 seconds, while credit-control and automatic-rotation process loops keep their internal cadence.
- Preserve runtime policy APIs for automatic rotation and add runtime settings APIs for operational data and credit control.
- Make runtime enable/disable and operational data interval/expiration changes take effect without service restart.
- Make operational-data snapshots and metrics the shared read layer for notification evaluation, credit-control reads, and automatic-orchestration reads wherever those features need upstream state.
- Fail fast when removed deployment fields are present instead of keeping compatibility.

**Non-Goals:**
- Removing the authenticated automatic-rotation runtime policy fields such as cooldown, usage window, thresholds, and balance tolerances.
- Changing webhook rule `readIntervalMinutes`, `forMinutes`, or `cooldownMinutes`.
- Replacing PostgreSQL operational data storage.
- Adding a deployment-configurable collection interval under another name.
- Replacing direct upstream mutation calls such as group replacement, API-key group update, and credit balance update.

## Decisions

### Deployment config does not own runtime switches

Deployment config accepts no `auto_rotation`, `credit_control`, or `operational_data` sections. These names are runtime domains, so putting them in `config.yaml` would imply a restart-bound operator workflow. If any of those sections or their previous environment variables are present, startup fails with a clear configuration error.

Alternative considered: keep old fields but ignore them. That would hide invalid configuration and preserve the exact confusion this change removes, so removed fields must be rejected.

### Runtime settings live in PostgreSQL and API/UI

Operational data runtime settings are persisted in PostgreSQL and edited only through the authenticated runtime API/UI. They are represented as an API document, not as `config.yaml`:

```json
{
  "enabled": true,
  "collect_interval_seconds": 60,
  "expiration": null,
  "retention_seconds": null,
  "max_storage_mb": null
}
```

`collect_interval_seconds` defaults to 60 seconds and is changed only through authenticated API/UI. It is not a deployment config field. `expiration` is optional and controls consumer staleness checks; if it is unset, local operational data never expires for reads. `retention_seconds` and `max_storage_mb` are optional cleanup guards. `retention_seconds` deletes local snapshot and metric records older than the retention window after collection. `max_storage_mb` deletes oldest local snapshot and metric records until the operational data payload size is under the configured cap, while keeping the latest record for each source and metric key. Operators change these values through authenticated API/UI and the next scheduler wait/tick reads the new values.

Credit-control runtime settings are persisted in PostgreSQL and edited only through the authenticated runtime API/UI:

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

Provisioning assignment mode is persisted in PostgreSQL and edited only through the authenticated provisioning API/UI:

```json
{
  "assignment_mode": "dedicated"
}
```

New OAuth flows read the current provisioning runtime setting when `POST /provision/start` is called and persist the selected mode on the flow record. Later changes affect new flows only.

All four are runtime settings, not deployment settings, and none of these documents belongs in `config.yaml`.

### Operational data cadence is runtime-owned

The background collection cadence defaults to 60 seconds and belongs to operational-data runtime settings in PostgreSQL. The operational data scheduler loop starts with the process and reads the current persisted interval between ticks. Disabled runtime settings skip work but do not require stopping the process thread. After each successful collection pass, the service runs operational-data cleanup from the same PostgreSQL runtime settings. Status endpoints report `cadence_seconds`, `collect_interval_seconds`, cleanup settings, and current operational-data payload size as observed process/runtime status, but none is a deployment config contract.

Automatic rotation and credit-control scheduler loops keep the code-owned 60 second process cadence; they consume the local operational snapshots and their own runtime enabled/policy settings.

### Runtime policy config stays runtime policy

Automatic-rotation policy fields remain in the authenticated runtime config API/UI because they are not deployment boot settings. If no runtime config has been saved, the default automatic-rotation policy uses model defaults, including `enabled=false`.

### Operational data is the shared read layer

The collection stage fetches upstream Sub2API data in a fixed order, persists raw snapshots and derived metric samples, then consumers read local PostgreSQL state:

1. Operational-data collection reads upstream accounts, groups, users, per-user usage windows, per-user API keys, current-day usage, and previous-day usage.
2. Persistence writes raw snapshots, derived metric samples, and per-source status to PostgreSQL.
3. Notification evaluation reads notification config, rule state, and metric samples from PostgreSQL.
4. Credit-control list/filter/detail/target selection reads users and usage from operational snapshots.
5. Automatic rotation discovery, assignment sync, and usage load reads groups, users, API keys, and usage from operational snapshots.

Direct upstream calls remain only for mutating upstream state or for explicit operator actions whose purpose is to mutate upstream state.

## Risks / Trade-offs

- Existing deployments with old keys or runtime sections will fail startup after upgrade -> operators must remove the sections and set runtime switches in the web UI/API.
- Runtime collection cadence can be set too aggressively -> the API validates a minimum interval and status exposes source errors/freshness.
- Runtime cleanup can be set too aggressively -> size cleanup preserves the newest record for each source and metric key, and expiration still protects consumers from stale reads.
- Status APIs expose `cadence_seconds` as an observed process value -> docs and tests should frame it as status, not a configurable field.
- Runtime settings default records must be created lazily or read with stable defaults -> otherwise fresh installs could report missing settings.
- Using local snapshots means consumers may act on data up to one cadence old -> status APIs surface collection freshness and expiration controls gate stale data.

## Migration Plan

1. Remove `auto_rotation`, `credit_control`, and `operational_data` sections from `config.yaml`, plus their previous environment variables, before deploying.
2. Start the service with only deployment settings such as app, storage, OpenAI OAuth, Sub2API connection, and Sub2API provisioning defaults.
3. Configure provisioning assignment mode, operational data enabled/collect interval/expiration/cleanup guards, credit-control enabled, and automatic-rotation business policy through the authenticated runtime UI/API after startup.
4. Confirm scheduler status endpoints report running process schedulers and current runtime enabled state.
