from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from app.models.auth import PersistedAuthSession
from app.models.flow import AssignmentMode, FlowStatus, ProvisionEvent, ProvisionEventStatus, ProvisionEventType, ProvisionFlow
from app.models.group_usage import GroupUsageSegmentRecord
from app.models.operational_data import (
    CreditControlRuntimeSettings,
    OperationalDataRuntimeSettings,
    OperationalDataSnapshot,
    OperationalDataSourceStatus,
    OperationalMetricSample,
    ProvisioningRuntimeSettings,
)
from app.models.usage_segmentation import UsageSegment, UserUsageSegmentRecord
from app.models.rotation import RotationEvent, RotationPoolGroup, RotationResultStatus, RotationTrigger, UserGroupAssignment
from app.stores.postgres import PostgresFlowStore


def build_flow() -> ProvisionFlow:
    now = datetime.now(timezone.utc)
    return ProvisionFlow(
        flow_id="flow-1",
        upstream_id="main",
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
            platform="openai",
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
    assert groups[0].platform == "openai"
    assert second_store.get_rotation_pool_group(11) is not None
    assert second_store.get_rotation_pool_group("11") is not None
    second_store.delete_rotation_pool_group("11")
    assert second_store.get_rotation_pool_group(11) is None
    assert assignment is not None
    assert assignment.current_group_id == 11
    assert assignment.platform == "openai"
    assert len(events) == 1
    assert events[0].target_group_id == 22


def build_assignment(
    *,
    user_id: Any,
    platform: str,
    group_id: Any,
    group_name: str,
    email: str = "multi@example.com",
) -> UserGroupAssignment:
    now = datetime.now(timezone.utc)
    return UserGroupAssignment(
        user_id=user_id,
        platform=platform,
        email=email,
        current_group_id=group_id,
        current_group_name=group_name,
        created_at=now,
        updated_at=now,
    )


def test_postgres_store_keeps_one_assignment_per_user_and_platform(app_env: dict[str, str]) -> None:
    database_url = app_env["database_url"]
    store = PostgresFlowStore(database_url)
    store.upsert_user_assignment(
        build_assignment(user_id=101, platform="openai", group_id=11, group_name="openai-low")
    )
    store.upsert_user_assignment(
        build_assignment(user_id=101, platform="grok", group_id=22, group_name="grok-low")
    )

    reloaded = PostgresFlowStore(database_url)
    openai_assignment = reloaded.get_user_assignment(101, "openai")
    grok_assignment = reloaded.get_user_assignment(101, "grok")

    assert openai_assignment is not None
    assert grok_assignment is not None
    assert openai_assignment.current_group_id == 11
    assert openai_assignment.current_group_name == "openai-low"
    assert grok_assignment.current_group_id == 22
    assert grok_assignment.current_group_name == "grok-low"
    assert reloaded.get_user_assignment(101, "gemini") is None
    assert len(reloaded.list_user_assignments()) == 2


def test_postgres_store_overwrites_assignment_for_same_user_and_platform(app_env: dict[str, str]) -> None:
    database_url = app_env["database_url"]
    store = PostgresFlowStore(database_url)
    store.upsert_user_assignment(
        build_assignment(user_id=101, platform="openai", group_id=11, group_name="openai-low")
    )
    store.upsert_user_assignment(
        build_assignment(user_id=101, platform="grok", group_id=22, group_name="grok-low")
    )
    store.upsert_user_assignment(
        build_assignment(user_id=101, platform="openai", group_id=33, group_name="openai-high")
    )

    reloaded = PostgresFlowStore(database_url)
    openai_assignment = reloaded.get_user_assignment(101, "openai")
    grok_assignment = reloaded.get_user_assignment(101, "grok")

    assert openai_assignment is not None
    assert openai_assignment.current_group_id == 33
    assert openai_assignment.current_group_name == "openai-high"
    assert grok_assignment is not None
    assert grok_assignment.current_group_id == 22
    assert len(reloaded.list_user_assignments()) == 2


def test_postgres_store_lists_assignments_per_user(app_env: dict[str, str]) -> None:
    database_url = app_env["database_url"]
    store = PostgresFlowStore(database_url)
    store.upsert_user_assignment(
        build_assignment(user_id=101, platform="openai", group_id=11, group_name="openai-low")
    )
    store.upsert_user_assignment(
        build_assignment(user_id=101, platform="grok", group_id=22, group_name="grok-low")
    )
    store.upsert_user_assignment(
        build_assignment(
            user_id=202,
            platform="openai",
            group_id=33,
            group_name="openai-high",
            email="other@example.com",
        )
    )

    reloaded = PostgresFlowStore(database_url)
    for_user = reloaded.list_user_assignments(101)
    for_other_user = reloaded.list_user_assignments(202)

    assert [assignment.platform for assignment in for_user] == ["grok", "openai"]
    assert {assignment.current_group_id for assignment in for_user} == {11, 22}
    assert [assignment.current_group_id for assignment in for_other_user] == [33]
    assert len(reloaded.list_user_assignments()) == 3


def test_postgres_store_defaults_assignment_platform_for_legacy_callers(app_env: dict[str, str]) -> None:
    database_url = app_env["database_url"]
    store = PostgresFlowStore(database_url)
    now = datetime.now(timezone.utc)
    store.upsert_user_assignment(
        UserGroupAssignment(
            user_id=101,
            email="legacy@example.com",
            current_group_id=11,
            current_group_name="openai-low",
            created_at=now,
            updated_at=now,
        )
    )

    reloaded = PostgresFlowStore(database_url)
    assignment = reloaded.get_user_assignment(101)

    assert assignment is not None
    assert assignment.platform == "openai"


def test_postgres_store_rebuilds_legacy_assignment_table_without_platform(app_env: dict[str, str]) -> None:
    database_url = app_env["database_url"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP TABLE IF EXISTS user_group_assignments")
        connection.execute(
            """
            CREATE TABLE user_group_assignments (
                user_id_key TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                group_id_key TEXT NOT NULL,
                assignment_mode TEXT NOT NULL,
                last_rotation_at TEXT,
                has_api_keys INTEGER,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO user_group_assignments (
                user_id_key, email, group_id_key, assignment_mode, payload, created_at, updated_at
            ) VALUES ('101', 'legacy@example.com', '11', 'dedicated', '{}', 'x', 'x')
            """
        )

    store = PostgresFlowStore(database_url)

    with psycopg.connect(database_url, autocommit=True) as connection:
        columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = 'user_group_assignments'
                """
            ).fetchall()
        }
        primary_key_columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT attname FROM pg_index
                JOIN pg_attribute ON attrelid = indrelid AND attnum = ANY(indkey)
                WHERE indrelid = 'user_group_assignments'::regclass AND indisprimary
                """
            ).fetchall()
        }
        index_names = {
            row[0]
            for row in connection.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'user_group_assignments'"
            ).fetchall()
        }

    assert "platform" in columns
    assert primary_key_columns == {"user_id_key", "platform"}
    assert "idx_user_group_assignments_group" in index_names
    # The legacy row is intentionally discarded; bindings are re-derived from upstream.
    assert store.list_user_assignments() == []


