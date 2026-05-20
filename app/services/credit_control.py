from __future__ import annotations

import logging
import psycopg
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.clients.sub2api import Sub2APIClient, Sub2APIError
from app.models.credit import (
    CreditAdjustmentOutcome,
    CreditAuditOperation,
    CreditAuditRecord,
    CreditBalanceOperation,
    CreditOutcomeStatus,
    CreditRechargePolicy,
    CreditRechargeRunRecord,
    CreditRechargeSchedule,
    CreditRunStatus,
    CreditScheduleKind,
    CreditTargetScope,
    CreditTargetScopeKind,
    CreditUsageWindow,
    CreditUserSnapshot,
)
from app.models.usage_segmentation import UsageSegment, UserUsageSegmentRecord
from app.services.operational_data import (
    SOURCE_USER_API_KEYS,
    SOURCE_USER_USAGE,
    SOURCE_USERS,
)
from app.stores.postgres import PostgresFlowStore

logger = logging.getLogger(__name__)

SECRET_MARKER = "***REDACTED***"
SENSITIVE_KEYS = {
    "api_key",
    "access_key",
    "authorization",
    "bearer",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
}


class CreditControlError(Exception):
    """Raised when credit-control validation or execution fails."""


class CreditControlService:
    def __init__(self, store: PostgresFlowStore, sub2api_client: Sub2APIClient) -> None:
        self.store = store
        self.sub2api_client = sub2api_client

    def list_users(
        self,
        *,
        usage_window: CreditUsageWindow = CreditUsageWindow.window_1d,
        search: str | None = None,
        status: str | None = None,
        group_id: Any | None = None,
        balance_min: float | None = None,
        balance_max: float | None = None,
        consumption_min: float | None = None,
        consumption_max: float | None = None,
        usage_segment: UsageSegment | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[CreditUserSnapshot], int, dict[str, Any]]:
        all_items = self._load_user_snapshots(usage_window)
        filtered = [
            item
            for item in all_items
            if self._matches_user_filters(
                item,
                search=search,
                status=status,
                group_id=group_id,
                balance_min=balance_min,
                balance_max=balance_max,
                consumption_min=consumption_min,
                consumption_max=consumption_max,
                usage_segment=usage_segment,
            )
        ]
        total = len(filtered)
        paged = filtered[offset : offset + limit]
        aggregates = self._aggregate_users(filtered)
        return paged, total, aggregates

    def preview_filter_target(
        self,
        *,
        usage_window: CreditUsageWindow = CreditUsageWindow.window_1d,
        search: str | None = None,
        status: str | None = None,
        group_id: Any | None = None,
        balance_min: float | None = None,
        balance_max: float | None = None,
        consumption_min: float | None = None,
        consumption_max: float | None = None,
        usage_segment: UsageSegment | None = None,
    ) -> list[CreditUserSnapshot]:
        all_items = self._load_user_snapshots(usage_window)
        return [
            item
            for item in all_items
            if self._matches_user_filters(
                item,
                search=search,
                status=status,
                group_id=group_id,
                balance_min=balance_min,
                balance_max=balance_max,
                consumption_min=consumption_min,
                consumption_max=consumption_max,
                usage_segment=usage_segment,
            )
        ]

    def get_user_detail(
        self,
        user_id: Any,
        *,
        usage_window: CreditUsageWindow = CreditUsageWindow.window_1d,
    ) -> tuple[CreditUserSnapshot, list[CreditAuditRecord]]:
        for item in self._load_user_snapshots(usage_window):
            if str(item.user_id) == str(user_id):
                return item, self.store.list_credit_audit_records(user_id=user_id, limit=20)
        raise CreditControlError("User not found")

    def get_user_api_keys(
        self,
        user_id: Any,
    ) -> list[dict[str, Any]]:
        payload = self._latest_snapshot_payload(SOURCE_USER_API_KEYS, default={})
        if not isinstance(payload, dict):
            return []
        api_keys_payload = payload.get(str(user_id))
        if not isinstance(api_keys_payload, dict):
            return []
        items = api_keys_payload.get("items")
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def preview_adjustment(
        self,
        *,
        target_scope: CreditTargetScope,
        amount: float,
        operation: CreditBalanceOperation,
        reason: str,
        actor: str | None,
        usage_window: CreditUsageWindow = CreditUsageWindow.window_1d,
    ) -> CreditRechargeRunRecord:
        self._validate_adjustment(amount=amount, reason=reason, target_scope=target_scope)
        targets = self.resolve_target_scope(target_scope, usage_window=usage_window)
        if not targets:
            raise CreditControlError("target set is empty")
        outcomes = [
            CreditAdjustmentOutcome(
                user_id=user.user_id,
                email=user.email,
                status=CreditOutcomeStatus.planned,
                operation=operation,
                amount=amount,
                balance_before=user.balance,
                balance_after=self._planned_balance(user.balance, operation, amount),
            )
            for user in targets
        ]
        return self._build_run_record(
            operation_type=CreditAuditOperation.manual_adjustment,
            status=CreditRunStatus.planned if outcomes else CreditRunStatus.skipped,
            dry_run=True,
            amount=amount,
            target_scope=target_scope,
            reason=reason,
            actor=actor,
            outcomes=outcomes,
        )

    def execute_adjustment(
        self,
        *,
        target_scope: CreditTargetScope,
        amount: float,
        operation: CreditBalanceOperation,
        reason: str,
        actor: str | None,
    ) -> CreditRechargeRunRecord:
        self._validate_adjustment(amount=amount, reason=reason, target_scope=target_scope)
        targets = self.resolve_target_scope(target_scope)
        if not targets:
            raise CreditControlError("target set is empty")
        record = self._execute_outcomes(
            targets=targets,
            amount=amount,
            operation=operation,
            operation_type=CreditAuditOperation.manual_adjustment,
            target_scope=target_scope,
            reason=reason,
            actor=actor,
        )
        self.store.save_credit_run(record)
        self._audit_run(record)
        return record

    def preview_adjustment_for_users(
        self,
        *,
        users: list[CreditUserSnapshot],
        target_scope: CreditTargetScope,
        amount: float,
        operation: CreditBalanceOperation,
        reason: str,
        actor: str | None,
    ) -> CreditRechargeRunRecord:
        self._validate_adjustment(amount=amount, reason=reason, target_scope=target_scope)
        if not users:
            raise CreditControlError("target set is empty")
        outcomes = [
            CreditAdjustmentOutcome(
                user_id=user.user_id,
                email=user.email,
                status=CreditOutcomeStatus.planned,
                operation=operation,
                amount=amount,
                balance_before=user.balance,
                balance_after=self._planned_balance(user.balance, operation, amount),
            )
            for user in users
        ]
        return self._build_run_record(
            operation_type=CreditAuditOperation.manual_adjustment,
            status=CreditRunStatus.planned if outcomes else CreditRunStatus.skipped,
            dry_run=True,
            amount=amount,
            target_scope=target_scope,
            reason=reason,
            actor=actor,
            outcomes=outcomes,
        )

    def execute_adjustment_for_users(
        self,
        *,
        users: list[CreditUserSnapshot],
        target_scope: CreditTargetScope,
        amount: float,
        operation: CreditBalanceOperation,
        reason: str,
        actor: str | None,
    ) -> CreditRechargeRunRecord:
        self._validate_adjustment(amount=amount, reason=reason, target_scope=target_scope)
        if not users:
            raise CreditControlError("target set is empty")
        record = self._execute_outcomes(
            targets=users,
            amount=amount,
            operation=operation,
            operation_type=CreditAuditOperation.manual_adjustment,
            target_scope=target_scope,
            reason=reason,
            actor=actor,
        )
        self.store.save_credit_run(record)
        self._audit_run(record)
        return record

    def list_policies(self) -> list[CreditRechargePolicy]:
        return self.store.list_credit_policies()

    def get_policy(self, policy_id: str) -> CreditRechargePolicy:
        policy = self.store.get_credit_policy(policy_id)
        if policy is None:
            raise CreditControlError("Recharge policy not found")
        return policy

    def save_policy(
        self,
        policy: CreditRechargePolicy,
        *,
        actor: str | None,
        previous: CreditRechargePolicy | None = None,
    ) -> CreditRechargePolicy:
        self._validate_policy(policy)
        now = datetime.now(timezone.utc)
        policy.updated_at = now
        policy.next_run_at = (
            self._next_run_at(policy.schedule, from_time=now) if policy.enabled else None
        )
        saved = self.store.save_credit_policy(policy)
        operation = CreditAuditOperation.policy_created
        if previous is not None:
            if previous.enabled is not saved.enabled:
                operation = (
                    CreditAuditOperation.policy_enabled
                    if saved.enabled
                    else CreditAuditOperation.policy_disabled
                )
            else:
                operation = CreditAuditOperation.policy_updated
        self.store.save_credit_audit(
            CreditAuditRecord(
                operation_type=operation,
                status="succeeded",
                policy_id=saved.policy_id,
                actor=actor,
                summary=f"policy {operation.value}: {saved.name}",
                details={
                    "before": self._policy_summary(previous) if previous else None,
                    "after": self._policy_summary(saved),
                },
            )
        )
        return saved

    def delete_policy(self, policy_id: str, *, actor: str | None) -> None:
        existing = self.get_policy(policy_id)
        self.store.delete_credit_policy(policy_id)
        self.store.save_credit_audit(
            CreditAuditRecord(
                operation_type=CreditAuditOperation.policy_deleted,
                status="succeeded",
                policy_id=policy_id,
                actor=actor,
                summary=f"policy deleted: {existing.name}",
                details={"before": self._policy_summary(existing)},
            )
        )

    def preview_policy(self, policy: CreditRechargePolicy) -> CreditRechargeRunRecord:
        self._validate_policy(policy)
        targets = self.resolve_target_scope(policy.target_scope)
        outcomes = [
            CreditAdjustmentOutcome(
                user_id=user.user_id,
                email=user.email,
                status=CreditOutcomeStatus.planned,
                operation=CreditBalanceOperation.add,
                amount=policy.amount,
                balance_before=user.balance,
                balance_after=self._planned_balance(user.balance, CreditBalanceOperation.add, policy.amount),
            )
            for user in targets
        ]
        return self._build_run_record(
            policy=policy,
            operation_type=CreditAuditOperation.automatic_recharge,
            status=CreditRunStatus.planned if outcomes else CreditRunStatus.skipped,
            dry_run=True,
            amount=policy.amount,
            target_scope=policy.target_scope,
            reason=policy.reason_template,
            actor=None,
            scheduled_for=policy.next_run_at or policy.schedule.start_at,
            outcomes=outcomes,
        )

    def run_policy_now(self, policy_id: str, *, actor: str | None) -> CreditRechargeRunRecord:
        policy = self.get_policy(policy_id)
        return self._execute_policy(policy, scheduled_for=datetime.now(timezone.utc), actor=actor)

    def tick(self) -> list[CreditRechargeRunRecord]:
        now = datetime.now(timezone.utc)
        records: list[CreditRechargeRunRecord] = []
        for policy in self.store.list_due_credit_policies(now):
            catch_up_guard = 0
            while policy.enabled and policy.next_run_at is not None and policy.next_run_at <= now:
                scheduled_for = policy.next_run_at
                occurrence_key = self._occurrence_key(policy, scheduled_for)
                if self.store.get_credit_run_by_occurrence(policy.policy_id, occurrence_key):
                    self._advance_policy_after_occurrence(policy, scheduled_for)
                else:
                    records.append(
                        self._execute_policy(
                            policy,
                            scheduled_for=scheduled_for,
                            actor="scheduler",
                            catch_up=False,
                        )
                    )
                catch_up_guard += 1
                if catch_up_guard >= 100:
                    logger.warning(
                        "Stopped credit-control catch-up after 100 occurrences for policy_id=%s",
                        policy.policy_id,
                    )
                    break
        return records

    def list_runs(
        self, *, policy_id: str | None = None, limit: int = 50
    ) -> list[CreditRechargeRunRecord]:
        return self.store.list_credit_runs(policy_id=policy_id, limit=limit)

    def list_audit_records(
        self,
        *,
        user_id: Any | None = None,
        policy_id: str | None = None,
        run_id: str | None = None,
        operation_type: CreditAuditOperation | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[CreditAuditRecord]:
        return self.store.list_credit_audit_records(
            user_id=user_id,
            policy_id=policy_id,
            run_id=run_id,
            operation_type=operation_type,
            status=status,
            limit=limit,
        )

    def resolve_target_scope(
        self,
        target_scope: CreditTargetScope,
        *,
        usage_window: CreditUsageWindow = CreditUsageWindow.window_1d,
    ) -> list[CreditUserSnapshot]:
        users = self._load_user_snapshots(usage_window)
        if target_scope.kind == CreditTargetScopeKind.all_users:
            return users
        if target_scope.kind == CreditTargetScopeKind.explicit_user_ids:
            ids = {str(user_id) for user_id in target_scope.user_ids}
            matched = [user for user in users if str(user.user_id) in ids]
            if len(matched) != len(ids):
                matched_ids = {str(user.user_id) for user in matched}
                missing = sorted(ids - matched_ids)
                raise CreditControlError(f"target users not found: {', '.join(missing)}")
            return matched
        if target_scope.kind == CreditTargetScopeKind.group_ids:
            group_ids = {str(group_id) for group_id in target_scope.group_ids}
            return [
                user
                for user in users
                if any(str(user_group_id) in group_ids for user_group_id in user.group_ids)
            ]
        if target_scope.kind == CreditTargetScopeKind.balance_threshold:
            if target_scope.balance_below is None:
                raise CreditControlError("balance_below is required for balance threshold scope")
            return [
                user
                for user in users
                if user.balance is not None and user.balance < target_scope.balance_below
            ]
        raise CreditControlError("Unsupported target scope")

    def _execute_policy(
        self,
        policy: CreditRechargePolicy,
        *,
        scheduled_for: datetime,
        actor: str | None,
        catch_up: bool = True,
    ) -> CreditRechargeRunRecord:
        occurrence_key = self._occurrence_key(policy, scheduled_for)
        existing = self.store.get_credit_run_by_occurrence(policy.policy_id, occurrence_key)
        if existing is not None:
            return existing
        placeholder = self._build_run_record(
            policy=policy,
            occurrence_key=occurrence_key,
            operation_type=CreditAuditOperation.automatic_recharge,
            status=CreditRunStatus.planned,
            dry_run=False,
            amount=policy.amount,
            target_scope=policy.target_scope,
            reason=policy.reason_template,
            actor=actor,
            scheduled_for=scheduled_for,
            outcomes=[],
        )
        try:
            self.store.save_credit_run(placeholder)
        except psycopg.IntegrityError:
            existing = self.store.get_credit_run_by_occurrence(policy.policy_id, occurrence_key)
            if existing is not None:
                return existing
            raise
        targets = self.resolve_target_scope(policy.target_scope)
        record = self._execute_outcomes(
            targets=targets,
            amount=policy.amount,
            operation=CreditBalanceOperation.add,
            operation_type=CreditAuditOperation.automatic_recharge,
            target_scope=policy.target_scope,
            reason=policy.reason_template,
            actor=actor,
            policy=policy,
            scheduled_for=scheduled_for,
            occurrence_key=occurrence_key,
        )
        record.run_id = placeholder.run_id
        record.created_at = placeholder.created_at
        record.started_at = placeholder.started_at
        self.store.save_credit_run(record)
        self._audit_run(record)
        self._advance_policy_after_occurrence(policy, scheduled_for, catch_up=catch_up)
        return record

    def _execute_outcomes(
        self,
        *,
        targets: list[CreditUserSnapshot],
        amount: float,
        operation: CreditBalanceOperation,
        operation_type: CreditAuditOperation,
        target_scope: CreditTargetScope,
        reason: str,
        actor: str | None,
        policy: CreditRechargePolicy | None = None,
        scheduled_for: datetime | None = None,
        occurrence_key: str | None = None,
    ) -> CreditRechargeRunRecord:
        outcomes: list[CreditAdjustmentOutcome] = []
        for user in targets:
            try:
                response = self.sub2api_client.update_user_balance(
                    user_id=user.user_id,
                    amount=amount,
                    operation=operation.value,
                    notes=reason,
                )
                balance_after = response.get("balance")
                outcomes.append(
                    CreditAdjustmentOutcome(
                        user_id=user.user_id,
                        email=user.email,
                        status=CreditOutcomeStatus.succeeded,
                        operation=operation,
                        amount=amount,
                        balance_before=user.balance,
                        balance_after=balance_after if balance_after is not None else self._planned_balance(user.balance, operation, amount),
                    )
                )
            except Sub2APIError as exc:
                outcomes.append(
                    CreditAdjustmentOutcome(
                        user_id=user.user_id,
                        email=user.email,
                        status=CreditOutcomeStatus.failed,
                        operation=operation,
                        amount=amount,
                        balance_before=user.balance,
                        error_message=str(exc),
                    )
                )
        status = self._run_status(outcomes)
        return self._build_run_record(
            policy=policy,
            occurrence_key=occurrence_key,
            operation_type=operation_type,
            status=status,
            dry_run=False,
            amount=amount,
            target_scope=target_scope,
            reason=reason,
            actor=actor,
            scheduled_for=scheduled_for,
            outcomes=outcomes,
        )

    def _build_run_record(
        self,
        *,
        operation_type: CreditAuditOperation,
        status: CreditRunStatus,
        dry_run: bool,
        amount: float,
        target_scope: CreditTargetScope,
        reason: str,
        actor: str | None,
        outcomes: list[CreditAdjustmentOutcome],
        policy: CreditRechargePolicy | None = None,
        scheduled_for: datetime | None = None,
        occurrence_key: str | None = None,
    ) -> CreditRechargeRunRecord:
        success_count = sum(1 for item in outcomes if item.status == CreditOutcomeStatus.succeeded)
        skipped_count = sum(1 for item in outcomes if item.status == CreditOutcomeStatus.skipped)
        failure_count = sum(1 for item in outcomes if item.status == CreditOutcomeStatus.failed)
        now = datetime.now(timezone.utc)
        return CreditRechargeRunRecord(
            policy_id=policy.policy_id if policy else None,
            policy_name=policy.name if policy else None,
            occurrence_key=occurrence_key,
            operation_type=operation_type,
            status=status,
            dry_run=dry_run,
            amount=amount,
            target_scope=target_scope,
            reason=reason,
            actor=actor,
            scheduled_for=scheduled_for,
            finished_at=now,
            target_count=len(outcomes),
            success_count=success_count,
            skipped_count=skipped_count,
            failure_count=failure_count,
            outcomes=outcomes,
            updated_at=now,
        )

    def _audit_run(self, record: CreditRechargeRunRecord) -> None:
        details = self._redact(
            {
                "amount": record.amount,
                "reason": record.reason,
                "target_scope": record.target_scope.model_dump(mode="json"),
                "outcomes": [outcome.model_dump(mode="json") for outcome in record.outcomes],
            }
        )
        self.store.save_credit_audit(
            CreditAuditRecord(
                operation_type=record.operation_type,
                status=record.status.value,
                policy_id=record.policy_id,
                run_id=record.run_id,
                actor=record.actor,
                summary=f"{record.operation_type.value}: {record.status.value}",
                details=details,
            )
        )
        for outcome in record.outcomes:
            self.store.save_credit_audit(
                CreditAuditRecord(
                    operation_type=record.operation_type,
                    status=outcome.status.value,
                    user_id=outcome.user_id,
                    policy_id=record.policy_id,
                    run_id=record.run_id,
                    actor=record.actor,
                    summary=f"{record.operation_type.value} for {outcome.email}: {outcome.status.value}",
                    details=self._redact(
                        {**outcome.model_dump(mode="json"), "reason": record.reason}
                    ),
                )
            )

    def _load_user_snapshots(
        self, usage_window: CreditUsageWindow = CreditUsageWindow.window_1d
    ) -> list[CreditUserSnapshot]:
        users = self._latest_snapshot_payload(SOURCE_USERS, default=[])
        if not isinstance(users, list):
            users = []
        segments_by_user_id = {
            str(record.user_id): record
            for record in self.store.list_user_usage_segments(limit=100000)
        }
        snapshots: list[CreditUserSnapshot] = []
        for user in users:
            if not isinstance(user, dict):
                continue
            segment = segments_by_user_id.get(str(user.get("id")))
            usage = self._user_usage_from_snapshot(user["id"], usage_window)
            snapshots.append(
                CreditUserSnapshot(
                    user_id=user["id"],
                    email=str(user.get("email") or ""),
                    name=user.get("name"),
                    username=user.get("username"),
                    display_name=user.get("display_name"),
                    status=user.get("status"),
                    balance=user.get("balance"),
                    balance_display=user.get("balance_display"),
                    balance_unit=user.get("balance_unit"),
                    current_group_id=user.get("current_group_id"),
                    current_group_name=user.get("current_group_name"),
                    group_ids=list(user.get("group_ids") or []),
                    consumption=self._extract_consumption(usage),
                    usage_window=usage_window,
                    usage=self._redact(usage) if usage else {},
                    usage_segment=segment.segment.value if segment else None,
                    usage_segment_label=segment.segment_label if segment else None,
                    usage_profile=self._usage_profile(segment),
                )
            )
        return snapshots

    def _user_usage_from_snapshot(
        self,
        user_id: Any,
        usage_window: CreditUsageWindow,
    ) -> dict[str, Any]:
        usage_payload = self._latest_snapshot_payload(SOURCE_USER_USAGE, default={})
        if not isinstance(usage_payload, dict):
            return {}
        user_usage = usage_payload.get(str(user_id))
        if not isinstance(user_usage, dict):
            return {}
        usage = user_usage.get(usage_window.value)
        if not isinstance(usage, dict) or usage.get("error"):
            return {}
        return usage

    def _latest_snapshot_payload(self, source_key: str, *, default: Any) -> Any:
        snapshot = self.store.get_latest_operational_data_snapshot(source_key)
        if snapshot is None:
            return default
        return snapshot.payload

    def _extract_consumption(self, usage: dict[str, Any]) -> float | None:
        for key in (
            "total_cost",
            "total_actual_cost",
            "actual_cost",
            "cost",
            "usage",
            "amount",
        ):
            value = usage.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value)
                except ValueError:
                    continue
        return None

    def _matches_user_filters(
        self,
        item: CreditUserSnapshot,
        *,
        search: str | None,
        status: str | None,
        group_id: Any | None,
        balance_min: float | None,
        balance_max: float | None,
        consumption_min: float | None,
        consumption_max: float | None,
        usage_segment: UsageSegment | None,
    ) -> bool:
        if search:
            needle = search.lower()
            haystack = " ".join(
                str(value or "")
                for value in (item.user_id, item.email, item.name, item.username, item.display_name)
            ).lower()
            if needle not in haystack:
                return False
        if status and str(item.status or "") != status:
            return False
        if group_id is not None and str(item.current_group_id) != str(group_id):
            return False
        if balance_min is not None and (item.balance is None or item.balance < balance_min):
            return False
        if balance_max is not None and (item.balance is None or item.balance > balance_max):
            return False
        if consumption_min is not None and (
            item.consumption is None or item.consumption < consumption_min
        ):
            return False
        if consumption_max is not None and (
            item.consumption is None or item.consumption > consumption_max
        ):
            return False
        if usage_segment is not None and item.usage_segment != usage_segment.value:
            return False
        return True

    def _aggregate_users(self, items: list[CreditUserSnapshot]) -> dict[str, Any]:
        balances = [item.balance for item in items if item.balance is not None]
        consumption = [item.consumption for item in items if item.consumption is not None]
        segment_counts: dict[str, int] = {}
        for item in items:
            key = item.usage_segment or "unknown"
            segment_counts[key] = segment_counts.get(key, 0) + 1
        active_user_count = sum(
            1 for item in items if str(item.status or "").strip().lower() in {"active", "enabled", "ok"}
        )
        return {
            "user_count": len(items),
            "total_balance": sum(balances) if balances else None,
            "known_balance_count": len(balances),
            "average_balance": sum(balances) / len(balances) if balances else None,
            "negative_balance_count": sum(1 for balance in balances if balance < 0),
            "total_consumption": sum(consumption) if consumption else None,
            "known_consumption_count": len(consumption),
            "active_user_count": active_user_count,
            "segment_counts": segment_counts,
        }

    def _usage_profile(self, segment: UserUsageSegmentRecord | None) -> dict[str, Any]:
        if segment is None:
            return {}
        return self._redact(
            {
                "segment": segment.segment.value,
                "segment_label": segment.segment_label,
                "usage_by_window": segment.usage_by_window,
                "daily_average_by_window": segment.daily_average_by_window,
                "baseline_window": segment.baseline_window,
                "baseline_daily_average": segment.baseline_daily_average,
                "short_term_ratio": segment.short_term_ratio,
                "medium_term_ratio": segment.medium_term_ratio,
                "runway_days": segment.runway_days,
                "known_usage_window_count": segment.known_usage_window_count,
                "positive_usage_window_count": segment.positive_usage_window_count,
                "reasons": segment.reasons,
                "observed_at": segment.observed_at.isoformat(),
                "refreshed_at": segment.refreshed_at.isoformat(),
            }
        )

    def _validate_adjustment(
        self,
        *,
        amount: float,
        reason: str,
        target_scope: CreditTargetScope,
    ) -> None:
        if amount <= 0:
            raise CreditControlError("amount must be greater than zero")
        if not reason.strip():
            raise CreditControlError("reason is required")
        self._validate_target_scope(target_scope)

    def _validate_policy(self, policy: CreditRechargePolicy) -> None:
        if not policy.name.strip():
            raise CreditControlError("policy name is required")
        if policy.amount <= 0:
            raise CreditControlError("policy amount must be greater than zero")
        self._validate_target_scope(policy.target_scope)
        self._zone(policy.schedule.timezone)
        now = datetime.now(timezone.utc)
        if (
            policy.schedule.kind == CreditScheduleKind.once
            and policy.schedule.start_at.astimezone(timezone.utc) < now
        ):
            raise CreditControlError("one-time policy start time must be in the future")
        if policy.schedule.end_at and policy.schedule.end_at < policy.schedule.start_at:
            raise CreditControlError("policy end time must be after start time")

    def _validate_target_scope(self, target_scope: CreditTargetScope) -> None:
        if target_scope.kind == CreditTargetScopeKind.explicit_user_ids and not target_scope.user_ids:
            raise CreditControlError("user_ids is required for explicit user scope")
        if target_scope.kind == CreditTargetScopeKind.group_ids and not target_scope.group_ids:
            raise CreditControlError("group_ids is required for group scope")
        if (
            target_scope.kind == CreditTargetScopeKind.balance_threshold
            and target_scope.balance_below is None
        ):
            raise CreditControlError("balance_below is required for balance threshold scope")

    def _planned_balance(
        self,
        balance: float | None,
        operation: CreditBalanceOperation,
        amount: float,
    ) -> float | None:
        if balance is None:
            return None
        if operation == CreditBalanceOperation.add:
            return balance + amount
        return balance - amount

    def _run_status(self, outcomes: list[CreditAdjustmentOutcome]) -> CreditRunStatus:
        if not outcomes:
            return CreditRunStatus.skipped
        failures = sum(1 for item in outcomes if item.status == CreditOutcomeStatus.failed)
        successes = sum(1 for item in outcomes if item.status == CreditOutcomeStatus.succeeded)
        if failures and successes:
            return CreditRunStatus.partial_failed
        if failures:
            return CreditRunStatus.failed
        return CreditRunStatus.succeeded

    def _next_run_at(
        self,
        schedule: CreditRechargeSchedule,
        *,
        from_time: datetime,
    ) -> datetime | None:
        zone = self._zone(schedule.timezone)
        start = schedule.start_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=zone)
        current = start.astimezone(zone)
        reference = from_time.astimezone(zone)
        if schedule.kind == CreditScheduleKind.once:
            return current.astimezone(timezone.utc) if current >= reference else None

        while current < reference:
            if schedule.kind == CreditScheduleKind.daily:
                current += timedelta(days=1)
            elif schedule.kind == CreditScheduleKind.weekly:
                current += timedelta(days=7)
            elif schedule.kind == CreditScheduleKind.monthly:
                current = self._add_month(current)
            else:
                raise CreditControlError("unsupported schedule kind")
        if schedule.end_at and current > schedule.end_at.astimezone(zone):
            return None
        return current.astimezone(timezone.utc)

    def _advance_policy_after_occurrence(
        self,
        policy: CreditRechargePolicy,
        scheduled_for: datetime,
        *,
        catch_up: bool = False,
    ) -> None:
        now = datetime.now(timezone.utc)
        policy.last_run_at = scheduled_for
        if policy.schedule.kind == CreditScheduleKind.once:
            policy.next_run_at = None
            policy.enabled = False
        else:
            policy.next_run_at = self._next_run_at(
                policy.schedule,
                from_time=now if catch_up else scheduled_for + timedelta(seconds=1),
            )
        policy.updated_at = now
        self.store.save_credit_policy(policy)

    def _occurrence_key(self, policy: CreditRechargePolicy, scheduled_for: datetime) -> str:
        return f"{policy.policy_id}:{scheduled_for.astimezone(timezone.utc).isoformat()}"

    def _add_month(self, value: datetime) -> datetime:
        month = value.month + 1
        year = value.year
        if month > 12:
            month = 1
            year += 1
        day = min(
            value.day,
            29
            if month == 2 and year % 4 == 0
            else 28
            if month == 2
            else 30
            if month in {4, 6, 9, 11}
            else 31,
        )
        return value.replace(year=year, month=month, day=day)

    def _zone(self, timezone_name: str) -> ZoneInfo:
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise CreditControlError(f"unknown timezone: {timezone_name}") from exc

    def _policy_summary(self, policy: CreditRechargePolicy | None) -> dict[str, Any] | None:
        if policy is None:
            return None
        return self._redact(
            {
                "policy_id": policy.policy_id,
                "name": policy.name,
                "enabled": policy.enabled,
                "amount": policy.amount,
                "target_scope": policy.target_scope.model_dump(mode="json"),
                "schedule": policy.schedule.model_dump(mode="json"),
                "next_run_at": policy.next_run_at.isoformat() if policy.next_run_at else None,
            }
        )

    def _redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: SECRET_MARKER
                if any(marker in str(key).lower() for marker in SENSITIVE_KEYS)
                else self._redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value
