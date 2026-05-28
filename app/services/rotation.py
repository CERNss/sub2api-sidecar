from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from email_validator import EmailNotValidError, validate_email

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
    SOURCE_ACCOUNTS,
    SOURCE_GROUPS,
    SOURCE_USER_API_KEYS,
    SOURCE_USER_USAGE,
    SOURCE_USERS,
)
from app.stores.postgres import PostgresFlowStore

logger = logging.getLogger(__name__)
_DEFAULT_SOURCE_GROUP = object()
KEY_NAME_PATTERN = "service:environment:object:version:email"


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
class TargetGroupAvailability:
    groups_by_key: dict[str, dict[str, Any]]
    accounts_by_group_key: dict[str, list[dict[str, Any]]]


@dataclass
class KeyTransferItem:
    key_id: Any
    key_name: str | None
    key_value: str | None
    source_user_id: Any | None
    source_group_id: Any | None
    target_user_id: Any | None
    target_email: str | None
    target_group_id: Any | None
    status: RotationResultStatus
    reason: str
    quota: float | None = None


@dataclass(frozen=True)
class ParsedKeyName:
    service: str
    environment: str
    object: str
    version: str
    email: str


def parse_key_name(key_name: str | None) -> ParsedKeyName | None:
    if not key_name:
        return None
    parts = [part.strip() for part in key_name.split(":")]
    if len(parts) != 5 or any(not part for part in parts[:4]):
        return None
    suffix = parts[4]
    if not suffix or any(separator in suffix for separator in (",", ";", " ")):
        return None
    try:
        normalized = validate_email(suffix, check_deliverability=False).normalized
    except EmailNotValidError:
        return None
    return ParsedKeyName(
        service=parts[0],
        environment=parts[1],
        object=parts[2],
        version=parts[3],
        email=normalized.lower(),
    )


@dataclass
class KeyTransferRun:
    source_user_id: Any | None
    key_name_pattern: str
    dry_run: bool
    scope: str
    run_record: OrchestrationRunRecord | None
    items: list[KeyTransferItem]

    @property
    def planned_count(self) -> int:
        return sum(1 for item in self.items if item.status == RotationResultStatus.planned)

    @property
    def moved_count(self) -> int:
        return sum(1 for item in self.items if item.status == RotationResultStatus.moved)

    @property
    def skipped_count(self) -> int:
        return sum(1 for item in self.items if item.status == RotationResultStatus.skipped)

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.items if item.status == RotationResultStatus.failed)


@dataclass
class GroupMigrationRun:
    source_group_id: Any
    target_group_id: Any
    run_record: OrchestrationRunRecord


@dataclass
class _PreconditionBlock:
    status: RotationResultStatus
    reason: str


