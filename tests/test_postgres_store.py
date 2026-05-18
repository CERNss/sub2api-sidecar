from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.flow import AssignmentMode, FlowStatus, ProvisionEvent, ProvisionEventStatus, ProvisionEventType, ProvisionFlow
from app.models.operational_data import (
    CreditControlRuntimeSettings,
    OperationalDataRuntimeSettings,
    OperationalDataSnapshot,
    OperationalDataSourceStatus,
    OperationalMetricSample,
    ProvisioningRuntimeSettings,
)
from app.models.rotation import RotationEvent, RotationPoolGroup, RotationResultStatus, RotationTrigger, UserGroupAssignment
from app.stores.postgres import PostgresFlowStore


def build_flow() -> ProvisionFlow:
    now = datetime.now(timezone.utc)
    return ProvisionFlow(
        flow_id="flow-1",
        email="user@example.com",
        user_id=123,
        group_id=456,
        state="state-1",
        status=FlowStatus.pending_oauth,
        account_name="user@example.com",
        oauth_url="https://example.com/oauth",
        created_at=now,
        updated_at=now,
    )


def test_postgres_store_initializes_schema_and_persists_across_instances(app_env: dict[str, str]) -> None:
    database_url = app_env["database_url"]
    first_store = PostgresFlowStore(database_url)
    first_store.save(build_flow())

    second_store = PostgresFlowStore(database_url)
    reloaded_by_flow_id = second_store.get_by_flow_id("flow-1")
    reloaded_by_state = second_store.get_by_state("state-1")

    assert reloaded_by_flow_id is not None
    assert reloaded_by_state is not None
    assert reloaded_by_flow_id.email == "user@example.com"
    assert reloaded_by_state.group_id == 456
    assert reloaded_by_flow_id.flow_id == "flow-1"


def test_postgres_store_updates_persisted_flow(app_env: dict[str, str]) -> None:
    database_url = app_env["database_url"]
    store = PostgresFlowStore(database_url)
    flow = build_flow()
    store.save(flow)

    flow.status = FlowStatus.completed
    flow.oauth_account_id = "oa-1"
    flow.error_message = None
    flow.updated_at = datetime.now(timezone.utc)
    store.update(flow)

    reloaded_store = PostgresFlowStore(database_url)
    persisted = reloaded_store.get_by_flow_id("flow-1")

    assert persisted is not None
    assert persisted.status == FlowStatus.completed
    assert persisted.oauth_account_id == "oa-1"


def test_postgres_store_lists_flows_and_persists_provision_events(app_env: dict[str, str]) -> None:
    database_url = app_env["database_url"]
    store = PostgresFlowStore(database_url)
    first = build_flow()
    second = build_flow().model_copy(
        update={
            "flow_id": "flow-2",
            "state": "state-2",
            "email": "other@example.com",
            "status": FlowStatus.completed,
            "assignment_mode": AssignmentMode.managed_pool,
            "oauth_account_id": "oa-2",
            "updated_at": datetime.now(timezone.utc),
        }
    )
    store.save(first)
    store.save(second)
    store.save_provision_event(
        ProvisionEvent(
            flow_id="flow-2",
            event_type=ProvisionEventType.completed,
            status=ProvisionEventStatus.succeeded,
            message="done",
            details={"account_id": "oa-2"},
        )
    )

    reloaded = PostgresFlowStore(database_url)
    completed = reloaded.list_flows(status=FlowStatus.completed)
    managed_count = reloaded.count_flows(assignment_mode=AssignmentMode.managed_pool)
    matching_email = reloaded.list_flows(email="other")
    events = reloaded.list_provision_events("flow-2")

    assert [flow.flow_id for flow in completed] == ["flow-2"]
    assert managed_count == 1
    assert [flow.flow_id for flow in matching_email] == ["flow-2"]
    assert len(events) == 1
    assert events[0].event_type == ProvisionEventType.completed


