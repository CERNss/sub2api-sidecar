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
    AutoRotationRuntimeConfig,
    AutoRotationUsageWindow,
    RotationEvent,
    RotationPoolKind,
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
    metadata: dict[str, Any] | None = None


@dataclass
class UpstreamAssignmentSyncResult:
    summary: dict[str, int]
    assignments: list[UserGroupAssignment]
    seen_user_keys: set[str]
    new_user_candidates: list[UserGroupAssignment]


@dataclass
class UsageRotationCandidate:
    assignment: UserGroupAssignment
    usage_snapshot: dict[str, Any]
    usage_value: float


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
        rotation_selected = {
            self._normalize_key(group.group_id): group
            for group in self.store.list_rotation_pool_groups()
        }
        landing_selected = {
            self._normalize_key(group.group_id): group
            for group in self.store.list_rotation_pool_groups(RotationPoolKind.landing)
        }
        groups = self.sub2api_client.list_groups(
            platform=self.settings.sub2api_provisioning_defaults.group_platform
        )
        candidates: list[dict[str, Any]] = []
        for group in groups:
            group_key = self._normalize_key(group["id"])
            selected_group = rotation_selected.get(group_key)
            landing_group = landing_selected.get(group_key)
            rotation_supported, unsupported_reason = self._rotation_support(group)
            candidates.append(
                {
                    "group_id": group["id"],
                    "name": group["name"],
                    "group_kind": group.get("group_kind"),
                    "platform": group.get("platform"),
                    "status": group.get("status"),
                    "is_exclusive": group.get("is_exclusive", False),
                    "is_subscription": group.get("is_subscription", False),
                    "rotation_supported": rotation_supported,
                    "unsupported_reason": unsupported_reason,
                    "selected": selected_group is not None,
                    "rotation_selected": selected_group is not None,
                    "landing_selected": landing_group is not None,
                    "priority": selected_group.priority if selected_group else None,
                    "landing_priority": landing_group.priority if landing_group else None,
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

    def add_group_to_pool(
        self,
        group_id: Any,
        priority: int | None = None,
        pool_kind: RotationPoolKind = RotationPoolKind.rotation,
    ) -> RotationPoolGroup:
        group = self._get_upstream_group(group_id)
        rotation_supported, _ = self._rotation_support(group)
        if not rotation_supported:
            if group.get("is_subscription", False):
                raise RotationTargetValidationError(
                    "Subscription groups cannot be added to the rotation pool; replace-group supports only dedicated standard groups"
                )
            raise RotationTargetValidationError("Only exclusive groups can be added to the rotation pool")

        now = datetime.now(timezone.utc)
        existing = self.store.get_rotation_pool_group(group_id, pool_kind)
        next_priority = priority if priority is not None else self._next_priority(pool_kind)
        pool_group = RotationPoolGroup(
            group_id=group["id"],
            pool_kind=pool_kind,
            group_name=group["name"],
            group_kind=group.get("group_kind"),
            platform=group.get("platform"),
            status=group.get("status"),
            is_exclusive=True,
            is_subscription=bool(group.get("is_subscription", False)),
            priority=next_priority,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        return self.store.upsert_rotation_pool_group(pool_group)

    def remove_group_from_pool(
        self,
        group_id: Any,
        pool_kind: RotationPoolKind = RotationPoolKind.rotation,
    ) -> None:
        self.store.delete_rotation_pool_group(group_id, pool_kind)

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

        target_group = self._get_upstream_group(target_group_id)
        rotation_supported, unsupported_reason = self._rotation_support(target_group)
        if not rotation_supported:
            if target_group.get("is_subscription", False):
                raise RotationTargetValidationError(
                    "Subscription groups cannot be used as manual rotation targets; replace-group supports only dedicated standard groups"
                )
            raise RotationTargetValidationError(
                unsupported_reason or "Target group is not supported for manual rotation"
            )

        request_reason = reason or "manual rotation request"
        if self.store.has_pending_flow_for_user(assignment.user_id):
            return self._record_result(
                assignment=assignment,
                target_group_id=target_group_id,
                trigger_type=RotationTrigger.manual,
                status=RotationResultStatus.skipped,
                reason="User still has a pending OAuth provisioning flow",
            )

        if self._normalize_key(assignment.current_group_id) == self._normalize_key(target_group_id):
            assignment.current_group_name = target_group["name"]
            assignment.last_decision_reason = "Target group matches the current assignment"
            assignment.updated_at = datetime.now(timezone.utc)
            self.store.upsert_user_assignment(assignment)
            return self._record_result(
                assignment=assignment,
                target_group_id=target_group_id,
                trigger_type=RotationTrigger.manual,
                status=RotationResultStatus.skipped,
                reason=assignment.last_decision_reason,
            )

        try:
            response = self.sub2api_client.replace_exclusive_user_group(
                user_id=assignment.user_id,
                old_group_id=assignment.current_group_id,
                new_group_id=target_group_id,
            )
        except Sub2APIError as exc:
            logger.exception("Manual rotation failed for user_id=%s", assignment.user_id)
            return self._record_result(
                assignment=assignment,
                target_group_id=target_group_id,
                trigger_type=RotationTrigger.manual,
                status=RotationResultStatus.failed,
                reason=f"Upstream replace-group failed: {exc}",
            )

        now = datetime.now(timezone.utc)
        updated_assignment = UserGroupAssignment(
            user_id=assignment.user_id,
            email=assignment.email,
            current_group_id=target_group_id,
            current_group_name=target_group["name"],
            assignment_mode=AssignmentMode.managed_pool,
            last_rotation_at=now,
            last_decision_reason=request_reason,
            has_api_keys=assignment.has_api_keys,
            created_at=assignment.created_at,
            updated_at=now,
        )
        self.store.upsert_user_assignment(updated_assignment)
        return self._record_result(
            assignment=updated_assignment,
            source_group_id=assignment.current_group_id,
            target_group_id=target_group_id,
            trigger_type=RotationTrigger.manual,
            status=RotationResultStatus.moved,
            reason=request_reason,
            migrated_keys=int(response.get("migrated_keys") or 0),
            metadata={
                "source_group_name": assignment.current_group_name or "",
                "target_group_name": target_group["name"],
            },
        )

    def orchestrate_existing_assignment(
        self,
        *,
        user_id: Any,
        email: str,
        source_group_id: Any,
        target_group_id: Any,
        reason: str | None = None,
    ) -> RotationExecutionResult:
        if source_group_id in (None, ""):
            raise RotationTargetValidationError("Source group is required")
        if target_group_id in (None, ""):
            raise RotationTargetValidationError("Target group is required")

        direct_group_id, direct_group_name = self._get_direct_user_group(user_id)
        if direct_group_id in (None, ""):
            raise RotationTargetValidationError(
                "Selected user does not have a direct current group; source group cannot be inferred"
            )
        if self._normalize_key(source_group_id) != self._normalize_key(direct_group_id):
            raise RotationTargetValidationError(
                "Source group must match the selected user's direct current group"
            )

        target_group = self._get_upstream_group(target_group_id)
        rotation_supported, unsupported_reason = self._rotation_support(target_group)
        if not rotation_supported:
            if target_group.get("is_subscription", False):
                raise RotationTargetValidationError(
                    "Subscription groups cannot be used as orchestration targets; replace-group supports only dedicated standard groups"
                )
            raise RotationTargetValidationError(
                unsupported_reason or "Target group is not supported for orchestration"
            )

        now = datetime.now(timezone.utc)
        existing = self.store.get_user_assignment(user_id)
        assignment = UserGroupAssignment(
            user_id=user_id,
            email=email,
            current_group_id=source_group_id,
            current_group_name=direct_group_name or (existing.current_group_name if existing else None),
            assignment_mode=AssignmentMode.managed_pool,
            last_rotation_at=existing.last_rotation_at if existing else None,
            last_decision_reason=existing.last_decision_reason if existing else None,
            has_api_keys=existing.has_api_keys if existing else None,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

        request_reason = reason or "existing user/group orchestration"
        if self._normalize_key(source_group_id) == self._normalize_key(target_group_id):
            assignment.current_group_id = target_group_id
            assignment.current_group_name = target_group["name"]
            assignment.last_decision_reason = "Target group matches the current assignment"
            assignment.updated_at = now
            self.store.upsert_user_assignment(assignment)
            return self._record_result(
                assignment=assignment,
                source_group_id=source_group_id,
                target_group_id=target_group_id,
                trigger_type=RotationTrigger.manual,
                status=RotationResultStatus.skipped,
                reason=assignment.last_decision_reason,
            )

        try:
            response = self.sub2api_client.replace_exclusive_user_group(
                user_id=user_id,
                old_group_id=source_group_id,
                new_group_id=target_group_id,
            )
        except Sub2APIError as exc:
            logger.exception("Existing assignment orchestration failed for user_id=%s", user_id)
            return self._record_result(
                assignment=assignment,
                source_group_id=source_group_id,
                target_group_id=target_group_id,
                trigger_type=RotationTrigger.manual,
                status=RotationResultStatus.failed,
                reason=f"Upstream replace-group failed: {exc}",
            )

        assignment.current_group_id = target_group_id
        assignment.current_group_name = target_group["name"]
        assignment.last_rotation_at = now
        assignment.last_decision_reason = request_reason
        assignment.updated_at = now
        self.store.upsert_user_assignment(assignment)
        return self._record_result(
            assignment=assignment,
            source_group_id=source_group_id,
            target_group_id=target_group_id,
            trigger_type=RotationTrigger.manual,
            status=RotationResultStatus.moved,
            reason=request_reason,
            migrated_keys=int(response.get("migrated_keys") or 0),
        )

    def _get_direct_user_group(self, user_id: Any) -> tuple[Any | None, str | None]:
        for user in self.sub2api_client.list_users():
            if self._normalize_key(user.get("id")) == self._normalize_key(user_id):
                return user.get("current_group_id"), user.get("current_group_name")
        return None, None

    def orchestrate_existing_api_key(
        self,
        *,
        user_id: Any,
        email: str,
        key_id: Any,
        source_group_id: Any | None,
        target_group_id: Any,
        reason: str | None = None,
    ) -> RotationExecutionResult:
        if key_id in (None, ""):
            raise RotationTargetValidationError("API key is required")
        if target_group_id in (None, ""):
            raise RotationTargetValidationError("Target group is required")

        target_group = self._get_upstream_group(target_group_id)
        existing = self.store.get_user_assignment(user_id)
        assignment = UserGroupAssignment(
            user_id=user_id,
            email=email,
            current_group_id=source_group_id if source_group_id not in (None, "") else target_group_id,
            current_group_name=existing.current_group_name if existing else None,
            assignment_mode=AssignmentMode.managed_pool,
            last_rotation_at=existing.last_rotation_at if existing else None,
            last_decision_reason=existing.last_decision_reason if existing else None,
            has_api_keys=True,
            created_at=existing.created_at if existing else datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        try:
            self.sub2api_client.update_api_key_group(
                key_id=key_id,
                group_id=target_group_id,
            )
        except Sub2APIError as exc:
            logger.exception("API key group orchestration failed for key_id=%s", key_id)
            return self._record_result(
                assignment=assignment,
                source_group_id=source_group_id,
                target_group_id=target_group_id,
                trigger_type=RotationTrigger.manual,
                status=RotationResultStatus.failed,
                reason=f"Upstream api-key group update failed: {exc}",
            )

        result = self._record_result(
            assignment=assignment,
            source_group_id=source_group_id,
            target_group_id=target_group_id,
            trigger_type=RotationTrigger.manual,
            status=RotationResultStatus.moved,
            reason=reason or "single API key group orchestration",
            migrated_keys=1,
            metadata={
                "key_id": str(key_id),
                "target_group_name": target_group["name"],
            },
        )
        return result

    def run_auto_rotation(
        self,
        trigger_type: RotationTrigger = RotationTrigger.automatic_api,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        runtime_config = self.get_auto_rotation_config()
        if not dry_run and not runtime_config.enabled:
            raise RotationExecutionError("Automatic rotation is disabled")

        pool_groups = self.store.list_rotation_pool_groups()
        landing_groups = self.store.list_rotation_pool_groups(RotationPoolKind.landing)
        if not pool_groups:
            raise RotationPoolEmptyError("No rotation pool groups are available")

        sync_result = self._sync_existing_user_assignments(
            pool_groups,
            landing_groups=landing_groups,
            runtime_config=runtime_config,
            persist=not dry_run,
        )
        assignments_by_user_id = {
            self._normalize_key(assignment.user_id): assignment
            for assignment in self.store.list_user_assignments()
            if self._assignment_in_pool(assignment, pool_groups)
            and self._normalize_key(assignment.user_id) not in sync_result.seen_user_keys
        }
        for assignment in sync_result.assignments:
            assignments_by_user_id[self._normalize_key(assignment.user_id)] = assignment
        assignments = list(assignments_by_user_id.values())
        ordered_candidates: list[UsageRotationCandidate] = []
        for assignment in assignments:
            usage_snapshot = self._build_usage_snapshot(assignment.user_id)
            assignment.has_api_keys = usage_snapshot["has_api_keys"]
            assignment.updated_at = datetime.now(timezone.utc)
            if not dry_run:
                self.store.upsert_user_assignment(assignment)
            ordered_candidates.append(
                UsageRotationCandidate(
                    assignment=assignment,
                    usage_snapshot=usage_snapshot,
                    usage_value=float(usage_snapshot["usage_value"]),
                )
            )

        ordered_candidates.sort(
            key=lambda candidate: (
                -candidate.usage_value,
                str(candidate.assignment.email).lower(),
                str(candidate.assignment.user_id),
            )
        )

        moved: list[RotationExecutionResult] = []
        planned: list[RotationExecutionResult] = []
        skipped: list[RotationExecutionResult] = []
        failed: list[RotationExecutionResult] = []
        sorted_pool = sorted(pool_groups, key=lambda group: (group.priority, group.created_at))
        target_loads = self._initial_pool_usage_loads(sorted_pool, ordered_candidates)

        if runtime_config.auto_assign_new_users:
            for assignment in sync_result.new_user_candidates:
                usage_snapshot = self._build_usage_snapshot(assignment.user_id)
                usage_value = float(usage_snapshot["usage_value"])
                assignment.has_api_keys = usage_snapshot["has_api_keys"]
                target_group = self._select_least_loaded_usage_group(sorted_pool, target_loads)
                reason = f"auto assign new user by usage load window={usage_snapshot['usage_window'].value} usage={usage_value}"
                if dry_run:
                    result = self._preview_rotation(
                        assignment=assignment,
                        target_group_id=target_group.group_id,
                        trigger_type=trigger_type,
                        reason=reason,
                        usage_window=usage_snapshot["usage_window"],
                        usage_value=usage_value,
                        usage_snapshot=usage_snapshot,
                        metadata={
                            "decision_type": "new_user_usage_assignment",
                            "usage_loads_before": self._serialize_usage_loads(target_loads),
                        },
                    )
                else:
                    result = self._execute_rotation(
                        assignment=assignment,
                        target_group_id=target_group.group_id,
                        trigger_type=trigger_type,
                        reason=reason,
                        usage_window=usage_snapshot["usage_window"],
                        usage_value=usage_value,
                        usage_snapshot=usage_snapshot,
                        metadata={
                            "decision_type": "new_user_usage_assignment",
                            "usage_loads_before": self._serialize_usage_loads(target_loads),
                        },
                    )
                if result.status == RotationResultStatus.moved:
                    target_loads[self._normalize_key(target_group.group_id)] += usage_value
                    moved.append(result)
                elif result.status == RotationResultStatus.planned:
                    target_loads[self._normalize_key(target_group.group_id)] += usage_value
                    planned.append(result)
                elif result.status == RotationResultStatus.skipped:
                    skipped.append(result)
                else:
                    failed.append(result)

        for candidate in ordered_candidates:
            assignment = candidate.assignment
            target_group = self._select_usage_balancing_target_group(
                candidate,
                sorted_pool,
                target_loads,
            )
            source_key = self._normalize_key(assignment.current_group_id)
            target_key = self._normalize_key(target_group.group_id)
            reason = f"auto rotation by usage load window={candidate.usage_snapshot['usage_window'].value} usage={candidate.usage_value}"
            metadata = {
                "decision_type": "usage_balancing",
                "usage_loads_before": self._serialize_usage_loads(target_loads),
            }
            if dry_run:
                result = self._preview_rotation(
                    assignment=assignment,
                    target_group_id=target_group.group_id,
                    trigger_type=trigger_type,
                    reason=reason,
                    usage_window=candidate.usage_snapshot["usage_window"],
                    usage_value=candidate.usage_value,
                    usage_snapshot=candidate.usage_snapshot,
                    metadata=metadata,
                )
            else:
                result = self._execute_rotation(
                    assignment=assignment,
                    target_group_id=target_group.group_id,
                    trigger_type=trigger_type,
                    reason=reason,
                    usage_window=candidate.usage_snapshot["usage_window"],
                    usage_value=candidate.usage_value,
                    usage_snapshot=candidate.usage_snapshot,
                    metadata=metadata,
                )
            if source_key != target_key and result.status in {
                RotationResultStatus.moved,
                RotationResultStatus.planned,
            }:
                if source_key in target_loads:
                    target_loads[source_key] = max(0.0, target_loads[source_key] - candidate.usage_value)
                target_loads[target_key] += candidate.usage_value
            if result.status == RotationResultStatus.moved:
                moved.append(result)
            elif result.status == RotationResultStatus.planned:
                planned.append(result)
            elif result.status == RotationResultStatus.skipped:
                skipped.append(result)
            else:
                failed.append(result)

        return {
            "window": self._window_enum().value,
            "dry_run": dry_run,
            "synced": sync_result.summary,
            "config": self._serialize_config(runtime_config),
            "planned": [self._serialize_result(result) for result in planned],
            "moved": [self._serialize_result(result) for result in moved],
            "skipped": [self._serialize_result(result) for result in skipped],
            "failed": [self._serialize_result(result) for result in failed],
        }

    def get_auto_rotation_config(self) -> AutoRotationRuntimeConfig:
        stored = self.store.get_auto_rotation_config()
        if stored is not None:
            return stored
        return AutoRotationRuntimeConfig(
            enabled=self.settings.auto_rotation.enabled,
            auto_assign_new_users=False,
            cooldown_minutes=self.settings.auto_rotation.cooldown_minutes,
            usage_window=AutoRotationUsageWindow(self.settings.auto_rotation.usage_window.value),
            usage_thresholds=self.settings.auto_rotation.usage_thresholds,
            schedule_source_group_ids=(),
        )

    def update_auto_rotation_config(
        self,
        *,
        enabled: bool,
        auto_assign_new_users: bool,
        cooldown_minutes: int,
        usage_window: AutoRotationUsageWindow,
        usage_thresholds: tuple[float, ...],
        schedule_source_group_ids: tuple[Any, ...],
    ) -> AutoRotationRuntimeConfig:
        if cooldown_minutes < 0:
            raise RotationExecutionError("cooldown_minutes must be >= 0")
        now = datetime.now(timezone.utc)
        existing = self.store.get_auto_rotation_config()
        config = AutoRotationRuntimeConfig(
            enabled=enabled,
            auto_assign_new_users=auto_assign_new_users,
            cooldown_minutes=cooldown_minutes,
            usage_window=usage_window,
            usage_thresholds=usage_thresholds,
            schedule_source_group_ids=schedule_source_group_ids,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        return self.store.save_auto_rotation_config(config)

    def sync_existing_user_assignments(
        self,
        pool_groups: list[RotationPoolGroup] | None = None,
    ) -> dict[str, int]:
        return self._sync_existing_user_assignments(
            pool_groups,
            landing_groups=self.store.list_rotation_pool_groups(RotationPoolKind.landing),
            runtime_config=self.get_auto_rotation_config(),
            persist=True,
        ).summary

    def _sync_existing_user_assignments(
        self,
        pool_groups: list[RotationPoolGroup] | None = None,
        landing_groups: list[RotationPoolGroup] | None = None,
        runtime_config: AutoRotationRuntimeConfig | None = None,
        *,
        persist: bool,
    ) -> UpstreamAssignmentSyncResult:
        runtime_config = runtime_config if runtime_config is not None else self.get_auto_rotation_config()
        pool_groups = pool_groups if pool_groups is not None else self.store.list_rotation_pool_groups()
        landing_groups = (
            landing_groups
            if landing_groups is not None
            else self.store.list_rotation_pool_groups(RotationPoolKind.landing)
        )
        pool_group_names = {
            self._normalize_key(group.group_id): group.group_name
            for group in pool_groups
        }
        landing_group_names = {
            self._normalize_key(group.group_id): group.group_name
            for group in landing_groups
        }
        now = datetime.now(timezone.utc)
        summary = {
            "seen": 0,
            "synced": 0,
            "new_user_candidates": 0,
            "skipped_without_current_group": 0,
            "skipped_outside_schedule_range": 0,
            "skipped_outside_pool": 0,
        }
        assignments: list[UserGroupAssignment] = []
        new_user_candidates: list[UserGroupAssignment] = []
        seen_user_keys: set[str] = set()
        for user in self.sub2api_client.list_users():
            summary["seen"] += 1
            user_id = user.get("id")
            seen_user_keys.add(self._normalize_key(user_id))
            email = str(user.get("email") or "")
            current_group_id = user.get("current_group_id")
            current_group_key = self._normalize_key(current_group_id) if current_group_id not in (None, "") else ""
            if not current_group_key:
                summary["skipped_without_current_group"] += 1
                continue
            if current_group_key in pool_group_names:
                existing = self.store.get_user_assignment(user_id)
                assignment = UserGroupAssignment(
                    user_id=user_id,
                    email=email,
                    current_group_id=current_group_id,
                    current_group_name=user.get("current_group_name") or pool_group_names[current_group_key],
                    assignment_mode=existing.assignment_mode if existing else AssignmentMode.managed_pool,
                    last_rotation_at=existing.last_rotation_at if existing else None,
                    last_decision_reason=existing.last_decision_reason if existing else "synced from upstream current user group",
                    has_api_keys=existing.has_api_keys if existing else None,
                    created_at=existing.created_at if existing else now,
                    updated_at=now,
                )
                assignments.append(assignment)
                if persist:
                    self.store.upsert_user_assignment(assignment)
                summary["synced"] += 1
                continue

            if current_group_key not in landing_group_names:
                summary["skipped_outside_schedule_range"] += 1
                continue

            if runtime_config.auto_assign_new_users:
                existing = self.store.get_user_assignment(user_id)
                assignment = UserGroupAssignment(
                    user_id=user_id,
                    email=email,
                    current_group_id=current_group_id,
                    current_group_name=user.get("current_group_name") or landing_group_names[current_group_key],
                    assignment_mode=existing.assignment_mode if existing else AssignmentMode.managed_pool,
                    last_rotation_at=existing.last_rotation_at if existing else None,
                    last_decision_reason=existing.last_decision_reason if existing else "discovered outside dynamic target pool",
                    has_api_keys=existing.has_api_keys if existing else None,
                    created_at=existing.created_at if existing else now,
                    updated_at=now,
                )
                new_user_candidates.append(assignment)
                summary["new_user_candidates"] += 1
            else:
                summary["skipped_outside_pool"] += 1
        return UpstreamAssignmentSyncResult(
            summary=summary,
            assignments=assignments,
            seen_user_keys=seen_user_keys,
            new_user_candidates=new_user_candidates,
        )

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
        metadata: dict[str, Any] | None = None,
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

        if target_group.is_subscription:
            return self._record_result(
                assignment=assignment,
                target_group_id=target_group_id,
                trigger_type=trigger_type,
                status=RotationResultStatus.failed,
                reason="Target group is a subscription group; replace-group supports only dedicated standard groups",
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

        cooldown = self.get_auto_rotation_config().cooldown_minutes
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
        result_metadata = {
            **(metadata or {}),
            "source_group_name": assignment.current_group_name or "",
            "target_group_name": target_group.group_name,
        }
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
            metadata=result_metadata,
        )

    def _preview_rotation(
        self,
        *,
        assignment: UserGroupAssignment,
        target_group_id: Any,
        trigger_type: RotationTrigger,
        reason: str,
        usage_window: AutoRotationUsageWindow | None = None,
        usage_value: float | None = None,
        usage_snapshot: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RotationExecutionResult:
        target_group = self.store.get_rotation_pool_group(target_group_id)
        result_metadata = metadata.copy() if metadata else None
        if result_metadata is not None and target_group is not None:
            result_metadata.setdefault("source_group_name", assignment.current_group_name or "")
            result_metadata.setdefault("target_group_name", target_group.group_name)
        if target_group is None or not target_group.is_exclusive:
            return RotationExecutionResult(
                user_id=assignment.user_id,
                email=assignment.email,
                source_group_id=assignment.current_group_id,
                target_group_id=target_group_id,
                trigger_type=trigger_type,
                status=RotationResultStatus.failed,
                reason="Target group is not a selected exclusive rotation target",
                usage_window=usage_window,
                usage_value=usage_value,
                usage_snapshot=usage_snapshot,
                metadata=result_metadata,
            )

        if target_group.is_subscription:
            return RotationExecutionResult(
                user_id=assignment.user_id,
                email=assignment.email,
                source_group_id=assignment.current_group_id,
                target_group_id=target_group_id,
                trigger_type=trigger_type,
                status=RotationResultStatus.failed,
                reason="Target group is a subscription group; replace-group supports only dedicated standard groups",
                usage_window=usage_window,
                usage_value=usage_value,
                usage_snapshot=usage_snapshot,
                metadata=result_metadata,
            )

        if self.store.has_pending_flow_for_user(assignment.user_id):
            return RotationExecutionResult(
                user_id=assignment.user_id,
                email=assignment.email,
                source_group_id=assignment.current_group_id,
                target_group_id=target_group_id,
                trigger_type=trigger_type,
                status=RotationResultStatus.skipped,
                reason="User still has a pending OAuth provisioning flow",
                usage_window=usage_window,
                usage_value=usage_value,
                usage_snapshot=usage_snapshot,
                metadata=result_metadata,
            )

        if self._normalize_key(assignment.current_group_id) == self._normalize_key(target_group_id):
            return RotationExecutionResult(
                user_id=assignment.user_id,
                email=assignment.email,
                source_group_id=assignment.current_group_id,
                target_group_id=target_group_id,
                trigger_type=trigger_type,
                status=RotationResultStatus.skipped,
                reason="Target group matches the current assignment",
                usage_window=usage_window,
                usage_value=usage_value,
                usage_snapshot=usage_snapshot,
                metadata=result_metadata,
            )

        cooldown = self.get_auto_rotation_config().cooldown_minutes
        if cooldown > 0 and assignment.last_rotation_at is not None:
            earliest_allowed = assignment.last_rotation_at + timedelta(minutes=cooldown)
            if earliest_allowed > datetime.now(timezone.utc):
                return RotationExecutionResult(
                    user_id=assignment.user_id,
                    email=assignment.email,
                    source_group_id=assignment.current_group_id,
                    target_group_id=target_group_id,
                    trigger_type=trigger_type,
                    status=RotationResultStatus.skipped,
                    reason="Rotation is still within the configured cooldown window",
                    usage_window=usage_window,
                    usage_value=usage_value,
                    usage_snapshot=usage_snapshot,
                    metadata=result_metadata,
                )

        return RotationExecutionResult(
            user_id=assignment.user_id,
            email=assignment.email,
            source_group_id=assignment.current_group_id,
            target_group_id=target_group_id,
            trigger_type=trigger_type,
            status=RotationResultStatus.planned,
            reason=reason,
            usage_window=usage_window,
            usage_value=usage_value,
            usage_snapshot=usage_snapshot,
            metadata=result_metadata,
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
        metadata: dict[str, Any] | None = None,
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
            metadata=metadata,
        )
        event_metadata = {"migrated_keys": str(result.migrated_keys)}
        if metadata:
            event_metadata.update(metadata)
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
            metadata=event_metadata,
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

    def _rotation_support(self, group: dict[str, Any]) -> tuple[bool, str | None]:
        if not group.get("is_exclusive", False):
            return False, "group is not exclusive"
        if group.get("is_subscription", False):
            return False, "subscription groups cannot be rotated with replace-group"
        return True, None

    def _next_priority(self, pool_kind: RotationPoolKind = RotationPoolKind.rotation) -> int:
        groups = self.store.list_rotation_pool_groups(pool_kind)
        if not groups:
            return 0
        return max(group.priority for group in groups) + 1

    def _initial_pool_usage_loads(
        self,
        pool_groups: list[RotationPoolGroup],
        candidates: list[UsageRotationCandidate],
    ) -> dict[str, float]:
        loads = {self._normalize_key(group.group_id): 0.0 for group in pool_groups}
        for candidate in candidates:
            key = self._normalize_key(candidate.assignment.current_group_id)
            if key in loads:
                loads[key] += candidate.usage_value
        return loads

    def _select_least_loaded_usage_group(
        self,
        pool_groups: list[RotationPoolGroup],
        loads: dict[str, float],
    ) -> RotationPoolGroup:
        return min(
            pool_groups,
            key=lambda group: (
                loads.get(self._normalize_key(group.group_id), 0.0),
                group.priority,
                group.created_at,
                str(group.group_id),
            ),
        )

    def _select_usage_balancing_target_group(
        self,
        candidate: UsageRotationCandidate,
        pool_groups: list[RotationPoolGroup],
        loads: dict[str, float],
    ) -> RotationPoolGroup:
        assignment = candidate.assignment
        current_key = self._normalize_key(assignment.current_group_id)
        least_loaded = self._select_least_loaded_usage_group(pool_groups, loads)
        least_key = self._normalize_key(least_loaded.group_id)
        current_load = loads.get(current_key, 0.0)
        least_load = loads.get(least_key, 0.0)
        if current_key in loads and current_key != least_key:
            before_gap = current_load - least_load
            after_gap = abs((current_load - candidate.usage_value) - (least_load + candidate.usage_value))
            if before_gap > 0 and after_gap < before_gap:
                return least_loaded
        if current_key in loads:
            current_group = next(
                group
                for group in pool_groups
                if self._normalize_key(group.group_id) == current_key
            )
            return current_group
        return least_loaded

    def _serialize_usage_loads(self, loads: dict[str, float]) -> str:
        return ",".join(f"{key}:{loads[key]:.6g}" for key in sorted(loads))

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
        return self.get_auto_rotation_config().usage_window

    def _assignment_in_pool(
        self,
        assignment: UserGroupAssignment,
        pool_groups: list[RotationPoolGroup],
    ) -> bool:
        assignment_group_key = self._normalize_key(assignment.current_group_id)
        return any(
            self._normalize_key(group.group_id) == assignment_group_key
            for group in pool_groups
        )

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
            "metadata": result.metadata,
        }

    def _serialize_config(self, config: AutoRotationRuntimeConfig) -> dict[str, Any]:
        return {
            "enabled": config.enabled,
            "auto_assign_new_users": config.auto_assign_new_users,
            "cooldown_minutes": config.cooldown_minutes,
            "usage_window": config.usage_window.value,
            "usage_thresholds": list(config.usage_thresholds),
            "schedule_source_group_ids": list(config.schedule_source_group_ids),
            "created_at": config.created_at.isoformat(),
            "updated_at": config.updated_at.isoformat(),
        }
