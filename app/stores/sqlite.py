from __future__ import annotations

import sqlite3
from pathlib import Path

from app.models.flow import ProvisionFlow
from app.stores.base import FlowStore


class SQLiteFlowStore(FlowStore):
    """SQLite-backed flow store used by default for durable local persistence."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._use_uri = database_path.startswith("file:")
        self._prepare_database_path()
        self._initialize_schema()

    def save(self, flow: ProvisionFlow) -> ProvisionFlow:
        payload = flow.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO provision_flows (
                    flow_id,
                    state,
                    email,
                    status,
                    payload,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(flow_id) DO UPDATE SET
                    state = excluded.state,
                    email = excluded.email,
                    status = excluded.status,
                    payload = excluded.payload,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    flow.flow_id,
                    flow.state,
                    flow.email,
                    flow.status.value,
                    payload,
                    flow.created_at.isoformat(),
                    flow.updated_at.isoformat(),
                ),
            )
            connection.commit()
        return flow

    def get_by_flow_id(self, flow_id: str) -> ProvisionFlow | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM provision_flows WHERE flow_id = ?",
                (flow_id,),
            ).fetchone()
        if not row:
            return None
        return ProvisionFlow.model_validate_json(row["payload"])

    def get_by_state(self, state: str) -> ProvisionFlow | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM provision_flows WHERE state = ?",
                (state,),
            ).fetchone()
        if not row:
            return None
        return ProvisionFlow.model_validate_json(row["payload"])

    def update(self, flow: ProvisionFlow) -> ProvisionFlow:
        return self.save(flow)

    def _prepare_database_path(self) -> None:
        if self.database_path == ":memory:" or self._use_uri:
            return
        Path(self.database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provision_flows (
                    flow_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_provision_flows_state ON provision_flows(state)"
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, uri=self._use_uri)
        connection.row_factory = sqlite3.Row
        return connection