def test_postgres_store_persists_rotation_pool_assignments_and_events(app_env: dict[str, str]) -> None:
    database_url = app_env["database_url"]
    now = datetime.now(timezone.utc)
    first_store = PostgresFlowStore(database_url)
    first_store.upsert_rotation_pool_group(
        RotationPoolGroup(
            group_id=11,
            group_name="rotation-low",
            platform="openai",
            status="active",
            is_exclusive=True,
            priority=0,
            created_at=now,
            updated_at=now,
        )
    )
    first_store.upsert_user_assignment(
        UserGroupAssignment(
            user_id=101,
            email="rotate@example.com",
            current_group_id=11,
            current_group_name="rotation-low",
            created_at=now,
            updated_at=now,
        )
    )
    first_store.save_rotation_event(
        RotationEvent(
            user_id=101,
            email="rotate@example.com",
            source_group_id=11,
            target_group_id=22,
            trigger_type=RotationTrigger.manual,
            status=RotationResultStatus.moved,
            reason="manual move",
            created_at=now,
            updated_at=now,
        )
    )

    second_store = PostgresFlowStore(database_url)
    groups = second_store.list_rotation_pool_groups()
    assignment = second_store.get_user_assignment(101)
    events = second_store.list_rotation_events()

    assert len(groups) == 1
    assert groups[0].group_id == "11"
    assert second_store.get_rotation_pool_group(11) is not None
    assert second_store.get_rotation_pool_group("11") is not None
    second_store.delete_rotation_pool_group("11")
    assert second_store.get_rotation_pool_group(11) is None
    assert assignment is not None
    assert assignment.current_group_id == 11
    assert len(events) == 1
    assert events[0].target_group_id == 22


def test_postgres_store_persists_latest_operational_metric_sample(app_env: dict[str, str]) -> None:
    store = PostgresFlowStore(app_env["database_url"])
    older = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    newer = older + timedelta(minutes=1)

    store.save_operational_metric_samples(
        [
            OperationalMetricSample(
                metric_key="account_invalid",
                value=1,
                observed_at=older,
                collected_at=older,
                snapshot={"version": "older"},
            ),
            OperationalMetricSample(
                metric_key="account_invalid",
                value=2,
                observed_at=newer,
                collected_at=newer,
                snapshot={"version": "newer"},
            ),
            OperationalMetricSample(
                metric_key="user_balance_low",
                value=9,
                observed_at=older,
                collected_at=older,
            ),
        ]
    )

    latest = store.get_latest_operational_metric_sample("account_invalid")

    assert latest is not None
    assert latest.value == 2
    assert latest.snapshot == {"version": "newer"}


def test_postgres_store_persists_latest_operational_data_snapshot(app_env: dict[str, str]) -> None:
    store = PostgresFlowStore(app_env["database_url"])
    older = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    newer = older + timedelta(minutes=1)

    store.save_operational_data_snapshot(
        OperationalDataSnapshot(
            source_key="accounts",
            observed_at=older,
            collected_at=older,
            payload=[{"id": "older"}],
        )
    )
    store.save_operational_data_snapshot(
        OperationalDataSnapshot(
            source_key="accounts",
            observed_at=newer,
            collected_at=newer,
            payload=[{"id": "newer"}],
        )
    )

    snapshot = store.get_latest_operational_data_snapshot("accounts")

    assert snapshot is not None
    assert snapshot.payload == [{"id": "newer"}]


def test_postgres_store_cleans_operational_data_by_retention_cutoff(app_env: dict[str, str]) -> None:
    store = PostgresFlowStore(app_env["database_url"])
    older = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    newer = older + timedelta(minutes=10)

    store.save_operational_metric_samples(
        [
            OperationalMetricSample(
                metric_key="account_invalid",
                value=1,
                observed_at=older,
                collected_at=older,
            ),
            OperationalMetricSample(
                metric_key="account_invalid",
                value=2,
                observed_at=newer,
                collected_at=newer,
            ),
        ]
    )
    store.save_operational_data_snapshot(
        OperationalDataSnapshot(
            source_key="accounts",
            observed_at=older,
            collected_at=older,
            payload=[{"id": "older"}],
        )
    )
    store.save_operational_data_snapshot(
        OperationalDataSnapshot(
            source_key="accounts",
            observed_at=newer,
            collected_at=newer,
            payload=[{"id": "newer"}],
        )
    )

    result = store.cleanup_operational_data(retention_cutoff=older + timedelta(minutes=1))

    assert result.deleted_metric_samples == 1
    assert result.deleted_snapshots == 1
    assert store.get_latest_operational_metric_sample("account_invalid").value == 2
    assert store.get_latest_operational_data_snapshot("accounts").payload == [{"id": "newer"}]


