from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.models.flow import FlowStatus, ProvisionFlow
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