def test_postgres_store_round_trips_rotation_pool_group_platform(app_env: dict[str, str]) -> None:
    database_url = app_env["database_url"]
    now = datetime.now(timezone.utc)
    store = PostgresFlowStore(database_url)
    store.upsert_rotation_pool_group(
        RotationPoolGroup(
            group_id=11,
            group_name="grok-low",
            platform="grok",
            is_exclusive=True,
            priority=0,
            created_at=now,
            updated_at=now,
        )
    )
    store.upsert_rotation_pool_group(
        RotationPoolGroup(
            group_id=22,
            group_name="no-platform",
            is_exclusive=True,
            priority=1,
            created_at=now,
            updated_at=now,
        )
    )

    reloaded = PostgresFlowStore(database_url)
    grok_group = reloaded.get_rotation_pool_group(11)
    platformless_group = reloaded.get_rotation_pool_group(22)

    assert grok_group is not None
    assert grok_group.platform == "grok"
    assert platformless_group is not None
    assert platformless_group.platform is None

    with psycopg.connect(database_url, autocommit=True) as connection:
        rows = connection.execute(
            "SELECT group_id_key, platform FROM rotation_pool_groups ORDER BY priority ASC"
        ).fetchall()
    assert [row[1] for row in rows] == ["grok", None]

    # The upstream platform must survive an in-place update of an existing pool row.
    store.upsert_rotation_pool_group(
        RotationPoolGroup(
            group_id=11,
            group_name="grok-low",
            platform="composite",
            is_exclusive=True,
            priority=0,
            created_at=now,
            updated_at=now,
        )
    )
    with psycopg.connect(database_url, autocommit=True) as connection:
        updated_platform = connection.execute(
            "SELECT platform FROM rotation_pool_groups WHERE group_id_key = %s",
            ('"11"',),
        ).fetchone()[0]
    assert updated_platform == "composite"
    assert PostgresFlowStore(database_url).get_rotation_pool_group(11).platform == "composite"


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