def test_postgres_store_cleans_operational_data_by_size_without_deleting_latest_per_key(
    app_env: dict[str, str],
) -> None:
    store = PostgresFlowStore(app_env["database_url"])
    older = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    newer = older + timedelta(minutes=10)

    store.save_operational_metric_samples(
        [
            OperationalMetricSample(
                metric_key="account_invalid",
                value=1,
                observed_at=older,
                collected_at=older,
                snapshot={"payload": "x" * 200},
            ),
            OperationalMetricSample(
                metric_key="account_invalid",
                value=2,
                observed_at=newer,
                collected_at=newer,
                snapshot={"payload": "y" * 200},
            ),
            OperationalMetricSample(
                metric_key="user_balance_low",
                value=3,
                observed_at=newer,
                collected_at=newer,
                snapshot={"payload": "z" * 200},
            ),
        ]
    )
    store.save_operational_data_snapshot(
        OperationalDataSnapshot(
            source_key="accounts",
            observed_at=older,
            collected_at=older,
            payload=[{"id": "older", "payload": "a" * 200}],
        )
    )
    store.save_operational_data_snapshot(
        OperationalDataSnapshot(
            source_key="accounts",
            observed_at=newer,
            collected_at=newer,
            payload=[{"id": "newer", "payload": "b" * 200}],
        )
    )

    result = store.cleanup_operational_data(max_storage_bytes=1)

    assert result.deleted_metric_samples == 1
    assert result.deleted_snapshots == 1
    assert store.get_latest_operational_metric_sample("account_invalid").value == 2
    assert store.get_latest_operational_metric_sample("user_balance_low").value == 3
    assert store.get_latest_operational_data_snapshot("accounts").payload[0]["id"] == "newer"


def test_postgres_store_upserts_operational_source_status(app_env: dict[str, str]) -> None:
    store = PostgresFlowStore(app_env["database_url"])
    started_at = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)

    store.save_operational_data_source_status(
        OperationalDataSourceStatus(
            source_key="accounts",
            status="failed",
            started_at=started_at,
            finished_at=started_at,
            error_message="timeout",
        )
    )
    store.save_operational_data_source_status(
        OperationalDataSourceStatus(
            source_key="accounts",
            status="succeeded",
            started_at=started_at + timedelta(minutes=1),
            finished_at=started_at + timedelta(minutes=1),
            item_count=3,
        )
    )

    statuses = store.list_operational_data_source_statuses()

    assert len(statuses) == 1
    assert statuses[0].source_key == "accounts"
    assert statuses[0].status == "succeeded"
    assert statuses[0].error_message is None
    assert statuses[0].item_count == 3


def test_postgres_store_persists_runtime_settings(app_env: dict[str, str]) -> None:
    store = PostgresFlowStore(app_env["database_url"])
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)

    store.save_operational_data_runtime_settings(
        OperationalDataRuntimeSettings(
            enabled=False,
            collect_interval_seconds=60,
            expiration=None,
            retention_seconds=None,
            max_storage_mb=None,
            created_at=now,
            updated_at=now,
        )
    )
    store.save_operational_data_runtime_settings(
        OperationalDataRuntimeSettings(
            enabled=True,
            collect_interval_seconds=45,
            expiration=180,
            retention_seconds=3600,
            max_storage_mb=128,
            created_at=now,
            updated_at=now + timedelta(minutes=1),
        )
    )
    store.save_credit_control_runtime_settings(
        CreditControlRuntimeSettings(
            enabled=False,
            created_at=now,
            updated_at=now,
        )
    )
    store.save_provisioning_runtime_settings(
        ProvisioningRuntimeSettings(
            assignment_mode=AssignmentMode.managed_pool,
            created_at=now,
            updated_at=now,
        )
    )

    operational = store.get_operational_data_runtime_settings()
    credit = store.get_credit_control_runtime_settings()
    provisioning = store.get_provisioning_runtime_settings()

    assert operational is not None
    assert operational.enabled is True
    assert operational.collect_interval_seconds == 45
    assert operational.expiration == 180
    assert operational.retention_seconds == 3600
    assert operational.max_storage_mb == 128
    assert credit is not None
    assert credit.enabled is False
    assert provisioning is not None
    assert provisioning.assignment_mode == AssignmentMode.managed_pool
