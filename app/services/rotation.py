from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.clients.sub2api import Sub2APIClient, Sub2APIError
from app.config import Settings
from app.errors import RotationExecutionError, RotationPoolEmptyError, RotationTargetValidationError
from app.models.flow import AssignmentMode
from app.models.rotation import (
    AutoRotationUsageWindow,
    RotationEvent,
    RotationPoolGroup,
    RotationResultStatus,
    RotationTrigger,
    UserGroupAssignment,
)
from app.stores.sqlite import SQLiteFlowStore

logger = logging.getLogger(__name__)


@dataclass
class RotationExecutionResult:
    user_id: Any
    email: str
    source_group_id: Any | None
    target_group_id: Any | None
    trigger_type: RotationTrigger
    status: RotationResultStatus
    reason: str
    migrated_keys: int = 0
    usage_window: AutoRotationUsageWindow | None = None
    usage_value: float | None = None
    usage_snapshot: dict[str, Any] | None = None


class RotationService:
    def __init__(
        self,
        store: SQLiteFlowStore,
        sub2api_client: Sub2APIClient,
        settings: Settings,
    ) -> None:
        self.store = store
        self.sub2api_client = sub2api_client
        self.settings = settings

    def list_pool_candidates(self) -> list[dict[str, Any]]:
        selected = {
            self._normalize_key(group.group_id): group
            for group in self.store.list_rotation_pool_groups()
        }
        groups = self.sub2api_client.list_groups(
            platform=self.settings.sub2api_provisioning_defaults.group_platform
        )
        candidates: list[dict[str, Any]] = []
        for group in groups:
            selected_group = selected.get(self._normalize_key(group["id"]))
            candidates.append(
                {
                    "group_id": group["id"],
                    "name": group["name"],
                    "platform": group.get("platform"),
                    "status": group.get("status"),
                    "is_exclusive": group.get("is_exclusive", False),
                    "selected": selected_group is not None,
                    "priority": selected_group.priority if selected_group else None,
                }
            )
        candidates.sort(
            key=lambda item: (
                0 if item["is_exclusive"] else 1,
                0 if item["selected"] else 1,
                item["priority"] if item["priority"] is not None else 999999,
                str(item["name"]),
            )
        )
        return candidates

    def add_group_to_pool(self, group_id: Any, priority: int | None = None) -> RotationPoolGroup:
        group = self._get_upstream_group(group_id)
        if not group["is_exclusive"]:
            raise RotationTargetValidationError("Only exclusive groups can be added to the rotation pool")

        now = datetime.now(timezone.utc)
        existing = self.store.get_rotation_pool_group(group_id)
        next_priority = priority if priority is not None else self._next_priority()
        pool_group = RotationPoolGroup(
            group_id=group["id"],
            group_name=group["name"],
            platform=group.get("platform"),
            status=group.get("status"),
            is_exclusive=True,
            priority=next_priority,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        return self.store.upsert_rotation_pool_group(pool_group)

    def remove_group_from_pool(self, group_id: Any) -> None:
        self.store.delete_rotation_pool_group(group_id)

    def manual_rotate(
        self,
        *,
        user_id: Any,
        target_group_id: Any,
        reason: str | None = None,
    ) -> RotationExecutionResult:
        assignment = self.store.get_user_assignment(user_id)
        if assignment is None:
            raise RotationExecutionError("No stored assignment found for the user")
        return self._execute_rotation(
            assignment=assignment,
            target_group_id=target_group_id,
            trigger_type=RotationTrigger.manual,
            reason=reason or "manual rotation request",
        )

    def run_auto_rotation(self, trigger_type: RotationTrigger = RotationTrigger.automatic_api) -> dict[str, Any]:
        if not self.settings.auto_rotation.enabled:
            raise RotationExecutionError("Automatic rotation is disabled")

        pool_groups = self.store.list_rotation_pool_groups()
        if not pool_groups:
            raise RotationPoolEmptyError("No rotation pool groups are available")
        self._validate_pool_threshold_mapping(pool_groups)

        assignments = self.store.list_user_assignments()
        ordered_candidates: list[tuple[UserGroupAssignment, dict[str, Any]]] = []
        for assignment in assignments:
            usage_snapshot = self._build_usage_snapshot(assignment.user_id)
            assignment.has_api_keys = usage_snapshot["has_api_keys"]
            assignment.updated_at = datetime.now(timezone.utc)
            self.store.upsert_user_assignment(assignment)
            ordered_candidates.append((assignment, usage_snapshot))

        ordered_candidates.sort(
            key=lambda item: (
                0 if item[1]["has_api_keys"] else 1,
                str(item[0].email).lower(),
                str(item[0].user_id),
            )
        )

        moved: list[RotationExecutionResult] = []
        skipped: list[RotationExecutionResult] = []
        failed: list[RotationExecutionResult] = []
        sorted_pool = sorted(pool_groups, key=lambda group: (group.priority, group.created_at))

        for assignment, usage_snapshot in ordered_candidates:
            target_group = self._select_target_group(sorted_pool, usage_snapshot["usage_value"])
            result = self._execute_rotation(
                assignment=assignment,
                target_group_id=target_group.group_id,
                trigger_type=trigger_type,
                reason=f"auto rotation window={usage_snapshot['usage_window']} usage={usage_snapshot['usage_value']}",
                usage_window=usage_snapshot["usage_window"],
                usage_value=usage_snapshot["usage_value"],
                usage_snapshot=usage_snapshot,
            )
            if result.status == RotationResultStatus.moved:
                moved.append(result)
            elif result.status == RotationResultStatus.skipped:
                skipped.append(result)
            else:
                failed.append(result)

        return {
            "window": self._window_enum().value,
            "moved": [self._serialize_result(result) for result in moved],
            "skipped": [self._serialize_result(result) for result in skipped],
            "failed": [self._serialize_result(result) for result in failed],
        }

    def sync_assignment_after_provision(
        self,
        *,
        user_id: Any,
        email: str,
        group_id: Any,
        assignment_mode: AssignmentMode,
        reason: str | None,
        group_name: str | None = None,
    ) -> UserGroupAssignment:
        now = datetime.now(timezone.utc)
        existing = self.store.get_user_assignment(user_id)
        assignment = UserGroupAssignment(
            user_id=user_id,
            email=email,
            current_group_id=group_id,
            current_group_name=group_name,
            assignment_mode=assignment_mode,
            last_rotation_at=existing.last_rotation_at if existing else None,
            last_decision_reason=reason,
            has_api_keys=existing.has_api_keys if existing else None,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        return self.store.upsert_user_assignment(assignment)

    def _execute_rotation(
        self,
        *,
        assignment: UserGroupAssignment,
        target_group_id: Any,
        trigger_type: RotationTrigger,
        reason: str,
        usage_window: AutoRotationUsageWindow | None = None,
        usage_value: float | None = None,
        usage_snapshot: dict[str, Any] | None = None,
    ) -> RotationExecutionResult:
        target_group = self.store.get_rotation_pool_group(target_group_id)
        if target_group is None or not target_group.is_exclusive:
            return self._record_result(
                assignment=assignment,
                target_group_id=target_group_id,
                trigger_type=trigger_type,
                status=RotationResultStatus.failed,
                reason="Target group is not a selected exclusive rotation target",
                usage_window=usage_window,
                usage_value=usage_value,
                usage_snapshot=usage_snapshot,
            )

        if self.store.has_pending_flow_for_user(assignment.user_id):
            return self._record_result(
                assignment=assignment,
                target_group_id=target_group_id,
                trigger_type=trigger_type,
                status=RotationResultStatus.skipped,
                reason="User still has a pending OAuth provisioning flow",
                usage_window=usage_window,
                usage_value=usage_value,
                usage_snapshot=usage_snapshot,
            )

        if self._normalize_key(assignment.current_group_id) == self._normalize_key(target_group_id):
            return self._record_result(
                assignment=assignment,
                target_group_id=target_group_id,
                trigger_type=trigger_type,
                status=RotationResultStatus.skipped,
                reason="Target group matches the current assignment",
                usage_window=usage_window,
                usage_value=usage_value,
                usage_snapshot=usage_snapshot,
            )

        cooldown = self.settings.auto_rotation.cooldown_minutes
        if cooldown > 0 and assignment.last_rotation_at is not None:
            earliest_allowed = assignment.last_rotation_at + timedelta(minutes=cooldown)
            if earliest_allowed > datetime.now(timezone.utc):
                return self._record_result(
                    assignment=assignment,
                    target_group_id=target_group_id,
                    trigger_type=trigger_type,
                    status=RotationResultStatus.skipped,
                    reason="Rotation is still within the configured cooldown window",
                    usage_window=usage_window,
                    usage_value=usage_value,
                    usage_snapshot=usage_snapshot,
                )

        try:
            response = self.sub2api_client.replace_exclusive_user_group(
                user_id=assignment.user_id,
                old_group_id=assignment.current_group_id,
                new_group_id=target_group_id,
            )
        except Sub2APIError as exc:
            logger.exception("Rotation execution failed for user_id=%s", assignment.user_id)
            return self._record_result(
                assignment=assignment,
                target_group_id=target_group_id,
                trigger_type=trigger_type,
                status=RotationResultStatus.failed,
                reason=f"Upstream replace-group failed: {exc}",
                usage_window=usage_window,
                usage_value=usage_value,
                usage_snapshot=usage_snapshot,
            )

        now = datetime.now(timezone.utc)
        updated_assignment = UserGroupAssignment(
            user_id=assignment.user_id,
            email=assignment.email,
            current_group_id=target_group_id,
            current_group_name=target_group.group_name,
            assignment_mode=AssignmentMode.managed_pool,
            last_rotation_at=now,
            last_decision_reason=reason,
            has_api_keys=assignment.has_api_keys,
            created_at=assignment.created_at,
            updated_at=now,
        )
        self.store.upsert_user_assignment(updated_assignment)
        return self._record_result(
            assignment=updated_assignment,
            source_group_id=assignment.current_group_id,
            target_group_id=target_group_id,
            trigger_type=trigger_type,
            status=RotationResultStatus.moved,
            reason=reason,
            migrated_keys=int(response.get("migrated_keys") or 0),
            usage_window=usage_window,
            usage_value=usage_value,
            usage_snapshot=usage_snapshot,
        )

    def _record_result(
        self,
        *,
        assignment: UserGroupAssignment,
        target_group_id: Any,
        trigger_type: RotationTrigger,
        status: RotationResultStatus,
        reason: str,
        source_group_id: Any | None = None,
        migrated_keys: int = 0,
        usage_window: AutoRotationUsageWindow | None = None,
        usage_value: float | None = None,
        usage_snapshot: dict[str, Any] | None = None,
    ) -> RotationExecutionResult:
        result = RotationExecutionResult(
            user_id=assignment.user_id,
            email=assignment.email,
            source_group_id=source_group_id if source_group_id is not None else assignment.current_group_id,
            target_group_id=target_group_id,
            trigger_type=trigger_type,
            status=status,
            reason=reason,
            migrated_keys=migrated_keys,
            usage_window=usage_window,
            usage_value=usage_value,
            usage_snapshot=usage_snapshot,
        )
        event = RotationEvent(
            user_id=result.user_id,
            email=result.email,
            source_group_id=result.source_group_id,
            target_group_id=result.target_group_id,
            trigger_type=result.trigger_type,
            status=result.status,
            reason=result.reason,
            usage_window=result.usage_window,
            usage_value=result.usage_value,
            usage_snapshot=result.usage_snapshot,
            metadata={"migrated_keys": str(result.migrated_keys)},
        )
        self.store.save_rotation_event(event)
        return result

    def _get_upstream_group(self, group_id: Any) -> dict[str, Any]:
        target_key = self._normalize_key(group_id)
        for group in self.sub2api_client.list_groups(
            platform=self.settings.sub2api_provisioning_defaults.group_platform
        ):
            if self._normalize_key(group["id"]) == target_key:
                return group
        raise RotationTargetValidationError("Group was not found in upstream Sub2API")

    def _next_priority(self) -> int:
        groups = self.store.list_rotation_pool_groups()
        if not groups:
            return 0
        return max(group.priority for group in groups) + 1

    def _validate_pool_threshold_mapping(self, pool_groups: list[RotationPoolGroup]) -> None:
        thresholds = self.settings.auto_rotation.usage_thresholds
        expected_group_count = len(thresholds) + 1
        if len(pool_groups) != expected_group_count:
            raise RotationExecutionError(
                "Automatic rotation requires len(rotation_pool_groups) == len(AUTO_ROTATION_USAGE_THRESHOLDS_JSON) + 1"
            )

    def _select_target_group(
        self, pool_groups: list[RotationPoolGroup], usage_value: float
    ) -> RotationPoolGroup:
        thresholds = self.settings.auto_rotation.usage_thresholds
        index = 0
        for threshold in thresholds:
            if usage_value <= threshold:
                break
            index += 1
        return pool_groups[min(index, len(pool_groups) - 1)]

    def _build_usage_snapshot(self, user_id: Any) -> dict[str, Any]:
        api_keys_response = self.sub2api_client.get_user_api_keys(user_id)
        api_keys = api_keys_response["items"]
        has_api_keys = api_keys_response["total"] > 0
        usage_window = self._window_enum()
        usage_value = 0.0

        if has_api_keys and usage_window in {
            AutoRotationUsageWindow.window_5h,
            AutoRotationUsageWindow.window_1d,
            AutoRotationUsageWindow.window_7d,
        }:
            field_name = {
                AutoRotationUsageWindow.window_5h: "usage_5h",
                AutoRotationUsageWindow.window_1d: "usage_1d",
                AutoRotationUsageWindow.window_7d: "usage_7d",
            }[usage_window]
            usage_value = sum(float(item.get(field_name) or 0.0) for item in api_keys)
        elif has_api_keys and usage_window == AutoRotationUsageWindow.window_30d:
            end_date = date.today()
            start_date = end_date - timedelta(days=29)
            stats = self.sub2api_client.get_usage_stats(
                user_id=user_id,
                start_date=start_date,
                end_date=end_date,
                timezone_name="",
            )
            usage_value = float(stats.get("total_actual_cost") or 0.0)

        return {
            "usage_window": usage_window,
            "usage_value": usage_value,
            "has_api_keys": has_api_keys,
            "api_key_count": api_keys_response["total"],
        }

    def _window_enum(self) -> AutoRotationUsageWindow:
        return AutoRotationUsageWindow(self.settings.auto_rotation.usage_window.value)

    def _normalize_key(self, value: Any) -> str:
        return str(value)

    def _serialize_result(self, result: RotationExecutionResult) -> dict[str, Any]:
        return {
            "user_id": result.user_id,
            "email": result.email,
            "source_group_id": result.source_group_id,
            "target_group_id": result.target_group_id,
            "trigger_type": result.trigger_type.value,
            "status": result.status.value,
            "reason": result.reason,
            "migrated_keys": result.migrated_keys,
            "usage_window": result.usage_window.value if result.usage_window else None,
            "usage_value": result.usage_value,
            "usage_snapshot": result.usage_snapshot,
        }
