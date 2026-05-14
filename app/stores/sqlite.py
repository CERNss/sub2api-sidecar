from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.models.flow import AssignmentMode, FlowStatus, ProvisionEvent, ProvisionFlow
from app.models.credit import (
    CreditAuditOperation,
    CreditAuditRecord,
    CreditRechargePolicy,
    CreditRechargeRunRecord,
)
from app.models.notification import (
    NotificationDeliveryRecord,
    NotificationRuleState,
    NotificationSettings,
)
from app.models.operational_data import (
    OperationalDataSnapshot,
    OperationalDataSourceStatus,
    OperationalMetricSample,
)
from app.models.rotation import (
    AutoRotationRuntimeConfig,
    OrchestrationRunRecord,
    RotationEvent,
    RotationPoolGroup,
    RotationPoolKind,
    UserGroupAssignment,
)
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

    def list_flows(
        self,
        *,
        status: FlowStatus | None = None,
        assignment_mode: AssignmentMode | None = None,
        email: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ProvisionFlow]:
        where_clause, params = self._flow_filter_clause(
            status=status,
            assignment_mode=assignment_mode,
            email=email,
        )
        query = f"""
            SELECT payload FROM provision_flows
            {where_clause}
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ? OFFSET ?
        """
        return self._load_many_models(
            query,
            (*params, limit, offset),
            ProvisionFlow,
        )

    def count_flows(
        self,
        *,
        status: FlowStatus | None = None,
        assignment_mode: AssignmentMode | None = None,
        email: str | None = None,
    ) -> int:
        where_clause, params = self._flow_filter_clause(
            status=status,
            assignment_mode=assignment_mode,
            email=email,
        )
        query = f"SELECT COUNT(*) AS total FROM provision_flows {where_clause}"
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return int(row["total"] if row else 0)

    def update(self, flow: ProvisionFlow) -> ProvisionFlow:
        return self.save(flow)

    def save_provision_event(self, event: ProvisionEvent) -> ProvisionEvent:
        payload = event.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO provision_events (
                    event_id,
                    flow_id,
                    event_type,
                    status,
                    payload,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.flow_id,
                    event.event_type.value,
                    event.status.value,
                    payload,
                    event.created_at.isoformat(),
                    event.updated_at.isoformat(),
                ),
            )
            connection.commit()
        return event

    def list_provision_events(self, flow_id: str) -> list[ProvisionEvent]:
        return self._load_many_models(
            """
            SELECT payload FROM provision_events
            WHERE flow_id = ?
            ORDER BY created_at ASC, event_id ASC
            """,
            (flow_id,),
            ProvisionEvent,
        )

    def upsert_rotation_pool_group(self, group: RotationPoolGroup) -> RotationPoolGroup:
        payload = group.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO rotation_pool_groups (
                    pool_kind,
                    group_id_key,
                    priority,
                    group_name,
                    is_exclusive,
                    payload,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pool_kind, group_id_key) DO UPDATE SET
                    priority = excluded.priority,
                    group_name = excluded.group_name,
                    is_exclusive = excluded.is_exclusive,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    group.pool_kind.value,
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

    def get_rotation_pool_group(
        self,
        group_id: Any,
        pool_kind: RotationPoolKind = RotationPoolKind.rotation,
    ) -> RotationPoolGroup | None:
        return self._load_single_model(
            """
            SELECT payload FROM rotation_pool_groups
            WHERE pool_kind = ? AND group_id_key = ?
            """,
            (pool_kind.value, self._serialize_rotation_pool_group_id_key(group_id)),
            RotationPoolGroup,
        )

    def list_rotation_pool_groups(
        self,
        pool_kind: RotationPoolKind = RotationPoolKind.rotation,
    ) -> list[RotationPoolGroup]:
        return self._load_many_models(
            """
            SELECT payload FROM rotation_pool_groups
            WHERE pool_kind = ?
            ORDER BY priority ASC, created_at ASC
            """,
            (pool_kind.value,),
            RotationPoolGroup,
        )

    def get_default_rotation_pool_group(
        self,
        pool_kind: RotationPoolKind = RotationPoolKind.rotation,
    ) -> RotationPoolGroup | None:
        groups = self.list_rotation_pool_groups(pool_kind)
        return groups[0] if groups else None

    def delete_rotation_pool_group(
        self,
        group_id: Any,
        pool_kind: RotationPoolKind = RotationPoolKind.rotation,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM rotation_pool_groups WHERE pool_kind = ? AND group_id_key = ?",
                (pool_kind.value, self._serialize_rotation_pool_group_id_key(group_id)),
            )
            connection.commit()

    def get_auto_rotation_config(self) -> AutoRotationRuntimeConfig | None:
        return self._load_single_model(
            """
            SELECT payload FROM auto_rotation_config
            WHERE config_key = 'default'
            """,
            (),
            AutoRotationRuntimeConfig,
        )

    def save_auto_rotation_config(
        self,
        config: AutoRotationRuntimeConfig,
    ) -> AutoRotationRuntimeConfig:
        payload = config.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO auto_rotation_config (
                    config_key,
                    enabled,
                    usage_window,
                    payload,
                    created_at,
                    updated_at
                ) VALUES ('default', ?, ?, ?, ?, ?)
                ON CONFLICT(config_key) DO UPDATE SET
                    enabled = excluded.enabled,
                    usage_window = excluded.usage_window,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    1 if config.enabled else 0,
                    config.usage_window.value,
                    payload,
                    config.created_at.isoformat(),
                    config.updated_at.isoformat(),
                ),
            )
            connection.commit()
        return config

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

    def save_orchestration_run(
        self,
        record: OrchestrationRunRecord,
    ) -> OrchestrationRunRecord:
        payload = record.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO orchestration_runs (
                    run_id,
                    run_kind,
                    tag,
                    trigger_type,
                    status,
                    dry_run,
                    payload,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    run_kind = excluded.run_kind,
                    tag = excluded.tag,
                    trigger_type = excluded.trigger_type,
                    status = excluded.status,
                    dry_run = excluded.dry_run,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    record.run_id,
                    record.run_kind.value,
                    record.tag,
                    record.trigger_type.value,
                    record.status,
                    1 if record.dry_run else 0,
                    payload,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            connection.commit()
        return record

    def get_orchestration_run(self, run_id: str) -> OrchestrationRunRecord | None:
        return self._load_single_model(
            """
            SELECT payload FROM orchestration_runs
            WHERE run_id = ?
            """,
            (run_id,),
            OrchestrationRunRecord,
        )

    def list_orchestration_runs(self, limit: int = 50) -> list[OrchestrationRunRecord]:
        return self._load_many_models(
            """
            SELECT payload FROM orchestration_runs
            ORDER BY created_at DESC, run_id DESC
            LIMIT ?
            """,
            (limit,),
            OrchestrationRunRecord,
        )

    def get_notification_settings(self) -> NotificationSettings | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM notification_config WHERE config_key = 'default'"
            ).fetchone()
        if not row:
            return None
        return NotificationSettings.model_validate_json(row["payload"])

    def save_notification_settings(self, settings: NotificationSettings) -> NotificationSettings:
        now = datetime.now(timezone.utc).isoformat()
        payload = settings.model_dump_json(by_alias=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO notification_config (config_key, payload, created_at, updated_at)
                VALUES ('default', ?, ?, ?)
                ON CONFLICT(config_key) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (payload, now, now),
            )
            connection.commit()
        return settings

    def save_notification_delivery(
        self, record: NotificationDeliveryRecord
    ) -> NotificationDeliveryRecord:
        payload = record.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO notification_deliveries (
                    delivery_id, receiver_id, rule_id, provider, severity,
                    trigger, status, attempt_index, payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.delivery_id,
                    record.receiver_id,
                    record.rule_id,
                    record.provider.value,
                    record.severity.value,
                    record.trigger.value,
                    record.status.value,
                    record.attempt_index,
                    payload,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            connection.commit()
        return record

    def list_notification_deliveries(self, limit: int = 50) -> list[NotificationDeliveryRecord]:
        return self._load_many_models(
            """
            SELECT payload FROM notification_deliveries
            ORDER BY created_at DESC, delivery_id DESC
            LIMIT ?
            """,
            (limit,),
            NotificationDeliveryRecord,
        )

    def get_notification_rule_state(self, rule_id: str) -> NotificationRuleState | None:
        return self._load_single_model(
            "SELECT payload FROM notification_rule_states WHERE rule_id = ?",
            (rule_id,),
            NotificationRuleState,
        )

    def upsert_notification_rule_state(
        self, state: NotificationRuleState
    ) -> NotificationRuleState:
        payload = state.model_dump_json()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO notification_rule_states (rule_id, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(rule_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (state.rule_id, payload, state.created_at.isoformat(), now),
            )
            connection.commit()
        return state

    def save_operational_metric_samples(
        self, samples: list[OperationalMetricSample]
    ) -> list[OperationalMetricSample]:
        if not samples:
            return samples
        with self._connect() as connection:
            for sample in samples:
                connection.execute(
                    """
                    INSERT INTO operational_metric_samples (
                        metric_key, observed_at, collected_at, value, payload
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        sample.metric_key,
                        sample.observed_at.isoformat(),
                        sample.collected_at.isoformat(),
                        sample.value,
                        sample.model_dump_json(),
                    ),
                )
            connection.commit()
        return samples

    def get_latest_operational_metric_sample(
        self, metric_key: str
    ) -> OperationalMetricSample | None:
        return self._load_single_model(
            """
            SELECT payload FROM operational_metric_samples
            WHERE metric_key = ?
            ORDER BY observed_at DESC, collected_at DESC, sample_id DESC
            LIMIT 1
            """,
            (metric_key,),
            OperationalMetricSample,
        )

    def save_operational_data_snapshot(
        self, snapshot: OperationalDataSnapshot
    ) -> OperationalDataSnapshot:
        payload = snapshot.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO operational_data_snapshots (
                    source_key, observed_at, collected_at, payload
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    snapshot.source_key,
                    snapshot.observed_at.isoformat(),
                    snapshot.collected_at.isoformat(),
                    payload,
                ),
            )
            connection.commit()
        return snapshot

    def get_latest_operational_data_snapshot(
        self, source_key: str
    ) -> OperationalDataSnapshot | None:
        return self._load_single_model(
            """
            SELECT payload FROM operational_data_snapshots
            WHERE source_key = ?
            ORDER BY observed_at DESC, collected_at DESC, snapshot_id DESC
            LIMIT 1
            """,
            (source_key,),
            OperationalDataSnapshot,
        )

    def save_operational_data_source_status(
        self, status: OperationalDataSourceStatus
    ) -> OperationalDataSourceStatus:
        payload = status.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO operational_data_source_statuses (
                    source_key, status, started_at, finished_at, error_message,
                    item_count, payload, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    status = excluded.status,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    error_message = excluded.error_message,
                    item_count = excluded.item_count,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    status.source_key,
                    status.status,
                    status.started_at.isoformat(),
                    status.finished_at.isoformat() if status.finished_at else None,
                    status.error_message,
                    status.item_count,
                    payload,
                    status.updated_at.isoformat(),
                ),
            )
            connection.commit()
        return status

    def list_operational_data_source_statuses(
        self,
    ) -> list[OperationalDataSourceStatus]:
        return self._load_many_models(
            """
            SELECT payload FROM operational_data_source_statuses
            ORDER BY source_key ASC
            """,
            (),
            OperationalDataSourceStatus,
        )

    def save_credit_policy(self, policy: CreditRechargePolicy) -> CreditRechargePolicy:
        payload = policy.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO credit_recharge_policies (
                    policy_id, enabled, next_run_at, payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(policy_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    next_run_at = excluded.next_run_at,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    policy.policy_id,
                    1 if policy.enabled else 0,
                    policy.next_run_at.isoformat() if policy.next_run_at else None,
                    payload,
                    policy.created_at.isoformat(),
                    policy.updated_at.isoformat(),
                ),
            )
            connection.commit()
        return policy

    def get_credit_policy(self, policy_id: str) -> CreditRechargePolicy | None:
        return self._load_single_model(
            "SELECT payload FROM credit_recharge_policies WHERE policy_id = ?",
            (policy_id,),
            CreditRechargePolicy,
        )

    def list_credit_policies(self) -> list[CreditRechargePolicy]:
        return self._load_many_models(
            """
            SELECT payload FROM credit_recharge_policies
            ORDER BY updated_at DESC, created_at DESC
            """,
            (),
            CreditRechargePolicy,
        )

    def delete_credit_policy(self, policy_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM credit_recharge_policies WHERE policy_id = ?",
                (policy_id,),
            )
            connection.commit()

    def list_due_credit_policies(self, now: datetime) -> list[CreditRechargePolicy]:
        return self._load_many_models(
            """
            SELECT payload FROM credit_recharge_policies
            WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
            ORDER BY next_run_at ASC, policy_id ASC
            """,
            (now.isoformat(),),
            CreditRechargePolicy,
        )

    def save_credit_run(self, record: CreditRechargeRunRecord) -> CreditRechargeRunRecord:
        payload = record.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO credit_recharge_runs (
                    run_id, policy_id, occurrence_key, operation_type, status,
                    dry_run, payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    policy_id = excluded.policy_id,
                    occurrence_key = excluded.occurrence_key,
                    operation_type = excluded.operation_type,
                    status = excluded.status,
                    dry_run = excluded.dry_run,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    record.run_id,
                    record.policy_id,
                    record.occurrence_key,
                    record.operation_type.value,
                    record.status.value,
                    1 if record.dry_run else 0,
                    payload,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            connection.commit()
        return record

    def get_credit_run_by_occurrence(
        self, policy_id: str, occurrence_key: str
    ) -> CreditRechargeRunRecord | None:
        return self._load_single_model(
            """
            SELECT payload FROM credit_recharge_runs
            WHERE policy_id = ? AND occurrence_key = ? AND dry_run = 0
            """,
            (policy_id, occurrence_key),
            CreditRechargeRunRecord,
        )

    def list_credit_runs(
        self,
        *,
        policy_id: str | None = None,
        limit: int = 50,
    ) -> list[CreditRechargeRunRecord]:
        params: tuple[Any, ...]
        where = ""
        if policy_id:
            where = "WHERE policy_id = ?"
            params = (policy_id, limit)
        else:
            params = (limit,)
        return self._load_many_models(
            f"""
            SELECT payload FROM credit_recharge_runs
            {where}
            ORDER BY created_at DESC, run_id DESC
            LIMIT ?
            """,
            params,
            CreditRechargeRunRecord,
        )

    def save_credit_audit(self, record: CreditAuditRecord) -> CreditAuditRecord:
        payload = record.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO credit_audit_records (
                    audit_id, operation_type, status, user_id_key, policy_id,
                    run_id, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.audit_id,
                    record.operation_type.value,
                    record.status,
                    self._serialize_key(record.user_id) if record.user_id is not None else None,
                    record.policy_id,
                    record.run_id,
                    payload,
                    record.created_at.isoformat(),
                ),
            )
            connection.commit()
        return record

    def list_credit_audit_records(
        self,
        *,
        user_id: Any | None = None,
        policy_id: str | None = None,
        run_id: str | None = None,
        operation_type: CreditAuditOperation | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[CreditAuditRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            user_id_keys = self._serialize_lookup_keys(user_id)
            clauses.append(
                "user_id_key IN (" + ",".join("?" for _ in user_id_keys) + ")"
            )
            params.extend(user_id_keys)
        if policy_id:
            clauses.append("policy_id = ?")
            params.append(policy_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if operation_type is not None:
            clauses.append("operation_type = ?")
            params.append(operation_type.value)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        return self._load_many_models(
            f"""
            SELECT payload FROM credit_audit_records
            {where}
            ORDER BY created_at DESC, audit_id DESC
            LIMIT ?
            """,
            tuple(params),
            CreditAuditRecord,
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
                    pool_kind TEXT NOT NULL DEFAULT 'rotation',
                    group_id_key TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    group_name TEXT NOT NULL,
                    is_exclusive INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (pool_kind, group_id_key)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provision_events (
                    event_id TEXT PRIMARY KEY,
                    flow_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_provision_flows_list
                ON provision_flows(status, assignment_mode, updated_at DESC, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_provision_flows_email
                ON provision_flows(email)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_provision_events_flow
                ON provision_events(flow_id, created_at ASC)
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
                CREATE TABLE IF NOT EXISTS auto_rotation_config (
                    config_key TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL,
                    usage_window TEXT NOT NULL,
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
                CREATE TABLE IF NOT EXISTS orchestration_runs (
                    run_id TEXT PRIMARY KEY,
                    run_kind TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    dry_run INTEGER NOT NULL DEFAULT 0,
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
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_orchestration_runs_list
                ON orchestration_runs(created_at DESC, run_kind, tag)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_config (
                    config_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    receiver_id TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_index INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_notification_deliveries_list
                ON notification_deliveries(created_at DESC, receiver_id, rule_id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_rule_states (
                    rule_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operational_metric_samples (
                    sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric_key TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    value REAL NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_operational_metric_samples_latest
                ON operational_metric_samples(metric_key, observed_at DESC, collected_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operational_data_snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_operational_data_snapshots_latest
                ON operational_data_snapshots(source_key, observed_at DESC, collected_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operational_data_source_statuses (
                    source_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error_message TEXT,
                    item_count INTEGER,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS credit_recharge_policies (
                    policy_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL,
                    next_run_at TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_credit_recharge_policies_due
                ON credit_recharge_policies(enabled, next_run_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS credit_recharge_runs (
                    run_id TEXT PRIMARY KEY,
                    policy_id TEXT,
                    occurrence_key TEXT,
                    operation_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    dry_run INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_credit_recharge_runs_occurrence
                ON credit_recharge_runs(policy_id, occurrence_key)
                WHERE policy_id IS NOT NULL AND occurrence_key IS NOT NULL AND dry_run = 0
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_credit_recharge_runs_list
                ON credit_recharge_runs(created_at DESC, policy_id, status)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS credit_audit_records (
                    audit_id TEXT PRIMARY KEY,
                    operation_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    user_id_key TEXT,
                    policy_id TEXT,
                    run_id TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_credit_audit_records_list
                ON credit_audit_records(created_at DESC, operation_type, status)
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
        model_class: type[BaseModel],
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
        model_class: type[BaseModel],
    ) -> list[Any]:
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [model_class.model_validate_json(row["payload"]) for row in rows]

    def _flow_filter_clause(
        self,
        *,
        status: FlowStatus | None,
        assignment_mode: AssignmentMode | None,
        email: str | None,
    ) -> tuple[str, tuple[Any, ...]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if assignment_mode is not None:
            clauses.append("assignment_mode = ?")
            params.append(assignment_mode.value)
        if email:
            clauses.append("LOWER(email) LIKE ?")
            params.append(f"%{email.lower()}%")
        if not clauses:
            return "", ()
        return "WHERE " + " AND ".join(clauses), tuple(params)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, uri=self._use_uri)
        connection.row_factory = sqlite3.Row
        return connection

    def _serialize_key(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))

    def _serialize_lookup_keys(self, value: Any) -> tuple[str, ...]:
        keys = [self._serialize_key(value)]
        if isinstance(value, str):
            try:
                numeric_value = int(value)
            except ValueError:
                numeric_value = None
            if numeric_value is not None:
                keys.append(self._serialize_key(numeric_value))
        return tuple(dict.fromkeys(keys))

    def _serialize_rotation_pool_group_id_key(self, value: Any) -> str:
        return self._serialize_key(str(value))

    def _serialize_optional_bool(self, value: bool | None) -> int | None:
        if value is None:
            return None
        return 1 if value else 0
