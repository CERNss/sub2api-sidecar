from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.clients.sub2api import Sub2APIClient, Sub2APIError
from app.config import Settings
from app.errors import RotationExecutionError, RotationPoolEmptyError, RotationTargetValidationError
from app.models.flow import AssignmentMode
from app.models.group_usage import GroupUsageSegmentRecord
from app.models.rotation import (
    AutoRotationRuntimeConfig,
    AutoRotationUsageWindow,
    OrchestrationRunKind,
    OrchestrationRunRecord,
    RotationEvent,
    RotationPoolKind,
    RotationPoolGroup,
    RotationResultStatus,
    RotationTrigger,
    UserGroupAssignment,
)
from app.models.usage_segmentation import UserUsageSegmentRecord
from app.services.operational_data import (
    SOURCE_GROUPS,
    SOURCE_USER_API_KEYS,
    SOURCE_USER_USAGE,
    SOURCE_USERS,
)
from app.stores.postgres import PostgresFlowStore

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


@dataclass
class GroupLoadState:
    loads: dict[str, float]
    sources: dict[str, str]
    records: dict[str, GroupUsageSegmentRecord]


@dataclass
class _PreconditionBlock:
    status: RotationResultStatus
    reason: str


class RotationService:
    def __init__(
        self,
        store: PostgresFlowStore,
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
        groups = self._pool_candidate_groups(rotation_selected, landing_selected)
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
                0 if item["selected"] or item["landing_selected"] else 1,
                item["priority"]
                if item["priority"] is not None
                else item["landing_priority"]
                if item["landing_priority"] is not None
                else 999999,
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
        if pool_kind == RotationPoolKind.rotation:
            supported, _ = self._rotation_support(group)
            if not supported:
                if group.get("is_subscription", False):
                    raise RotationTargetValidationError(
                        "Subscription groups cannot be added to the rotation pool; replace-group supports only dedicated standard groups"
                    )
                raise RotationTargetValidationError("Only exclusive groups can be added to the rotation pool")
        else:
            supported, _ = self._landing_support(group)
            if not supported:
                raise RotationTargetValidationError(
                    "Subscription groups cannot be added to the landing pool"
                )

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
            is_exclusive=bool(group.get("is_exclusive", False)),
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
        for user in self._latest_users_snapshot():
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

    _STATUS_BUCKETS = {
        RotationResultStatus.moved: "moved",
        RotationResultStatus.planned: "planned",
        RotationResultStatus.failed: "failed",
        RotationResultStatus.skipped: "skipped",
    }

    def save_manual_run_record(
        self,
        *,
        tag: str,
        result: RotationExecutionResult,
    ) -> OrchestrationRunRecord:
        serialized = self._serialize_result(result)
        buckets: dict[str, list[dict[str, Any]]] = {
            "planned": [],
            "moved": [],
            "skipped": [],
            "failed": [],
        }
        buckets[self._STATUS_BUCKETS[result.status]].append(serialized)
        return self._save_orchestration_run(
            run_kind=OrchestrationRunKind.manual,
            tag=tag,
            trigger_type=RotationTrigger.manual,
            dry_run=False,
            window=result.usage_window,
            synced={},
            config={},
            **buckets,
        )

    def run_auto_rotation(
        self,
        trigger_type: RotationTrigger = RotationTrigger.automatic_api,
        *,
        dry_run: bool = False,
    ) -> OrchestrationRunRecord:
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
        group_load_state = self._initial_pool_group_loads(sorted_pool, ordered_candidates)
        target_loads = group_load_state.loads

        if runtime_config.auto_assign_new_users:
            for assignment in sync_result.new_user_candidates:
                usage_snapshot = self._build_usage_snapshot(assignment.user_id)
                usage_value = float(usage_snapshot["usage_value"])
                assignment.has_api_keys = usage_snapshot["has_api_keys"]
                target_group = self._select_least_loaded_usage_group(sorted_pool, target_loads)
                target_key = self._normalize_key(target_group.group_id)
                reason = f"auto assign new user by usage load window={usage_snapshot['usage_window'].value} usage={usage_value}"
                group_loads_before = self._group_load_metadata(
                    target_loads,
                    group_load_state.sources,
                )
                metadata = {
                    "decision_type": "new_user_usage_assignment",
                    "usage_loads_before": self._serialize_usage_loads(target_loads),
                    "group_loads_before": group_loads_before,
                    "group_load_source": group_load_state.sources.get(target_key, "candidate_sum"),
                    "target_group_load_before": target_loads.get(target_key, 0.0),
                    "target_group_load_source": group_load_state.sources.get(target_key, "candidate_sum"),
                }
                if dry_run:
                    result = self._preview_rotation(
                        assignment=assignment,
                        target_group_id=target_group.group_id,
                        trigger_type=trigger_type,
                        reason=reason,
                        usage_window=usage_snapshot["usage_window"],
                        usage_value=usage_value,
                        usage_snapshot=usage_snapshot,
                        metadata=metadata,
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
                        metadata=metadata,
                    )
                if result.status == RotationResultStatus.moved:
                    target_loads[target_key] += usage_value
                    moved.append(result)
                elif result.status == RotationResultStatus.planned:
                    target_loads[target_key] += usage_value
                    planned.append(result)
                elif result.status == RotationResultStatus.skipped:
                    skipped.append(result)
                else:
                    failed.append(result)

        imbalance_epsilon = max(0.0, float(runtime_config.imbalance_epsilon))
        improvement_delta = max(0.0, float(runtime_config.improvement_delta))
        dead_band_skipped = False
        for candidate in ordered_candidates:
            if imbalance_epsilon > 0 and target_loads:
                spread = max(target_loads.values()) - min(target_loads.values())
                if spread <= imbalance_epsilon:
                    dead_band_skipped = True
                    break
            assignment = candidate.assignment
            source_key = self._normalize_key(assignment.current_group_id)
            target_group, selection = self._select_usage_balancing_target_group(
                candidate,
                sorted_pool,
                target_loads,
                improvement_delta=improvement_delta,
            )
            target_key = self._normalize_key(target_group.group_id)
            reason = f"auto rotation by usage load window={candidate.usage_snapshot['usage_window'].value} usage={candidate.usage_value}"
            before_loads = self._group_load_metadata(
                target_loads,
                group_load_state.sources,
            )
            after_loads = self._simulated_group_load_metadata(
                target_loads,
                group_load_state.sources,
                source_key=source_key,
                target_key=target_key,
                usage_value=candidate.usage_value,
            )
            metadata = {
                "decision_type": "usage_balancing",
                "usage_loads_before": self._serialize_usage_loads(target_loads),
                "group_loads_before": before_loads,
                "group_loads_after": after_loads,
                "group_load_source": {
                    source_key: group_load_state.sources.get(source_key, "candidate_sum"),
                    target_key: group_load_state.sources.get(target_key, "candidate_sum"),
                },
                "source_group_load_before": target_loads.get(source_key, 0.0),
                "target_group_load_before": target_loads.get(target_key, 0.0),
                "source_group_load_source": group_load_state.sources.get(source_key, "candidate_sum"),
                "target_group_load_source": group_load_state.sources.get(target_key, "candidate_sum"),
                "source_group_key": source_key,
                "target_group_key": target_key,
                **selection,
                "imbalance_epsilon": imbalance_epsilon,
                "improvement_delta": improvement_delta,
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

        return self._save_orchestration_run(
            run_kind=OrchestrationRunKind.automatic,
            tag="automatic_preview" if dry_run else "automatic_execution",
            trigger_type=trigger_type,
            dry_run=dry_run,
            window=self._window_enum(),
            synced=sync_result.summary,
            config=self._serialize_config(runtime_config),
            dead_band_skipped=dead_band_skipped,
            planned=[self._serialize_result(result) for result in planned],
            moved=[self._serialize_result(result) for result in moved],
            skipped=[self._serialize_result(result) for result in skipped],
            failed=[self._serialize_result(result) for result in failed],
        )

    def get_auto_rotation_config(self) -> AutoRotationRuntimeConfig:
        stored = self.store.get_auto_rotation_config()
        if stored is not None:
            return stored
        return AutoRotationRuntimeConfig(
            enabled=False,
            auto_assign_new_users=False,
            cooldown_minutes=0,
            usage_window=AutoRotationUsageWindow.window_1d,
            usage_thresholds=(),
            imbalance_epsilon=0.0,
            improvement_delta=0.0,
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
        imbalance_epsilon: float = 0.0,
        improvement_delta: float = 0.0,
    ) -> AutoRotationRuntimeConfig:
        if cooldown_minutes < 0:
            raise RotationExecutionError("cooldown_minutes must be >= 0")
        if imbalance_epsilon < 0:
            raise RotationExecutionError("imbalance_epsilon must be >= 0")
        if improvement_delta < 0:
            raise RotationExecutionError("improvement_delta must be >= 0")
        now = datetime.now(timezone.utc)
        existing = self.store.get_auto_rotation_config()
        config = AutoRotationRuntimeConfig(
            enabled=enabled,
            auto_assign_new_users=auto_assign_new_users,
            cooldown_minutes=cooldown_minutes,
            usage_window=usage_window,
            usage_thresholds=usage_thresholds,
            imbalance_epsilon=imbalance_epsilon,
            improvement_delta=improvement_delta,
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
        for user in self._latest_users_snapshot():
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

    def list_orchestration_runs(self, limit: int = 50) -> list[OrchestrationRunRecord]:
        return self.store.list_orchestration_runs(limit=limit)

    def get_orchestration_run(self, run_id: str) -> OrchestrationRunRecord | None:
        return self.store.get_orchestration_run(run_id)

    def rollback_orchestration_run(self, run_id: str) -> OrchestrationRunRecord:
        record = self.store.get_orchestration_run(run_id)
        if record is None:
            raise RotationExecutionError("Run record not found")
        if record.run_kind != OrchestrationRunKind.automatic:
            raise RotationExecutionError("Manual run records cannot be rolled back")
        if record.dry_run:
            raise RotationExecutionError("Preview run records cannot be rolled back")
        if record.rollback_status:
            raise RotationExecutionError("Run record was already rolled back")
        if not record.moved:
            raise RotationExecutionError("Run record has no moved items to roll back")

        rollback_results: list[dict[str, Any]] = []
        for item in record.moved:
            source_group_id = item.get("target_group_id")
            target_group_id = item.get("source_group_id")
            if target_group_id in (None, ""):
                rollback_results.append(
                    {
                        **item,
                        "source_group_id": source_group_id,
                        "target_group_id": target_group_id,
                        "trigger_type": RotationTrigger.manual.value,
                        "status": RotationResultStatus.failed.value,
                        "reason": "Original source group is missing; rollback skipped",
                        "migrated_keys": 0,
                    }
                )
                continue

            assignment = UserGroupAssignment(
                user_id=item.get("user_id"),
                email=str(item.get("email") or ""),
                current_group_id=source_group_id,
                current_group_name=self._metadata_text(item, "target_group_name"),
                assignment_mode=AssignmentMode.managed_pool,
                last_decision_reason="rollback source state",
            )
            rollback_result = self._rollback_result(
                assignment=assignment,
                source_group_id=source_group_id,
                target_group_id=target_group_id,
                reason=f"rollback run {record.run_id}",
            )
            rollback_results.append(self._serialize_result(rollback_result))

        failed_count = sum(
            1
            for item in rollback_results
            if item.get("status") == RotationResultStatus.failed.value
        )
        record.rollback_results = rollback_results
        record.rollback_status = "failed" if failed_count == len(rollback_results) else "completed"
        record.rollback_reason = (
            f"{failed_count} rollback item(s) failed"
            if failed_count
            else "Rollback completed"
        )
        record.updated_at = datetime.now(timezone.utc)
        return self.store.save_orchestration_run(record)

    def _evaluate_rotation_preconditions(
        self,
        *,
        assignment: UserGroupAssignment,
        target_group_id: Any,
    ) -> tuple[RotationPoolGroup | None, _PreconditionBlock | None]:
        target_group = self.store.get_rotation_pool_group(target_group_id)
        if target_group is None or not target_group.is_exclusive:
            return None, _PreconditionBlock(
                RotationResultStatus.failed,
                "Target group is not a selected exclusive rotation target",
            )
        if target_group.is_subscription:
            return target_group, _PreconditionBlock(
                RotationResultStatus.failed,
                "Target group is a subscription group; replace-group supports only dedicated standard groups",
            )
        if self.store.has_pending_flow_for_user(assignment.user_id):
            return target_group, _PreconditionBlock(
                RotationResultStatus.skipped,
                "User still has a pending OAuth provisioning flow",
            )
        if self._normalize_key(assignment.current_group_id) == self._normalize_key(target_group_id):
            return target_group, _PreconditionBlock(
                RotationResultStatus.skipped,
                "Target group matches the current assignment",
            )
        cooldown = self.get_auto_rotation_config().cooldown_minutes
        if cooldown > 0 and assignment.last_rotation_at is not None:
            earliest_allowed = assignment.last_rotation_at + timedelta(minutes=cooldown)
            if earliest_allowed > datetime.now(timezone.utc):
                return target_group, _PreconditionBlock(
                    RotationResultStatus.skipped,
                    "Rotation is still within the configured cooldown window",
                )
        return target_group, None

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
        target_group, block = self._evaluate_rotation_preconditions(
            assignment=assignment,
            target_group_id=target_group_id,
        )
        if block is not None:
            return self._record_result(
                assignment=assignment,
                target_group_id=target_group_id,
                trigger_type=trigger_type,
                status=block.status,
                reason=block.reason,
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
        target_group, block = self._evaluate_rotation_preconditions(
            assignment=assignment,
            target_group_id=target_group_id,
        )
        result_metadata = metadata.copy() if metadata else None
        if result_metadata is not None and target_group is not None:
            result_metadata.setdefault("source_group_name", assignment.current_group_name or "")
            result_metadata.setdefault("target_group_name", target_group.group_name)
        status = block.status if block else RotationResultStatus.planned
        result_reason = block.reason if block else reason
        return RotationExecutionResult(
            user_id=assignment.user_id,
            email=assignment.email,
            source_group_id=assignment.current_group_id,
            target_group_id=target_group_id,
            trigger_type=trigger_type,
            status=status,
            reason=result_reason,
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

    def _rollback_result(
        self,
        *,
        assignment: UserGroupAssignment,
        source_group_id: Any,
        target_group_id: Any,
        reason: str,
    ) -> RotationExecutionResult:
        try:
            target_group = self._get_upstream_group(target_group_id)
            response = self.sub2api_client.replace_exclusive_user_group(
                user_id=assignment.user_id,
                old_group_id=source_group_id,
                new_group_id=target_group_id,
            )
        except Sub2APIError as exc:
            logger.exception("Rollback failed for user_id=%s", assignment.user_id)
            return self._record_result(
                assignment=assignment,
                source_group_id=source_group_id,
                target_group_id=target_group_id,
                trigger_type=RotationTrigger.manual,
                status=RotationResultStatus.failed,
                reason=f"Upstream rollback replace-group failed: {exc}",
            )

        now = datetime.now(timezone.utc)
        updated_assignment = UserGroupAssignment(
            user_id=assignment.user_id,
            email=assignment.email,
            current_group_id=target_group_id,
            current_group_name=target_group["name"],
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
            source_group_id=source_group_id,
            target_group_id=target_group_id,
            trigger_type=RotationTrigger.manual,
            status=RotationResultStatus.moved,
            reason=reason,
            migrated_keys=int(response.get("migrated_keys") or 0),
            metadata={
                "rollback": "true",
                "source_group_name": assignment.current_group_name or "",
                "target_group_name": target_group["name"],
            },
        )

    def _save_orchestration_run(
        self,
        *,
        run_kind: OrchestrationRunKind,
        tag: str,
        trigger_type: RotationTrigger,
        dry_run: bool,
        window: AutoRotationUsageWindow | None,
        synced: dict[str, int],
        config: dict[str, Any],
        planned: list[dict[str, Any]],
        moved: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
        failed: list[dict[str, Any]],
        dead_band_skipped: bool = False,
    ) -> OrchestrationRunRecord:
        now = datetime.now(timezone.utc)
        record = OrchestrationRunRecord(
            run_kind=run_kind,
            tag=tag,
            trigger_type=trigger_type,
            dry_run=dry_run,
            status=self._run_status(
                planned=planned,
                moved=moved,
                skipped=skipped,
                failed=failed,
            ),
            window=window,
            synced=synced,
            config=config,
            dead_band_skipped=dead_band_skipped,
            planned=planned,
            moved=moved,
            skipped=skipped,
            failed=failed,
            created_at=now,
            updated_at=now,
        )
        return self.store.save_orchestration_run(record)

    def _run_status(
        self,
        *,
        planned: list[dict[str, Any]],
        moved: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
        failed: list[dict[str, Any]],
    ) -> str:
        if failed and not moved and not planned:
            return "failed"
        if failed:
            return "partial_failed"
        if moved:
            return "moved"
        if planned:
            return "planned"
        if skipped:
            return "skipped"
        return "empty"

    def _metadata_text(self, item: dict[str, Any], key: str) -> str | None:
        metadata = item.get("metadata")
        if isinstance(metadata, dict) and metadata.get(key) not in (None, ""):
            return str(metadata[key])
        return None

    def _get_upstream_group(self, group_id: Any) -> dict[str, Any]:
        target_key = self._normalize_key(group_id)
        for group in self._latest_groups_snapshot():
            if self._normalize_key(group["id"]) == target_key:
                return group
        for group in self.sub2api_client.list_groups(platform="openai"):
            if self._normalize_key(group["id"]) == target_key:
                return group
        raise RotationTargetValidationError("Group was not found in upstream Sub2API")

    def _rotation_support(self, group: dict[str, Any]) -> tuple[bool, str | None]:
        if not group.get("is_exclusive", False):
            return False, "group is not exclusive"
        if group.get("is_subscription", False):
            return False, "subscription groups cannot be rotated with replace-group"
        return True, None

    def _landing_support(self, group: dict[str, Any]) -> tuple[bool, str | None]:
        if group.get("is_subscription", False):
            return False, "subscription groups cannot be used as landing groups"
        return True, None

    def _pool_candidate_groups(
        self,
        rotation_selected: dict[str, RotationPoolGroup],
        landing_selected: dict[str, RotationPoolGroup],
    ) -> list[dict[str, Any]]:
        groups = self._latest_groups_snapshot()
        if not groups:
            groups = self.sub2api_client.list_groups(platform="openai")

        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in groups:
            group_id = group.get("id")
            if group_id in (None, ""):
                continue
            group_key = self._normalize_key(group_id)
            if group_key in seen:
                continue
            seen.add(group_key)
            result.append(group)

        for pool_group in [*rotation_selected.values(), *landing_selected.values()]:
            group_key = self._normalize_key(pool_group.group_id)
            if group_key in seen:
                continue
            seen.add(group_key)
            result.append(
                {
                    "id": pool_group.group_id,
                    "name": pool_group.group_name,
                    "group_kind": pool_group.group_kind,
                    "platform": pool_group.platform,
                    "status": pool_group.status,
                    "is_exclusive": pool_group.is_exclusive,
                    "is_subscription": pool_group.is_subscription,
                }
            )

        return result

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

    def _initial_pool_group_loads(
        self,
        pool_groups: list[RotationPoolGroup],
        candidates: list[UsageRotationCandidate],
    ) -> GroupLoadState:
        fallback_loads = self._initial_pool_usage_loads(pool_groups, candidates)
        loads: dict[str, float] = {}
        sources: dict[str, str] = {}
        records: dict[str, GroupUsageSegmentRecord] = {}
        window = self._window_enum().value
        for group in pool_groups:
            key = self._normalize_key(group.group_id)
            record = self.store.get_group_usage_segment(group.group_id)
            usage_value = (
                record.usage_by_window.get(window)
                if record is not None
                else None
            )
            if usage_value is not None:
                loads[key] = float(usage_value)
                source = record.source_by_window.get(window) or "group_usage"
                sources[key] = f"group_usage:{source}"
                records[key] = record
            else:
                loads[key] = fallback_loads.get(key, 0.0)
                sources[key] = "candidate_sum"
        return GroupLoadState(loads=loads, sources=sources, records=records)

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
        improvement_delta: float = 0.0,
    ) -> tuple[RotationPoolGroup, dict[str, Any]]:
        assignment = candidate.assignment
        current_key = self._normalize_key(assignment.current_group_id)
        least_loaded = self._select_least_loaded_usage_group(pool_groups, loads)
        least_key = self._normalize_key(least_loaded.group_id)
        current_load = loads.get(current_key, 0.0)
        least_load = loads.get(least_key, 0.0)
        if current_key in loads and current_key != least_key:
            before_gap = current_load - least_load
            after_gap = abs((current_load - candidate.usage_value) - (least_load + candidate.usage_value))
            if before_gap > 0 and after_gap < before_gap - improvement_delta:
                return least_loaded, {
                    "selection_reason": "move_reduces_group_load_spread",
                    "before_gap": before_gap,
                    "after_gap": after_gap,
                    "spread_improvement": before_gap - after_gap,
                }
        if current_key in loads:
            current_group = next(
                group
                for group in pool_groups
                if self._normalize_key(group.group_id) == current_key
            )
            return current_group, {
                "selection_reason": "no_group_load_spread_improvement",
                "before_gap": current_load - least_load,
                "after_gap": None,
                "spread_improvement": 0.0,
            }
        return least_loaded, {
            "selection_reason": "source_group_missing_from_pool",
            "before_gap": None,
            "after_gap": None,
            "spread_improvement": 0.0,
        }

    def _serialize_usage_loads(self, loads: dict[str, float]) -> str:
        return ",".join(f"{key}:{loads[key]:.6g}" for key in sorted(loads))

    def _group_load_metadata(
        self,
        loads: dict[str, float],
        sources: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        return {
            key: {
                "load": loads[key],
                "source": sources.get(key, "candidate_sum"),
            }
            for key in sorted(loads)
        }

    def _simulated_group_load_metadata(
        self,
        loads: dict[str, float],
        sources: dict[str, str],
        *,
        source_key: str,
        target_key: str,
        usage_value: float,
    ) -> dict[str, dict[str, Any]]:
        simulated = dict(loads)
        if source_key != target_key:
            if source_key in simulated:
                simulated[source_key] = max(0.0, simulated[source_key] - usage_value)
            if target_key in simulated:
                simulated[target_key] += usage_value
        return self._group_load_metadata(simulated, sources)

    def _build_usage_snapshot(self, user_id: Any) -> dict[str, Any]:
        usage_window = self._window_enum()
        segment = self.store.get_user_usage_segment(user_id)
        if segment is not None:
            usage_value = segment.usage_by_window.get(usage_window.value)
            return self._usage_snapshot_from_segment(segment, usage_window, usage_value)

        api_keys_response = self._user_api_keys_snapshot(user_id)
        has_api_keys = api_keys_response["total"] > 0
        usage_stats = self._user_usage_snapshot(user_id, usage_window)
        usage_value = self._usage_value_from_stats(usage_stats)

        return {
            "usage_window": usage_window,
            "usage_value": float(usage_value or 0.0),
            "usage_source": "user_usage" if usage_value is not None else "missing",
            "has_api_keys": has_api_keys,
            "api_key_count": api_keys_response["total"],
        }

    def _usage_snapshot_from_segment(
        self,
        segment: UserUsageSegmentRecord,
        usage_window: AutoRotationUsageWindow,
        usage_value: float | None,
    ) -> dict[str, Any]:
        return {
            "usage_window": usage_window,
            "usage_value": float(usage_value or 0.0),
            "usage_source": "usage_segmentation",
            "segment": segment.segment.value,
            "segment_label": segment.segment_label,
            "daily_average_by_window": segment.daily_average_by_window,
            "baseline_window": segment.baseline_window,
            "baseline_daily_average": segment.baseline_daily_average,
            "short_term_ratio": segment.short_term_ratio,
            "medium_term_ratio": segment.medium_term_ratio,
            "runway_days": segment.runway_days,
            "has_api_keys": bool(segment.has_api_keys),
            "api_key_count": segment.api_key_count,
            "refreshed_at": segment.refreshed_at.isoformat(),
        }

    def _usage_value_from_stats(self, stats: dict[str, Any]) -> float | None:
        for key in (
            "total_actual_cost",
            "total_cost",
            "actual_cost",
            "cost",
            "usage",
            "amount",
        ):
            value = stats.get(key)
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

    def _latest_groups_snapshot(self) -> list[dict[str, Any]]:
        payload = self._latest_operational_payload(SOURCE_GROUPS, default=[])
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _latest_users_snapshot(self) -> list[dict[str, Any]]:
        payload = self._latest_operational_payload(SOURCE_USERS, default=[])
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _user_api_keys_snapshot(self, user_id: Any) -> dict[str, Any]:
        payload = self._latest_operational_payload(SOURCE_USER_API_KEYS, default={})
        if not isinstance(payload, dict):
            return {"items": [], "total": 0}
        api_keys_payload = payload.get(str(user_id))
        if not isinstance(api_keys_payload, dict):
            return {"items": [], "total": 0}
        items = [
            item
            for item in api_keys_payload.get("items", [])
            if isinstance(item, dict)
        ]
        total = api_keys_payload.get("total")
        if not isinstance(total, int):
            total = len(items)
        return {"items": items, "total": total}

    def _user_usage_snapshot(
        self,
        user_id: Any,
        usage_window: AutoRotationUsageWindow,
    ) -> dict[str, Any]:
        payload = self._latest_operational_payload(SOURCE_USER_USAGE, default={})
        if not isinstance(payload, dict):
            return {}
        user_usage = payload.get(str(user_id))
        if not isinstance(user_usage, dict):
            return {}
        usage = user_usage.get(usage_window.value)
        if not isinstance(usage, dict) or usage.get("error"):
            return {}
        return usage

    def _latest_operational_payload(self, source_key: str, *, default: Any) -> Any:
        snapshot = self.store.get_latest_operational_data_snapshot(source_key)
        if snapshot is None:
            return default
        return snapshot.payload

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
            "imbalance_epsilon": config.imbalance_epsilon,
            "improvement_delta": config.improvement_delta,
            "schedule_source_group_ids": list(config.schedule_source_group_ids),
            "created_at": config.created_at.isoformat(),
            "updated_at": config.updated_at.isoformat(),
        }
