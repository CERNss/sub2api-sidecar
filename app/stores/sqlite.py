from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.models.flow import FlowStatus, ProvisionFlow
from app.models.rotation import RotationEvent, RotationPoolGroup, UserGroupAssignment
from app.stores.base import FlowStore


class SQLiteFlowStore(FlowStore):
    """SQLite-backed store for flows, rotation pool membership, assignments, and audit events."""

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
                    user_id_key,
                    group_id_key,
                    assignment_mode,
                    status,
                    payload,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(flow_id) DO UPDATE SET
                    state = excluded.state,
                    email = excluded.email,
                    user_id_key = excluded.user_id_key,
                    group_id_key = excluded.group_id_key,
                    assignment_mode = excluded.assignment_mode,
                    status = excluded.status,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    flow.flow_id,
                    flow.state,
                    flow.email,
                    self._serialize_key(flow.user_id),
                    self._serialize_key(flow.group_id),
                    flow.assignment_mode.value,
                    flow.status.value,
                    payload,
                    flow.created_at.isoformat(),
                    flow.updated_at.isoformat(),
                ),
            )
            connection.commit()
        return flow

    def get_by_flow_id(self, flow_id: str) -> ProvisionFlow | None:
        return self._load_single_flow(
            "SELECT payload FROM provision_flows WHERE flow_id = ?",
            (flow_id,),
        )

    def get_by_state(self, state: str) -> ProvisionFlow | None:
        return self._load_single_flow(
            "SELECT payload FROM provision_flows WHERE state = ?",
            (state,),
        )

    def get_pending_flow_by_user_id(self, user_id: Any) -> ProvisionFlow | None:
        return self._load_single_flow(
            """
            SELECT payload FROM provision_flows
            WHERE user_id_key = ? AND status = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (self._serialize_key(user_id), FlowStatus.pending_oauth.value),
        )

    def has_pending_flow_for_user(self, user_id: Any) -> bool:
        return self.get_pending_flow_by_user_id(user_id) is not None

    def update(self, flow: ProvisionFlow) -> ProvisionFlow:
        return self.save(flow)

    def upsert_rotation_pool_group(self, group: RotationPoolGroup) -> RotationPoolGroup:
        payload = group.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO rotation_pool_groups (
                    group_id_key,
                    priority,
                    group_name,
                    is_exclusive,
                    payload,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_id_key) DO UPDATE SET
                    priority = excluded.priority,
                    group_name = excluded.group_name,
                    is_exclusive = excluded.is_exclusive,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    self._serialize_key(group.group_id),
                    group.priority,
                    group.group_name,
                    1 if group.is_exclusive else 0,
                    payload,
                    group.created_at.isoformat(),
                    group.updated_at.isoformat(),
                ),
            )
            connection.commit()
        return group

    def get_rotation_pool_group(self, group_id: Any) -> RotationPoolGroup | None:
        return self._load_single_model(
            """
            SELECT payload FROM rotation_pool_groups
            WHERE group_id_key = ?
            """,
            (self._serialize_key(group_id),),
            RotationPoolGroup,
        )

    def list_rotation_pool_groups(self) -> list[RotationPoolGroup]:
        return self._load_many_models(
            """
            SELECT payload FROM rotation_pool_groups
            ORDER BY priority ASC, created_at ASC
            """,
            (),
            RotationPoolGroup,
        )

    def get_default_rotation_pool_group(self) -> RotationPoolGroup | None:
        groups = self.list_rotation_pool_groups()
        return groups[0] if groups else None

    def delete_rotation_pool_group(self, group_id: Any) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM rotation_pool_groups WHERE group_id_key = ?",
                (self._serialize_key(group_id),),
            )
            connection.commit()

    def upsert_user_assignment(self, assignment: UserGroupAssignment) -> UserGroupAssignment:
        payload = assignment.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO user_group_assignments (
                    user_id_key,
                    email,
                    group_id_key,
                    assignment_mode,
                    last_rotation_at,
                    has_api_keys,
                    payload,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id_key) DO UPDATE SET
                    email = excluded.email,
                    group_id_key = excluded.group_id_key,
                    assignment_mode = excluded.assignment_mode,
                    last_rotation_at = excluded.last_rotation_at,
                    has_api_keys = excluded.has_api_keys,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    self._serialize_key(assignment.user_id),
                    assignment.email,
                    self._serialize_key(assignment.current_group_id),
                    assignment.assignment_mode.value,
                    assignment.last_rotation_at.isoformat() if assignment.last_rotation_at else None,
                    self._serialize_optional_bool(assignment.has_api_keys),
                    payload,
                    assignment.created_at.isoformat(),
                    assignment.updated_at.isoformat(),
                ),
            )
            connection.commit()
        return assignment

    def get_user_assignment(self, user_id: Any) -> UserGroupAssignment | None:
        return self._load_single_model(
            """
            SELECT payload FROM user_group_assignments
            WHERE user_id_key = ?
            """,
            (self._serialize_key(user_id),),
            UserGroupAssignment,
        )

    def list_user_assignments(self) -> list[UserGroupAssignment]:
        return self._load_many_models(
            """
            SELECT payload FROM user_group_assignments
            ORDER BY updated_at DESC, created_at DESC
            """,
            (),
            UserGroupAssignment,
        )

    def save_rotation_event(self, event: RotationEvent) -> RotationEvent:
        payload = event.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO rotation_events (
                    event_id,
                    user_id_key,
                    status,
                    trigger_type,
                    payload,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    self._serialize_key(event.user_id),
                    event.status.value,
                    event.trigger_type.value,
                    payload,
                    event.created_at.isoformat(),
                    event.updated_at.isoformat(),
                ),
            )
            connection.commit()
        return event

    def list_rotation_events(self) -> list[RotationEvent]:
        return self._load_many_models(
            """
            SELECT payload FROM rotation_events
            ORDER BY created_at DESC
            """,
            (),
            RotationEvent,
        )

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
                    user_id_key TEXT,
                    group_id_key TEXT,
                    assignment_mode TEXT NOT NULL DEFAULT 'dedicated',
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(connection, "provision_flows", "user_id_key", "TEXT")
            self._ensure_column(connection, "provision_flows", "group_id_key", "TEXT")
            self._ensure_column(
                connection,
                "provision_flows",
                "assignment_mode",
                "TEXT NOT NULL DEFAULT 'dedicated'",
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_provision_flows_state ON provision_flows(state)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rotation_pool_groups (
                    group_id_key TEXT PRIMARY KEY,
                    priority INTEGER NOT NULL,
                    group_name TEXT NOT NULL,
                    is_exclusive INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_group_assignments (
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
                CREATE TABLE IF NOT EXISTS rotation_events (
                    event_id TEXT PRIMARY KEY,
                    user_id_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_group_assignments_group
                ON user_group_assignments(group_id_key)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rotation_events_user
                ON rotation_events(user_id_key, created_at DESC)
                """
            )
            connection.commit()

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing = {row["name"] for row in rows}
        if column_name in existing:
            return
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
        )

    def _load_single_flow(self, query: str, params: tuple[Any, ...]) -> ProvisionFlow | None:
        return self._load_single_model(query, params, ProvisionFlow)

    def _load_single_model(
        self,
        query: str,
        params: tuple[Any, ...],
        model_class: type[ProvisionFlow] | type[RotationPoolGroup] | type[UserGroupAssignment] | type[RotationEvent],
    ) -> Any:
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        if not row:
            return None
        return model_class.model_validate_json(row["payload"])

    def _load_many_models(
        self,
        query: str,
        params: tuple[Any, ...],
        model_class: type[RotationPoolGroup] | type[UserGroupAssignment] | type[RotationEvent],
    ) -> list[Any]:
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [model_class.model_validate_json(row["payload"]) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, uri=self._use_uri)
        connection.row_factory = sqlite3.Row
        return connection

    def _serialize_key(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))

    def _serialize_optional_bool(self, value: bool | None) -> int | None:
        if value is None:
            return None
        return 1 if value else 0