@dataclass
class _GroupMigrationCandidate:
    user: dict[str, Any]
    direct_group_id: Any | None
    direct_group_name: str | None
    source_api_key_count: int


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
        source_group_id: Any | None,
        target_group_id: Any,
        reason: str | None = None,
    ) -> RotationExecutionResult:
        if target_group_id in (None, ""):
            raise RotationTargetValidationError("Target group is required")

        direct_group_id, direct_group_name = self._get_direct_user_group(user_id)
        effective_source_group_id = source_group_id
        if direct_group_id not in (None, ""):
            if source_group_id in (None, ""):
                effective_source_group_id = direct_group_id
            elif self._normalize_key(source_group_id) != self._normalize_key(direct_group_id):
                raise RotationTargetValidationError(
                    "Source group must match the selected user's direct current group"
                )
        elif source_group_id not in (None, ""):
            raise RotationTargetValidationError(
                "Selected user does not have a direct current group; source group cannot be inferred"
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
            current_group_id=effective_source_group_id,
            current_group_name=direct_group_name or (existing.current_group_name if existing else None),
            assignment_mode=AssignmentMode.managed_pool,
            last_rotation_at=existing.last_rotation_at if existing else None,
            last_decision_reason=existing.last_decision_reason if existing else None,
            has_api_keys=existing.has_api_keys if existing else None,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

        request_reason = reason or "existing user/group orchestration"
        if self._normalize_key(effective_source_group_id) == self._normalize_key(target_group_id):
            try:
                resource_sync = self._sync_user_resources_to_group(
                    user_id=user_id,
                    email=email,
                    target_group_id=target_group_id,
                )
            except Sub2APIError as exc:
                logger.exception("Existing assignment resource sync failed for user_id=%s", user_id)
                return self._record_result(
                    assignment=assignment,
                    source_group_id=effective_source_group_id,
                    target_group_id=target_group_id,
                    trigger_type=RotationTrigger.manual,
                    status=RotationResultStatus.failed,
                    reason=f"Upstream resource sync failed: {exc}",
                )

            assignment.current_group_id = target_group_id
            assignment.current_group_name = target_group["name"]
            resource_changes = resource_sync["migrated_keys"] + resource_sync["bound_accounts"]
            assignment.last_decision_reason = (
                "Target group matches the current assignment"
                if resource_changes == 0
                else "Target group matches the current assignment; synchronized user resources"
            )
            if resource_changes > 0:
                assignment.last_rotation_at = now
            assignment.updated_at = now
            self.store.upsert_user_assignment(assignment)
            return self._record_result(
                assignment=assignment,
                source_group_id=effective_source_group_id,
                target_group_id=target_group_id,
                trigger_type=RotationTrigger.manual,
                status=(
                    RotationResultStatus.skipped
                    if resource_changes == 0
                    else RotationResultStatus.moved
                ),
                reason=assignment.last_decision_reason,
                migrated_keys=resource_sync["migrated_keys"],
                metadata={"bound_accounts": resource_sync["bound_accounts"]},
            )

        metadata: dict[str, Any] | None = None
        try:
            if effective_source_group_id in (None, ""):
                self.sub2api_client.set_user_group(
                    user_id=user_id,
                    group_id=target_group_id,
                )
                resource_sync = self._sync_user_resources_to_group(
                    user_id=user_id,
                    email=email,
                    target_group_id=target_group_id,
                    exclude_source_group_id=source_group_id,
                )
                migrated_keys = resource_sync["migrated_keys"]
                metadata = {"bound_accounts": resource_sync["bound_accounts"]}
            else:
                response = self.sub2api_client.replace_exclusive_user_group(
                    user_id=user_id,
                    old_group_id=effective_source_group_id,
                    new_group_id=target_group_id,
                )
                resource_sync = self._sync_user_resources_to_group(
                    user_id=user_id,
                    email=email,
                    target_group_id=target_group_id,
                    only_unassigned_keys=True,
                )
                migrated_keys = int(response.get("migrated_keys") or 0) + resource_sync["migrated_keys"]
                metadata = {
                    "bound_accounts": resource_sync["bound_accounts"],
                    "supplemental_migrated_keys": resource_sync["migrated_keys"],
                }
        except Sub2APIError as exc:
            logger.exception("Existing assignment orchestration failed for user_id=%s", user_id)
            return self._record_result(
                assignment=assignment,
                source_group_id=effective_source_group_id,
                target_group_id=target_group_id,
                trigger_type=RotationTrigger.manual,
                status=RotationResultStatus.failed,
                reason=f"Upstream group assignment failed: {exc}",
            )

        assignment.current_group_id = target_group_id
        assignment.current_group_name = target_group["name"]
        assignment.last_rotation_at = now
        assignment.last_decision_reason = request_reason
        assignment.updated_at = now
        self.store.upsert_user_assignment(assignment)
        return self._record_result(
            assignment=assignment,
            source_group_id=effective_source_group_id,
            target_group_id=target_group_id,
            trigger_type=RotationTrigger.manual,
            status=RotationResultStatus.moved,
            reason=request_reason,
            migrated_keys=migrated_keys,
            metadata=metadata,
        )

    def migrate_group_assignments(
        self,
        *,
        source_group_id: Any,
        target_group_id: Any,
        reason: str | None = None,
    ) -> GroupMigrationRun:
        if source_group_id in (None, ""):
            raise RotationTargetValidationError("Source group is required")
        if target_group_id in (None, ""):
            raise RotationTargetValidationError("Target group is required")
        if self._normalize_key(source_group_id) == self._normalize_key(target_group_id):
            raise RotationTargetValidationError("Source and target groups must be different")

        source_group = self._get_upstream_group(source_group_id)
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

        users = self.sub2api_client.list_users()
        source_group_key = self._normalize_key(source_group_id)
        target_direct_user_count_before = self._direct_user_count_for_group(
            users,
            target_group_id,
        )
        migration_mode = "merge" if target_direct_user_count_before > 0 else "move"
        source_candidates = self._group_migration_candidates(
            users,
            source_group_id,
        )
        source_candidates.sort(
            key=lambda candidate: (
                self._normalize_email(candidate.user.get("email")),
                self._normalize_key(candidate.user.get("id")),
            )
        )

        moved: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        request_reason = reason or "group to group orchestration"
        now = datetime.now(timezone.utc)
        for candidate in source_candidates:
            user = candidate.user
            user_id = user.get("id")
            email = str(user.get("email") or "")
            if user_id in (None, "") or not email.strip():
                result = self._record_result(
                    assignment=UserGroupAssignment(
                        user_id=user_id or "",
                        email=email.strip() or "unknown@example.com",
                        current_group_id=source_group_id,
                        current_group_name=source_group.get("name"),
                        assignment_mode=AssignmentMode.managed_pool,
                    ),
                    source_group_id=source_group_id,
                    target_group_id=target_group_id,
                    trigger_type=RotationTrigger.manual,
                    status=RotationResultStatus.skipped,
                    reason="User id or email is missing",
                    metadata={
                        "source_group_name": source_group.get("name") or "",
                        "target_group_name": target_group.get("name") or "",
                    },
                )
                skipped.append(self._serialize_result(result))
                continue

            existing = self.store.get_user_assignment(user_id)
            direct_group_id = candidate.direct_group_id
            direct_group_name = candidate.direct_group_name
            assignment = UserGroupAssignment(
                user_id=user_id,
                email=email,
                current_group_id=direct_group_id or source_group_id,
                current_group_name=direct_group_name or source_group.get("name"),
                assignment_mode=existing.assignment_mode if existing else AssignmentMode.managed_pool,
                last_rotation_at=existing.last_rotation_at if existing else None,
                last_decision_reason=existing.last_decision_reason if existing else None,
                has_api_keys=existing.has_api_keys if existing else None,
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )

            try:
                direct_source_match = self._normalize_key(direct_group_id) == source_group_key
                if direct_source_match:
                    response = self.sub2api_client.replace_exclusive_user_group(
                        user_id=user_id,
                        old_group_id=source_group_id,
                        new_group_id=target_group_id,
                    )
                    upstream_migrated_keys = int(response.get("migrated_keys") or 0)
                    source_match = "direct_group"
                else:
                    self.sub2api_client.set_user_group(
                        user_id=user_id,
                        group_id=target_group_id,
                    )
                    upstream_migrated_keys = 0
                    source_match = "api_key_route"
                resource_sync = self._sync_user_resources_to_group(
                    user_id=user_id,
                    email=email,
                    target_group_id=target_group_id,
                )
            except Sub2APIError as exc:
                logger.exception("Group assignment migration failed for user_id=%s", user_id)
                result = self._record_result(
                    assignment=assignment,
                    source_group_id=source_group_id,
                    target_group_id=target_group_id,
                    trigger_type=RotationTrigger.manual,
                    status=RotationResultStatus.failed,
                    reason=f"Upstream group assignment failed: {exc}",
                    metadata={
                        "source_group_name": source_group.get("name") or "",
                        "target_group_name": target_group.get("name") or "",
                    },
                )
                failed.append(self._serialize_result(result))
                continue

            updated_assignment = UserGroupAssignment(
                user_id=user_id,
                email=email,
                current_group_id=target_group_id,
                current_group_name=target_group.get("name"),
                assignment_mode=AssignmentMode.managed_pool,
                last_rotation_at=now,
                last_decision_reason=request_reason,
                has_api_keys=True,
                created_at=assignment.created_at,
                updated_at=now,
            )
            self.store.upsert_user_assignment(updated_assignment)
            result = self._record_result(
                assignment=updated_assignment,
                source_group_id=source_group_id,
                target_group_id=target_group_id,
                trigger_type=RotationTrigger.manual,
                status=RotationResultStatus.moved,
                reason=request_reason,
                migrated_keys=upstream_migrated_keys + resource_sync["migrated_keys"],
                metadata={
                    "source_group_name": source_group.get("name") or "",
                    "target_group_name": target_group.get("name") or "",
                    "bound_accounts": resource_sync["bound_accounts"],
                    "supplemental_migrated_keys": resource_sync["migrated_keys"],
                    "source_match": source_match,
                    "source_api_key_count": candidate.source_api_key_count,
                    "migration_mode": migration_mode,
                },
            )
            moved.append(self._serialize_result(result))

        run_record = self._save_orchestration_run(
            run_kind=OrchestrationRunKind.manual,
            tag="manual_group_migration",
            trigger_type=RotationTrigger.manual,
            dry_run=False,
            window=None,
            synced={},
            config={
                "source_group_id": str(source_group_id),
                "source_group_name": str(source_group.get("name") or ""),
                "target_group_id": str(target_group_id),
                "target_group_name": str(target_group.get("name") or ""),
                "reason": reason or "",
                "mode": migration_mode,
                "target_direct_user_count_before": target_direct_user_count_before,
            },
            planned=[],
            moved=moved,
            skipped=skipped,
            failed=failed,
        )
        return GroupMigrationRun(
            source_group_id=source_group_id,
            target_group_id=target_group_id,
            run_record=run_record,
        )

    def _direct_user_count_for_group(
        self,
        users: list[dict[str, Any]],
        group_id: Any,
    ) -> int:
        group_key = self._normalize_key(group_id)
        return sum(
            1
            for user in users
            if self._normalize_key(self._direct_user_group_from_item(user)[0]) == group_key
        )

    def _group_migration_candidates(
        self,
        users: list[dict[str, Any]],
        source_group_id: Any,
    ) -> list[_GroupMigrationCandidate]:
        source_group_key = self._normalize_key(source_group_id)
        candidates: list[_GroupMigrationCandidate] = []
        for user in users:
            direct_group_id, direct_group_name = self._direct_user_group_from_item(user)
            direct_source_match = self._normalize_key(direct_group_id) == source_group_key
            source_api_key_count = 0
            if direct_source_match:
                candidates.append(
                    _GroupMigrationCandidate(
                        user=user,
                        direct_group_id=direct_group_id,
                        direct_group_name=direct_group_name,
                        source_api_key_count=source_api_key_count,
                    )
                )
                continue
            user_id = user.get("id")
            if user_id not in (None, ""):
                api_keys = self.sub2api_client.get_user_api_keys(user_id)["items"]
                source_api_key_count = sum(
                    1
                    for api_key in api_keys
                    if isinstance(api_key, dict)
                    and self._normalize_key(self._api_key_group_id(api_key)) == source_group_key
                )
            if source_api_key_count > 0:
                candidates.append(
                    _GroupMigrationCandidate(
                        user=user,
                        direct_group_id=direct_group_id,
                        direct_group_name=direct_group_name,
                        source_api_key_count=source_api_key_count,
                    )
                )
        return candidates

    def _sync_user_resources_to_group(
        self,
        *,
        user_id: Any,
        email: str,
        target_group_id: Any,
        only_unassigned_keys: bool = False,
        exclude_source_group_id: Any | None = None,
    ) -> dict[str, int]:
        api_keys = self.sub2api_client.get_user_api_keys(user_id)["items"]
        if only_unassigned_keys:
            api_keys = [
                api_key
                for api_key in api_keys
                if isinstance(api_key, dict)
                and self._api_key_group_id(api_key) in (None, "")
            ]
        elif exclude_source_group_id not in (None, ""):
            source_group_key = self._normalize_key(exclude_source_group_id)
            api_keys = [
                api_key
                for api_key in api_keys
                if isinstance(api_key, dict)
                and self._normalize_key(self._api_key_group_id(api_key)) != source_group_key
            ]
        bound_accounts = self._bind_user_api_key_accounts_to_group(
            api_keys=api_keys,
            email=email,
            target_group_id=target_group_id,
        )
        migrated_keys = self._move_api_keys_to_group(
            api_keys=api_keys,
            target_group_id=target_group_id,
        )
        return {"migrated_keys": migrated_keys, "bound_accounts": bound_accounts}

    def _move_api_keys_to_group(
        self,
        *,
        api_keys: list[dict[str, Any]],
        target_group_id: Any,
    ) -> int:
        moved = 0
        for api_key in api_keys:
            if not isinstance(api_key, dict):
                continue
            key_id = api_key.get("id") or api_key.get("key_id")
            if key_id in (None, ""):
                continue
            if self._normalize_key(self._api_key_group_id(api_key)) == self._normalize_key(target_group_id):
                continue
            self.sub2api_client.update_api_key_group(
                key_id=key_id,
                group_id=target_group_id,
            )
            moved += 1
        return moved

    def _bind_user_api_key_accounts_to_group(
        self,
        *,
        api_keys: list[dict[str, Any]],
        email: str,
        target_group_id: Any,
    ) -> int:
        if not api_keys:
            return 0

        accounts = self.sub2api_client.list_openai_accounts()
        accounts_by_key = {
            self._normalize_key(account.get("id")): account
            for account in accounts
            if account.get("id") not in (None, "")
        }
        explicit_account_keys = [
            account_key
            for api_key in api_keys
            if isinstance(api_key, dict)
            for account_key in self._api_key_account_keys(api_key)
        ]
        named_account_keys = [
            self._normalize_key(account["id"])
            for api_key in api_keys
            if isinstance(api_key, dict)
            for account_name in self._api_key_account_names(api_key)
            for account in accounts
            if account.get("id") not in (None, "")
            and self._account_matches_text(account, account_name)
        ]
        candidate_account_keys = [*explicit_account_keys, *named_account_keys]
        if not candidate_account_keys:
            candidate_account_keys.extend(
                self._normalize_key(account["id"])
                for account in accounts
                if account.get("id") not in (None, "")
                and self._account_matches_email(account, email)
            )

        bound_accounts = 0
        seen_account_keys: set[str] = set()
        for account_key in candidate_account_keys:
            if not account_key or account_key in seen_account_keys:
                continue
            seen_account_keys.add(account_key)
            account = accounts_by_key.get(account_key)
            account_id = account.get("id") if account else account_key
            if account and self._account_has_group(account, target_group_id):
                continue
            self.sub2api_client.bind_account_to_group(account_id, target_group_id)
            bound_accounts += 1
        return bound_accounts

    def _api_key_group_id(self, api_key: dict[str, Any]) -> Any | None:
        for field_name in ("group_id", "current_group_id", "groupId", "currentGroupId"):
            value = api_key.get(field_name)
            if value not in (None, ""):
                return value

        for field_name in ("group", "current_group", "currentGroup"):
            raw_group = api_key.get(field_name)
            if isinstance(raw_group, dict):
                value = raw_group.get("id") or raw_group.get("group_id") or raw_group.get("groupId")
                if value not in (None, ""):
                    return value

        raw_payload = api_key.get("raw")
        if isinstance(raw_payload, dict) and raw_payload is not api_key:
            return self._api_key_group_id(raw_payload)
        return None

    def _api_key_account_keys(self, api_key: dict[str, Any]) -> list[str]:
        account_ids: list[str] = []

        def add(value: Any) -> None:
            if value in (None, ""):
                return
            account_key = self._normalize_key(value)
            if account_key and account_key not in account_ids:
                account_ids.append(account_key)

        for field_name in (
            "account_id",
            "accountId",
            "openai_account_id",
            "openaiAccountId",
            "oauth_account_id",
            "oauthAccountId",
            "upstream_account_id",
            "upstreamAccountId",
            "provider_account_id",
            "providerAccountId",
        ):
            add(api_key.get(field_name))

        for field_name in ("account", "openai_account", "openaiAccount", "oauth_account", "oauthAccount"):
            raw_account = api_key.get(field_name)
            if isinstance(raw_account, dict):
                add(raw_account.get("id") or raw_account.get("account_id") or raw_account.get("accountId"))

        for field_name in ("accounts", "openai_accounts", "openaiAccounts"):
            raw_accounts = api_key.get(field_name)
            if not isinstance(raw_accounts, list):
                continue
            for raw_account in raw_accounts:
                if isinstance(raw_account, dict):
                    add(raw_account.get("id") or raw_account.get("account_id") or raw_account.get("accountId"))
                else:
                    add(raw_account)

        raw_payload = api_key.get("raw")
        if isinstance(raw_payload, dict):
            for account_key in self._api_key_account_keys(raw_payload):
                add(account_key)
        return account_ids

    def _api_key_account_names(self, api_key: dict[str, Any]) -> list[str]:
        names: list[str] = []

        def add(value: Any) -> None:
            text = self._text_or_none(value)
            if text and text not in names:
                names.append(text)

        for field_name in (
            "account_name",
            "accountName",
            "openai_account_name",
            "openaiAccountName",
            "oauth_account_name",
            "oauthAccountName",
        ):
            add(api_key.get(field_name))

        for field_name in ("account", "openai_account", "openaiAccount", "oauth_account", "oauthAccount"):
            raw_account = api_key.get(field_name)
            if isinstance(raw_account, dict):
                add(raw_account.get("name") or raw_account.get("account_name") or raw_account.get("accountName"))

        raw_payload = api_key.get("raw")
        if isinstance(raw_payload, dict):
            for account_name in self._api_key_account_names(raw_payload):
                add(account_name)
        return names

    def _account_has_group(self, account: dict[str, Any], group_id: Any) -> bool:
        group_key = self._normalize_key(group_id)
        return any(self._normalize_key(value) == group_key for value in self._account_group_ids(account))

    def _account_group_ids(self, account: dict[str, Any]) -> list[Any]:
        group_ids: list[Any] = []

        def add(value: Any) -> None:
            if value in (None, ""):
                return
            if not any(self._normalize_key(existing) == self._normalize_key(value) for existing in group_ids):
                group_ids.append(value)

        for field_name in (
            "group_id",
            "groupId",
            "current_group_id",
            "currentGroupId",
            "default_group_id",
            "defaultGroupId",
            "bound_group_id",
            "boundGroupId",
        ):
            add(account.get(field_name))

        for field_name in ("group", "current_group", "currentGroup", "default_group", "defaultGroup"):
            raw_group = account.get(field_name)
            if isinstance(raw_group, dict):
                add(raw_group.get("id") or raw_group.get("group_id") or raw_group.get("groupId"))

        for field_name in ("binding", "bindings", "account_group", "accountGroup"):
            raw_binding = account.get(field_name)
            raw_bindings = raw_binding if isinstance(raw_binding, list) else [raw_binding]
            for binding in raw_bindings:
                if isinstance(binding, dict):
                    add(binding.get("group_id") or binding.get("groupId"))

        for field_name in (
            "groups",
            "group_ids",
            "groupIds",
            "allowed_groups",
            "allowedGroups",
            "bound_groups",
            "boundGroups",
            "bind_groups",
            "bindGroups",
        ):
            raw_groups = account.get(field_name)
            if not isinstance(raw_groups, list):
                continue
            for raw_group in raw_groups:
                if isinstance(raw_group, dict):
                    add(raw_group.get("id") or raw_group.get("group_id") or raw_group.get("groupId"))
                else:
                    add(raw_group)

        raw_payload = account.get("raw")
        if isinstance(raw_payload, dict) and raw_payload is not account:
            for group_id in self._account_group_ids(raw_payload):
                add(group_id)
        return group_ids

    def _account_matches_email(self, account: dict[str, Any], email: str) -> bool:
        normalized_email = self._normalize_email(email)
        if not normalized_email:
            return False
        return self._account_matches_text(account, normalized_email)

    def _account_matches_text(self, account: dict[str, Any], text: str) -> bool:
        needle = self._normalize_email(text)
        if not needle:
            return False
        raw = account.get("raw")
        candidates = [
            account.get("name"),
            account.get("email"),
        ]
        if isinstance(raw, dict):
            candidates.extend(
                [
                    raw.get("name"),
                    raw.get("email"),
                    raw.get("account_name"),
                    raw.get("account_email"),
                    raw.get("login_email"),
                    self._nested_value(raw, "extra.email"),
                    self._nested_value(raw, "credentials.email"),
                ]
            )
        return any(self._normalize_email(candidate) == needle for candidate in candidates)

    def _nested_value(self, payload: dict[str, Any], path: str) -> Any | None:
        current: Any = payload
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    def _get_direct_user_group(self, user_id: Any) -> tuple[Any | None, str | None]:
        for user in self._latest_users_snapshot():
            if self._normalize_key(user.get("id")) == self._normalize_key(user_id):
                return user.get("current_group_id"), user.get("current_group_name")
        return None, None

    def _direct_user_group_from_item(self, user: dict[str, Any]) -> tuple[Any | None, str | None]:
        raw = user.get("raw")
        if isinstance(raw, dict):
            for field_name in (
                "group_id",
                "groupId",
                "current_group_id",
                "currentGroupId",
                "default_group_id",
                "defaultGroupId",
            ):
                value = raw.get(field_name)
                if value not in (None, ""):
                    return value, self._direct_user_group_name(raw)
            for field_name in ("group", "current_group", "currentGroup", "default_group", "defaultGroup"):
                group = raw.get(field_name)
                if isinstance(group, dict):
                    group_id = group.get("id") or group.get("group_id") or group.get("groupId")
                    if group_id not in (None, ""):
                        return group_id, group.get("name") or group.get("group_name") or group.get("groupName")
            return None, self._direct_user_group_name(raw)

        current_group_id = user.get("current_group_id")
        if current_group_id not in (None, ""):
            return current_group_id, user.get("current_group_name")
        return None, user.get("current_group_name")

    def _direct_user_group_name(self, payload: dict[str, Any]) -> str | None:
        for field_name in (
            "group_name",
            "groupName",
            "current_group_name",
            "currentGroupName",
            "default_group_name",
            "defaultGroupName",
        ):
            value = payload.get(field_name)
            if value not in (None, ""):
                return str(value)
        return None

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

    def transfer_admin_api_keys(
        self,
        *,
        source_user_id: Any | None = None,
        key_ids: list[Any] | None = None,
        dry_run: bool = False,
        reason: str | None = None,
        scope: str = "admin",
    ) -> KeyTransferRun:
        scope_key = scope.strip().lower() if scope else "admin"
        if scope_key not in {"admin", "all_users"}:
            raise RotationExecutionError("Unsupported key transfer scope")
        if scope_key == "all_users" and source_user_id not in (None, ""):
            scope_key = "admin"
        if scope_key == "all_users":
            source_user_id = None
            source_user_key = None
            source_keys = self.sub2api_client.list_all_user_api_keys()["items"]
        else:
            if source_user_id in (None, ""):
                if key_ids is not None:
                    scope_key = "all_users"
                    source_user_key = None
                    source_keys = self.sub2api_client.list_all_user_api_keys()["items"]
                else:
                    admin_user_id = self._resolve_admin_user_id()
                    source_user_id = admin_user_id
                    source_user_key = self._normalize_key(source_user_id)
                    source_keys = self.sub2api_client.get_user_api_keys(source_user_id)["items"]
            else:
                source_user_key = self._normalize_key(source_user_id)
                source_keys = self.sub2api_client.get_user_api_keys(source_user_id)["items"]
        selected_key_ids = self._normalize_selected_key_ids(key_ids)
        if selected_key_ids is not None:
            source_keys = [
                key_item
                for key_item in source_keys
                if isinstance(key_item, dict)
                and self._normalize_key(key_item.get("id") or key_item.get("key_id")) in selected_key_ids
            ]
        if scope_key == "all_users" and selected_key_ids is not None:
            source_keys = self._hydrate_selected_keys_from_owners(source_keys)
        target_emails = {
            parsed.email
            for key_item in source_keys
            if isinstance(key_item, dict)
            for parsed in [self._parse_transfer_key_name(self._text_or_none(key_item.get("name")))]
            if parsed is not None
        }
        users_by_email, duplicate_emails = self._users_by_exact_emails(target_emails)
        available_groups_by_key = self._available_groups_by_key()
        items: list[KeyTransferItem] = []
        for key_item in source_keys:
            item = self._plan_key_transfer(
                key_item,
                source_user_id=source_user_id,
                source_user_key=source_user_key,
                users_by_email=users_by_email,
                duplicate_emails=duplicate_emails,
                available_groups_by_key=available_groups_by_key,
            )
            if item.status == RotationResultStatus.planned and not dry_run:
                item = self._execute_key_transfer(item)
            items.append(item)

        run_record = self._save_key_transfer_run(
            items=items,
            dry_run=dry_run,
            reason=reason,
            scope=scope_key,
        )
        return KeyTransferRun(
            source_user_id=source_user_id,
            key_name_pattern=KEY_NAME_PATTERN,
            dry_run=dry_run,
            scope=scope_key,
            run_record=run_record,
            items=items,
        )

    def _hydrate_selected_keys_from_owners(
        self,
        source_keys: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        hydrated: list[dict[str, Any]] = []
        for key_item in source_keys:
            if not isinstance(key_item, dict):
                continue
            source_user_id = key_item.get("user_id") or key_item.get("owner_user_id")
            key_id = key_item.get("id") or key_item.get("key_id")
            if source_user_id in (None, "") or key_id in (None, ""):
                hydrated.append(key_item)
                continue
            user_keys = self.sub2api_client.get_user_api_keys(source_user_id)["items"]
            replacement = next(
                (
                    user_key
                    for user_key in user_keys
                    if isinstance(user_key, dict)
                    and self._normalize_key(user_key.get("id") or user_key.get("key_id"))
                    == self._normalize_key(key_id)
                ),
                None,
            )
            if replacement is None:
                hydrated.append(key_item)
                continue
            hydrated_key = dict(replacement)
            hydrated_key.setdefault("user_id", source_user_id)
            hydrated_key.setdefault("owner_user_id", source_user_id)
            hydrated_key.setdefault("owner_email", key_item.get("owner_email"))
            hydrated.append(hydrated_key)
        return hydrated

    def migrate_rotom_keys(
        self,
        *,
        source_user_id: Any | None = None,
        key_ids: list[Any] | None = None,
        dry_run: bool = False,
        reason: str | None = None,
    ) -> KeyTransferRun:
        return self.transfer_admin_api_keys(
            source_user_id=source_user_id,
            key_ids=key_ids,
            dry_run=dry_run,
            reason=reason,
            scope="admin",
        )

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
        target_availability = self._target_group_availability()

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
                        availability=target_availability,
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
                        availability=target_availability,
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
                    availability=target_availability,
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
                    availability=target_availability,
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
        availability: TargetGroupAvailability | None = None,
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
        availability_check = self._target_group_availability_block(
            target_group_id,
            availability=availability,
        )
        if availability_check is not None:
            return target_group, availability_check
        return target_group, None

    def _execute_rotation(
        self,
        *,
        assignment: UserGroupAssignment,
        target_group_id: Any,
        availability: TargetGroupAvailability | None = None,
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
            availability=availability,
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
        availability: TargetGroupAvailability | None = None,
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
            availability=availability,
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
        source_group_id: Any | None | object = _DEFAULT_SOURCE_GROUP,
        migrated_keys: int = 0,
        usage_window: AutoRotationUsageWindow | None = None,
        usage_value: float | None = None,
        usage_snapshot: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RotationExecutionResult:
        recorded_source_group_id = (
            assignment.current_group_id
            if source_group_id is _DEFAULT_SOURCE_GROUP
            else source_group_id
        )
        result = RotationExecutionResult(
            user_id=assignment.user_id,
            email=assignment.email,
            source_group_id=recorded_source_group_id,
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

    def _users_by_exact_emails(
        self,
        emails: set[str],
    ) -> tuple[dict[str, dict[str, Any]], set[str]]:
        users_by_email: dict[str, dict[str, Any]] = {}
        duplicate_emails: set[str] = set()
        for email in sorted(emails):
            email_key = self._normalize_email(email)
            exact_matches = [
                user
                for user in self.sub2api_client.list_users(email=email)
                if self._normalize_email(user.get("email")) == email_key
            ]
            unique_matches: list[dict[str, Any]] = []
            seen_user_ids: set[str] = set()
            for user in exact_matches:
                user_key = self._normalize_key(user.get("id"))
                if user_key in seen_user_ids:
                    continue
                seen_user_ids.add(user_key)
                unique_matches.append(user)
            if len(unique_matches) == 1:
                users_by_email[email_key] = unique_matches[0]
            elif len(unique_matches) > 1:
                duplicate_emails.add(email_key)
        return users_by_email, duplicate_emails

    def users_by_exact_emails(
        self,
        emails: set[str],
    ) -> tuple[dict[str, dict[str, Any]], set[str]]:
        return self._users_by_exact_emails(emails)

    def _available_groups_by_key(self) -> dict[str, dict[str, Any]]:
        groups = self.sub2api_client.list_groups(
            platform=self.sub2api_client.provisioning_defaults.group_platform
        )
        available: dict[str, dict[str, Any]] = {}
        for group in groups:
            group_id = group.get("id")
            if group_id in (None, ""):
                continue
            status = str(group.get("status") or "active").strip().lower()
            if status and status != "active":
                continue
            available[self._normalize_key(group_id)] = group
        return available

    def _target_group_availability(self) -> TargetGroupAvailability:
        groups = self._latest_groups_snapshot()
        if not groups:
            groups = self.sub2api_client.list_groups(
                platform=self.sub2api_client.provisioning_defaults.group_platform
            )
        accounts = self._latest_accounts_snapshot()
        if not accounts:
            accounts = self.sub2api_client.list_openai_accounts()

        groups_by_key = {
            self._normalize_key(group.get("id")): group
            for group in groups
            if isinstance(group, dict) and group.get("id") not in (None, "")
        }
        accounts_by_group_key: dict[str, list[dict[str, Any]]] = {}
        for account in accounts:
            if not isinstance(account, dict):
                continue
            for group_id in self._account_group_ids(account):
                group_key = self._normalize_key(group_id)
                if group_key:
                    accounts_by_group_key.setdefault(group_key, []).append(account)
        return TargetGroupAvailability(
            groups_by_key=groups_by_key,
            accounts_by_group_key=accounts_by_group_key,
        )

    def _target_group_availability_block(
        self,
        target_group_id: Any,
        *,
        availability: TargetGroupAvailability | None = None,
    ) -> _PreconditionBlock | None:
        availability = availability or self._target_group_availability()
        target_key = self._normalize_key(target_group_id)
        if target_key not in availability.groups_by_key:
            return _PreconditionBlock(
                RotationResultStatus.failed,
                "Target group does not exist in upstream Sub2API",
            )

        accounts = availability.accounts_by_group_key.get(target_key, [])
        if not accounts:
            return _PreconditionBlock(
                RotationResultStatus.failed,
                "Target group has no upstream accounts",
            )

        schedulable_accounts = [
            account
            for account in accounts
            if self._account_is_schedulable_for_rotation(account)
        ]
        if not schedulable_accounts:
            return _PreconditionBlock(
                RotationResultStatus.failed,
                "Target group has no schedulable upstream accounts",
            )
        return None

    def _account_is_schedulable_for_rotation(self, account: dict[str, Any]) -> bool:
        if self._account_bool_value(
            account,
            "temporary_unschedulable",
            "temporarily_unschedulable",
            "is_temporary_unschedulable",
            "unschedulable",
        ) is True:
            return False
        if self._account_bool_value(account, "rate_limited", "is_rate_limited", "limited") is True:
            return False
        if self._account_bool_value(account, "disabled", "is_disabled") is True:
            return False

        explicit_available = self._account_bool_value(
            account,
            "is_available",
            "available",
            "availability.is_available",
            "availability.available",
        )
        schedulable = self._account_bool_value(
            account,
            "schedulable",
            "is_schedulable",
            "availability.schedulable",
        )
        enabled = self._account_bool_value(account, "enabled", "is_enabled")
        if explicit_available is False:
            return False
        if schedulable is False:
            return False
        if enabled is False:
            return False

        status_key = self._normalize_key(
            account.get("availability_status")
            or self._account_nested_value(account, "availability.status", "availability.state")
            or account.get("status")
        ).lower()
        if status_key in {
            "temporary_unschedulable",
            "temporarily_unschedulable",
            "rate_limited",
            "ratelimited",
            "overloaded",
            "overload",
            "unavailable",
            "disabled",
            "banned",
            "needs_reauth",
            "requires_reauth",
            "needs_verify",
            "requires_verify",
        }:
            return False
        if explicit_available is True or schedulable is True:
            return True
        return status_key in {"available", "active", "ok", "healthy", "enabled", "ready", "normal"}

    def _account_bool_value(self, account: dict[str, Any], *field_names: str) -> bool | None:
        for field_name in field_names:
            value = self._account_nested_value(account, field_name)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                text = value.strip().lower()
                if text in {"1", "true", "yes", "y", "on", "available", "active", "enabled"}:
                    return True
                if text in {"0", "false", "no", "n", "off", "unavailable", "inactive", "disabled"}:
                    return False
            if isinstance(value, (int, float)):
                return value != 0

        raw_payload = account.get("raw")
        if isinstance(raw_payload, dict) and raw_payload is not account:
            return self._account_bool_value(raw_payload, *field_names)
        return None

    def _account_nested_value(self, account: dict[str, Any], *field_names: str) -> Any:
        for field_name in field_names:
            current: Any = account
            for part in field_name.split("."):
                if not isinstance(current, dict):
                    current = None
                    break
                current = current.get(part)
            if current not in (None, ""):
                return current
        return None

    def first_available_group_id_for_user(self, user: dict[str, Any]) -> Any | None:
        return self._first_available_user_group_id(user, self._available_groups_by_key())

    def available_groups_by_key(self) -> dict[str, dict[str, Any]]:
        return self._available_groups_by_key()

    def _resolve_admin_user_id(self) -> Any:
        candidates: list[dict[str, Any]] = []
        seen_user_ids: set[str] = set()
        for search_term in ("admin", self.settings.app_auth_username):
            for user in self.sub2api_client.list_users(email=search_term):
                user_id = user.get("id")
                if user_id in (None, ""):
                    continue
                user_key = self._normalize_key(user_id)
                if not user_key or user_key in seen_user_ids:
                    continue
                seen_user_ids.add(user_key)
                username = self._normalize_key(user.get("username")).strip().lower()
                email_local = self._normalize_email(user.get("email")).split("@", 1)[0]
                name = self._normalize_key(user.get("name")).strip().lower()
                display_name = self._normalize_key(user.get("display_name")).strip().lower()
                auth_username = self.settings.app_auth_username.strip().lower()
                if auth_username in {username, email_local, name, display_name}:
                    candidates.append(user)
        if len(candidates) == 1:
            return candidates[0]["id"]
        if not candidates:
            raise RotationExecutionError("Admin source user was not found")
        raise RotationExecutionError("Admin source user is ambiguous")

    def resolve_admin_user_id(self) -> Any:
        return self._resolve_admin_user_id()

    def _plan_key_transfer(
        self,
        key_item: dict[str, Any],
        *,
        source_user_id: Any | None,
        source_user_key: str | None,
        users_by_email: dict[str, dict[str, Any]],
        duplicate_emails: set[str],
        available_groups_by_key: dict[str, dict[str, Any]],
    ) -> KeyTransferItem:
        key_id = key_item.get("id") or key_item.get("key_id")
        key_name = self._text_or_none(key_item.get("name"))
        key_value = self._text_or_none(key_item.get("key"))
        source_group_id = key_item.get("group_id") or key_item.get("current_group_id")
        source_key_user_id = key_item.get("user_id", source_user_id)
        if source_key_user_id in (None, ""):
            source_key_user_id = key_item.get("owner_user_id", source_user_id)
        if key_id in (None, ""):
            return KeyTransferItem(
                key_id=None,
                key_name=key_name,
                key_value=key_value,
                source_user_id=source_user_id,
                source_group_id=source_group_id,
                target_user_id=None,
                target_email=None,
                target_group_id=None,
                status=RotationResultStatus.skipped,
                reason="API key id is missing",
            )
        if source_user_key is not None and self._normalize_key(source_key_user_id) != source_user_key:
            return KeyTransferItem(
                key_id=key_id,
                key_name=key_name,
                key_value=key_value,
                source_user_id=source_key_user_id,
                source_group_id=source_group_id,
                target_user_id=None,
                target_email=None,
                target_group_id=None,
                status=RotationResultStatus.skipped,
                reason="API key is not owned by the selected source user",
            )
        if not key_value:
            return KeyTransferItem(
                key_id=key_id,
                key_name=key_name,
                key_value=key_value,
                source_user_id=source_key_user_id,
                source_group_id=source_group_id,
                target_user_id=None,
                target_email=None,
                target_group_id=None,
                status=RotationResultStatus.skipped,
                reason="API key value is missing; cannot verify preservation",
            )

        parsed_key_name = self._parse_transfer_key_name(key_name)
        if parsed_key_name is None:
            return KeyTransferItem(
                key_id=key_id,
                key_name=key_name,
                key_value=key_value,
                source_user_id=source_key_user_id,
                source_group_id=source_group_id,
                target_user_id=None,
                target_email=None,
                target_group_id=None,
                status=RotationResultStatus.skipped,
                reason=f"API key name does not match the {KEY_NAME_PATTERN} pattern",
            )
        target_email = parsed_key_name.email

        target_email_key = self._normalize_email(target_email)
        if target_email_key in duplicate_emails:
            return KeyTransferItem(
                key_id=key_id,
                key_name=key_name,
                key_value=key_value,
                source_user_id=source_key_user_id,
                source_group_id=source_group_id,
                target_user_id=None,
                target_email=target_email,
                target_group_id=None,
                status=RotationResultStatus.skipped,
                reason="USER_EMAIL_NOT_UNIQUE",
            )

        target_user = users_by_email.get(target_email_key)
        if target_user is None:
            return KeyTransferItem(
                key_id=key_id,
                key_name=key_name,
                key_value=key_value,
                source_user_id=source_key_user_id,
                source_group_id=source_group_id,
                target_user_id=None,
                target_email=target_email,
                target_group_id=None,
                status=RotationResultStatus.skipped,
                reason="USER_NOT_FOUND",
            )

        target_user_id = target_user.get("id")
        if target_user_id in (None, ""):
            return KeyTransferItem(
                key_id=key_id,
                key_name=key_name,
                key_value=key_value,
                source_user_id=source_key_user_id,
                source_group_id=source_group_id,
                target_user_id=None,
                target_email=target_email,
                target_group_id=None,
                status=RotationResultStatus.skipped,
                reason="TARGET_USER_ID_NOT_FOUND",
            )

        target_group_id = self._first_available_user_group_id(
            target_user,
            available_groups_by_key,
        )
        if target_group_id in (None, ""):
            return KeyTransferItem(
                key_id=key_id,
                key_name=key_name,
                key_value=key_value,
                source_user_id=source_key_user_id,
                source_group_id=source_group_id,
                target_user_id=target_user_id,
                target_email=target_email,
                target_group_id=None,
                status=RotationResultStatus.skipped,
                reason="TARGET_USER_GROUP_NOT_FOUND",
            )

        if self._normalize_key(source_key_user_id) == self._normalize_key(target_user_id):
            return KeyTransferItem(
                key_id=key_id,
                key_name=key_name,
                key_value=key_value,
                source_user_id=source_key_user_id,
                source_group_id=source_group_id,
                target_user_id=target_user_id,
                target_email=target_email,
                target_group_id=target_group_id,
                status=RotationResultStatus.skipped,
                reason="API key already belongs to target user",
            )

        return KeyTransferItem(
            key_id=key_id,
            key_name=key_name,
            key_value=key_value,
            source_user_id=source_key_user_id,
            source_group_id=source_group_id,
            target_user_id=target_user_id,
            target_email=target_email,
            target_group_id=target_group_id,
            status=RotationResultStatus.planned,
            reason="Ready to transfer API key to target email user",
            quota=0.0,
        )

    def _normalize_selected_key_ids(self, key_ids: list[Any] | None) -> set[str] | None:
        if key_ids is None:
            return None
        selected = {self._normalize_key(key_id) for key_id in key_ids if key_id not in (None, "")}
        if not selected:
            raise RotationExecutionError("At least one API key must be selected")
        return selected

    def _execute_key_transfer(
        self,
        item: KeyTransferItem,
    ) -> KeyTransferItem:
        try:
            response = self.sub2api_client.migrate_api_key_owner(
                key_id=item.key_id,
                user_id=item.target_user_id,
                group_id=item.target_group_id,
                quota=0.0,
                reset_quota=True,
            )
        except Sub2APIError as exc:
            logger.exception("API key transfer failed for key_id=%s", item.key_id)
            item.status = RotationResultStatus.failed
            item.reason = f"Upstream api-key owner migration failed: {exc}"
            return item

        api_key = response.get("api_key")
        if not isinstance(api_key, dict):
            api_key = {}
        if not api_key:
            item.status = RotationResultStatus.failed
            item.reason = "Upstream response did not include API key confirmation"
            return item
        returned_key_value = self._text_or_none(api_key.get("key"))
        if not returned_key_value or returned_key_value != item.key_value:
            item.status = RotationResultStatus.failed
            item.reason = "Upstream response changed API key value"
            return item
        returned_user_id = api_key.get("user_id")
        returned_group_id = api_key.get("group_id")
        returned_quota = self._optional_float(api_key.get("quota"))
        mismatches: list[str] = []
        if returned_user_id in (None, "") or self._normalize_key(returned_user_id) != self._normalize_key(item.target_user_id):
            mismatches.append("user_id")
        if returned_group_id in (None, "") or self._normalize_key(returned_group_id) != self._normalize_key(item.target_group_id):
            mismatches.append("group_id")
        if returned_quota != 0.0:
            mismatches.append("quota")
        if mismatches:
            item.status = RotationResultStatus.failed
            item.reason = "Upstream response did not confirm " + ", ".join(mismatches)
            return item
        item.status = RotationResultStatus.moved
        item.reason = "API key transferred to target email user"
        item.quota = 0.0
        return item

    def _save_key_transfer_run(
        self,
        *,
        items: list[KeyTransferItem],
        dry_run: bool,
        reason: str | None,
        scope: str,
    ) -> OrchestrationRunRecord:
        planned: list[dict[str, Any]] = []
        moved: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for item in items:
            serialized = self._serialize_key_transfer_item(item)
            if item.status == RotationResultStatus.moved:
                moved.append(serialized)
            elif item.status == RotationResultStatus.planned:
                planned.append(serialized)
            elif item.status == RotationResultStatus.failed:
                failed.append(serialized)
            else:
                skipped.append(serialized)
        return self._save_orchestration_run(
            run_kind=OrchestrationRunKind.manual,
            tag="key_transfer_preview" if dry_run else "key_transfer",
            trigger_type=RotationTrigger.manual,
            dry_run=dry_run,
            window=None,
            synced={},
            config={
                "key_name_pattern": KEY_NAME_PATTERN,
                "reason": reason or "",
                "scope": scope,
            },
            planned=planned,
            moved=moved,
            skipped=skipped,
            failed=failed,
        )

    def _serialize_key_transfer_item(self, item: KeyTransferItem) -> dict[str, Any]:
        return {
            "user_id": item.target_user_id or item.source_user_id or "",
            "email": item.target_email or "unknown@example.com",
            "source_group_id": item.source_group_id,
            "target_group_id": item.target_group_id,
            "trigger_type": RotationTrigger.manual.value,
            "status": item.status.value,
            "reason": item.reason,
            "migrated_keys": 1 if item.status in {RotationResultStatus.moved, RotationResultStatus.planned} else 0,
            "usage_window": None,
            "usage_value": None,
            "usage_snapshot": None,
            "metadata": {
                "key_id": str(item.key_id or ""),
                "key_name": item.key_name or "",
                "source_user_id": str(item.source_user_id or ""),
                "target_user_id": str(item.target_user_id or ""),
                "target_email": item.target_email or "",
                "quota": str(item.quota if item.quota is not None else ""),
            },
        }

    def _parse_transfer_key_name(self, key_name: str | None) -> ParsedKeyName | None:
        return parse_key_name(key_name)

    def parse_transfer_key_name(self, key_name: str | None) -> ParsedKeyName | None:
        return self._parse_transfer_key_name(key_name)

    def extract_transfer_email(self, key_name: str | None) -> str | None:
        parsed = self._parse_transfer_key_name(key_name)
        return parsed.email if parsed else None

    def _first_available_user_group_id(
        self,
        user: dict[str, Any],
        available_groups_by_key: dict[str, dict[str, Any]],
    ) -> Any | None:
        for group_id in self._candidate_user_group_ids(user):
            if self._normalize_key(group_id) in available_groups_by_key:
                return group_id
        return None

    def _candidate_user_group_ids(self, user: dict[str, Any]) -> list[Any]:
        candidate_group_ids: list[Any] = []

        def add(group_id: Any) -> None:
            if group_id in (None, ""):
                return
            if not any(self._normalize_key(existing) == self._normalize_key(group_id) for existing in candidate_group_ids):
                candidate_group_ids.append(group_id)

        group_ids = user.get("group_ids")
        if isinstance(group_ids, list):
            for group_id in group_ids:
                add(group_id)
        group_id = user.get("current_group_id") or user.get("group_id")
        add(group_id)
        raw = user.get("raw")
        if isinstance(raw, dict):
            for field_name in ("group_ids", "allowed_groups", "groups"):
                raw_groups = raw.get(field_name)
                if not isinstance(raw_groups, list):
                    continue
                for raw_group in raw_groups:
                    if isinstance(raw_group, dict):
                        group_id = raw_group.get("id") or raw_group.get("group_id")
                    else:
                        group_id = raw_group
                    add(group_id)
        return candidate_group_ids

    def candidate_group_ids_for_user(self, user: dict[str, Any]) -> list[Any]:
        return self._candidate_user_group_ids(user)

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

    def _latest_accounts_snapshot(self) -> list[dict[str, Any]]:
        payload = self._latest_operational_payload(SOURCE_ACCOUNTS, default=[])
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

    def normalize_key_value(self, value: Any) -> str:
        return self._normalize_key(value)

    def _normalize_email(self, value: Any) -> str:
        return str(value or "").strip().lower()

    def normalize_email_value(self, value: Any) -> str:
        return self._normalize_email(value)

    def _text_or_none(self, value: Any) -> str | None:
        if value in (None, ""):
            return None
        return str(value)

    def _optional_float(self, value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None

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