def test_postgres_store_persists_group_usage_segments(app_env: dict[str, str]) -> None:
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    store = PostgresFlowStore(app_env["database_url"])
    store.upsert_group_usage_segment(
        GroupUsageSegmentRecord(
            group_id=11,
            group_name="rotation-low",
            member_count=2,
            usage_by_window={"5h": 1.5, "1d": 4.0},
            daily_average_by_window={"5h": 7.2, "1d": 4.0},
            request_count_by_window={"5h": 2, "1d": 10},
            source_by_window={"5h": "usage_logs", "1d": "dashboard_groups"},
            observed_at=now,
            refreshed_at=now,
            created_at=now,
            updated_at=now,
        )
    )

    reloaded = PostgresFlowStore(app_env["database_url"])
    record = reloaded.get_group_usage_segment("11")
    records = reloaded.list_group_usage_segments()

    assert record is not None
    assert record.group_id == 11
    assert record.group_name == "rotation-low"
    assert record.member_count == 2
    assert record.usage_by_window["5h"] == 1.5
    assert record.source_by_window["1d"] == "dashboard_groups"
    assert reloaded.count_group_usage_segments() == 1
    assert [item.group_id for item in records] == [11]


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


def test_postgres_store_persists_and_revokes_auth_sessions(app_env: dict[str, str]) -> None:
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    store = PostgresFlowStore(app_env["database_url"])
    store.save_auth_session(
        PersistedAuthSession(
            access_key_hash="token-hash-1",
            username="admin",
            purpose="api_token",
            created_at=now,
            updated_at=now,
        )
    )
    store.save_auth_session(
        PersistedAuthSession(
            access_key_hash="token-hash-2",
            username="other",
            purpose="api_token",
            created_at=now,
            updated_at=now,
        )
    )
    store.save_auth_session(
        PersistedAuthSession(
            access_key_hash="session-hash-1",
            username="admin",
            purpose="external",
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=12),
        )
    )

    reloaded = PostgresFlowStore(app_env["database_url"])
    persisted = reloaded.get_auth_session("token-hash-1")
    revoked_count = reloaded.revoke_auth_sessions(username="admin", purpose="api_token")

    assert persisted is not None
    assert persisted.username == "admin"
    assert persisted.purpose == "api_token"
    assert revoked_count == 1
    assert reloaded.get_auth_session("token-hash-1") is None
    assert reloaded.get_auth_session("token-hash-2") is not None
    assert reloaded.get_auth_session("session-hash-1") is not None

    reloaded.revoke_auth_session("session-hash-1")

    assert reloaded.get_auth_session("session-hash-1") is None


def test_postgres_store_persists_latest_user_usage_segments(app_env: dict[str, str]) -> None:
    store = PostgresFlowStore(app_env["database_url"])
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)

    store.upsert_user_usage_segment(
        UserUsageSegmentRecord(
            user_id=101,
            email="rotate@example.com",
            segment=UsageSegment.active,
            segment_label="活跃",
            usage_by_window={"1d": 2.0},
            daily_average_by_window={"1d": 2.0},
            observed_at=now,
            refreshed_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    store.upsert_user_usage_segment(
        UserUsageSegmentRecord(
            user_id=101,
            email="rotate@example.com",
            segment=UsageSegment.heavy,
            segment_label="高频",
            usage_by_window={"1d": 8.0},
            daily_average_by_window={"1d": 8.0},
            observed_at=now + timedelta(minutes=1),
            refreshed_at=now + timedelta(minutes=1),
            created_at=now,
            updated_at=now + timedelta(minutes=1),
        )
    )

    record = store.get_user_usage_segment(101)
    records = store.list_user_usage_segments(segment=UsageSegment.heavy)
    counts = store.user_usage_segment_counts()

    assert record is not None
    assert record.segment == UsageSegment.heavy
    assert record.usage_by_window["1d"] == 8.0
    assert len(records) == 1
    assert counts == {"heavy": 1}


def test_postgres_store_shares_connection_pool_per_database_url(
    app_env: dict[str, str],
) -> None:
    database_url = app_env["database_url"]
    first_store = PostgresFlowStore(database_url)
    second_store = PostgresFlowStore(database_url)

    # Stores targeting the same database reuse one pool so the total number of
    # physical connections stays bounded regardless of how many stores exist.
    assert first_store._pool is second_store._pool


def test_postgres_store_handles_concurrent_access_within_pool_bounds(
    app_env: dict[str, str],
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    database_url = app_env["database_url"]
    # NOTE: the pool is shared per database URL, so sizing is fixed by whichever store
    # created it first (here, the app_env fixture). We assert against the pool's own
    # reported ceiling rather than assuming a value.
    store = PostgresFlowStore(database_url)

    def write_and_read(index: int) -> str | None:
        flow = build_flow()
        flow.flow_id = f"concurrent-{index}"
        flow.state = f"state-{index}"
        store.save(flow)
        reloaded = store.get_by_flow_id(f"concurrent-{index}")
        return reloaded.flow_id if reloaded else None

    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(write_and_read, range(40)))

    assert sorted(results) == sorted(f"concurrent-{index}" for index in range(40))

    # The pool never opens more physical connections than its configured ceiling,
    # even under more concurrent callers than max_size.
    stats = store._pool.get_stats()
    assert stats["pool_size"] <= stats["pool_max"]
