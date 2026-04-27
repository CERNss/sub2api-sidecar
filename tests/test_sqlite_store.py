from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.models.flow import FlowStatus, ProvisionFlow
from app.models.rotation import RotationEvent, RotationPoolGroup, RotationResultStatus, RotationTrigger, UserGroupAssignment
from app.stores.sqlite import SQLiteFlowStore


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


def test_sqlite_store_initializes_schema_and_persists_across_instances(tmp_path: Path) -> None:
    db_path = tmp_path / "flows.db"

    first_store = SQLiteFlowStore(str(db_path))
    first_store.save(build_flow())

    second_store = SQLiteFlowStore(str(db_path))
    reloaded_by_flow_id = second_store.get_by_flow_id("flow-1")
    reloaded_by_state = second_store.get_by_state("state-1")

    assert reloaded_by_flow_id is not None
    assert reloaded_by_state is not None
    assert reloaded_by_flow_id.email == "user@example.com"
    assert reloaded_by_state.group_id == 456
    assert db_path.exists()

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'provision_flows'"
        ).fetchone()

    assert row is not None


def test_sqlite_store_updates_persisted_flow(tmp_path: Path) -> None:
    db_path = tmp_path / "flows.db"
    store = SQLiteFlowStore(str(db_path))
    flow = build_flow()
    store.save(flow)

    flow.status = FlowStatus.completed
    flow.oauth_account_id = "oa-1"
    flow.error_message = None
    flow.updated_at = datetime.now(timezone.utc)
    store.update(flow)

    reloaded_store = SQLiteFlowStore(str(db_path))
    persisted = reloaded_store.get_by_flow_id("flow-1")

    assert persisted is not None
    assert persisted.status == FlowStatus.completed
    assert persisted.oauth_account_id == "oa-1"


def test_sqlite_store_persists_rotation_pool_assignments_and_events(tmp_path: Path) -> None:
    db_path = tmp_path / "rotation.db"
    now = datetime.now(timezone.utc)
    first_store = SQLiteFlowStore(str(db_path))
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

    second_store = SQLiteFlowStore(str(db_path))
    groups = second_store.list_rotation_pool_groups()
    assignment = second_store.get_user_assignment(101)
    events = second_store.list_rotation_events()

    assert len(groups) == 1
    assert groups[0].group_id == 11
    assert assignment is not None
    assert assignment.current_group_id == 11
    assert len(events) == 1
    assert events[0].target_group_id == 22
