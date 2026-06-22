from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any

from app.clients.sub2api import Sub2APIClient
from app.config import API_KEY_GROUP_SELECTION_FIRST, API_KEY_GROUP_SELECTION_RANDOM
from app.errors import RotationExecutionError
from app.services.rotation import KEY_NAME_PATTERN as API_KEY_NAME_PATTERN
from app.services.rotation import ParsedKeyName, RotationService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApiKeyCreateResult:
    api_key: dict[str, Any]
    user_id: Any
    user_email: str | None
    group_id: Any
    parsed_name: ParsedKeyName | None
    target_email: str | None
    fallback_to_admin: bool
    fallback_reason: str | None


@dataclass(frozen=True)
class ApiKeyCreateTargetError:
    status: str
    target_email: str | None
    reason: str


class ApiKeyAutomationService:
    """Token-facing API key operations backed by Sub2API admin APIs."""

    KEY_NAME_PATTERN = API_KEY_NAME_PATTERN

    def __init__(
        self,
        *,
        sub2api_client: Sub2APIClient,
        rotation_service: RotationService,
        group_selection: str = API_KEY_GROUP_SELECTION_FIRST,
    ) -> None:
        self.sub2api_client = sub2api_client
        self.rotation_service = rotation_service
        self.group_selection = group_selection

    def create_named_key(
        self,
        *,
        name: str,
        target: str | None = None,
        key_options: dict[str, Any] | None = None,
    ) -> ApiKeyCreateResult | ApiKeyCreateTargetError:
        parsed_name = self.rotation_service.parse_transfer_key_name(name)
        if parsed_name is None:
            return ApiKeyCreateTargetError(
                status="INVALID_KEY_NAME_FORMAT",
                target_email=None,
                reason=f"API key name must match the {self.KEY_NAME_PATTERN} pattern",
            )
        self.rotation_service.refresh_operational_data_before_mutation()
        target_user, target_email, fallback_reason, target_error = self._resolve_create_target(
            parsed_name=parsed_name,
            target=target,
        )
        if target_error is not None:
            return target_error

        target_group_id = self._select_group_id_for_user(target_user)
        if target_group_id in (None, ""):
            raise RotationExecutionError("Target user has no available group")

        target_user_id = target_user.get("id")
        if target_user_id in (None, ""):
            raise RotationExecutionError("Target user id is missing")

        api_key = self.sub2api_client.create_user_api_key(
            user_id=target_user_id,
            name=name,
            group_id=target_group_id,
            options=key_options or {},
        )
        return ApiKeyCreateResult(
            api_key=api_key,
            user_id=target_user_id,
            user_email=self._text_or_none(target_user.get("email")),
            group_id=target_group_id,
            parsed_name=parsed_name,
            target_email=target_email,
            fallback_to_admin=fallback_reason is not None,
            fallback_reason=fallback_reason,
        )

    def list_encoded_keys(self, *, email: str | None = None) -> list[dict[str, Any]]:
        normalized_filter = self.rotation_service.normalize_email_value(email) if email else None
        result = self.sub2api_client.list_all_user_api_keys()
        items: list[dict[str, Any]] = []
        for key_item in result["items"]:
            if not isinstance(key_item, dict):
                continue
            key_name = self._text_or_none(key_item.get("name"))
            parsed_name = self.rotation_service.parse_transfer_key_name(key_name)
            if parsed_name is None:
                continue
            target_email = parsed_name.email
            if normalized_filter and self.rotation_service.normalize_email_value(target_email) != normalized_filter:
                continue
            items.append(
                {
                    "key_id": key_item.get("id") or key_item.get("key_id"),
                    "name": key_name,
                    "key_service": parsed_name.service,
                    "key_environment": parsed_name.environment,
                    "key_object": parsed_name.object,
                    "key_version": parsed_name.version,
                    "target_email": target_email,
                    "user_id": key_item.get("user_id") or key_item.get("owner_user_id"),
                    "user_email": key_item.get("owner_email"),
                    "group_id": key_item.get("group_id") or key_item.get("current_group_id"),
                    "group_name": key_item.get("group_name") or key_item.get("current_group_name"),
                    "status": key_item.get("status"),
                    "quota": key_item.get("quota"),
                    "quota_used": key_item.get("quota_used"),
                    "usage_5h": key_item.get("usage_5h"),
                    "usage_1d": key_item.get("usage_1d"),
                    "usage_7d": key_item.get("usage_7d"),
                }
            )
        return items

    def _select_group_id_for_user(self, user: dict[str, Any]) -> Any | None:
        available_groups_by_key = self.rotation_service.available_groups_by_key()
        candidate_group_ids = self.rotation_service.candidate_group_ids_for_user(user)
        selectable_group_ids = [
            group_id
            for group_id in candidate_group_ids
            if self.rotation_service.normalize_key_value(group_id) in available_groups_by_key
        ]
        if not selectable_group_ids:
            return None
        if self.group_selection == API_KEY_GROUP_SELECTION_RANDOM:
            return random.choice(selectable_group_ids)
        return selectable_group_ids[0]

    def _resolve_create_target(
        self,
        *,
        parsed_name: ParsedKeyName,
        target: str | None = None,
    ) -> tuple[dict[str, Any], str | None, str | None, ApiKeyCreateTargetError | None]:
        explicit_target = (self._text_or_none(target) or "").strip()
        if explicit_target:
            target_email = explicit_target
            users_by_email, duplicate_emails = self.rotation_service.users_by_exact_emails(
                {target_email}
            )
            email_key = self.rotation_service.normalize_email_value(target_email)
            if email_key in duplicate_emails:
                return (
                    {},
                    target_email,
                    None,
                    ApiKeyCreateTargetError(
                        status="USER_EMAIL_NOT_UNIQUE",
                        target_email=target_email,
                        reason="Target user email is not unique",
                    ),
                )
            target_user = users_by_email.get(email_key)
            if target_user is None:
                return (
                    {},
                    target_email,
                    None,
                    ApiKeyCreateTargetError(
                        status="USER_NOT_FOUND",
                        target_email=target_email,
                        reason="Target user was not found",
                    ),
                )
            return target_user, target_email, None, None

        target_email = parsed_name.email
        users_by_email, duplicate_emails = self.rotation_service.users_by_exact_emails(
            {target_email}
        )
        email_key = self.rotation_service.normalize_email_value(target_email)
        if email_key in duplicate_emails:
            return self._admin_user(), target_email, "USER_EMAIL_NOT_UNIQUE", None
        target_user = users_by_email.get(email_key)
        if target_user is not None:
            return target_user, target_email, None, None
        return self._admin_user(), target_email, "USER_NOT_FOUND", None

    def _admin_user(self) -> dict[str, Any]:
        admin_user_id = self.rotation_service.resolve_admin_user_id()
        users = self.sub2api_client.list_users(page_size=1000)
        admin_key = self.rotation_service.normalize_key_value(admin_user_id)
        for user in users:
            if self.rotation_service.normalize_key_value(user.get("id")) == admin_key:
                return user
        raise RotationExecutionError("Admin source user was not found")

    def _text_or_none(self, value: Any) -> str | None:
        if value in (None, ""):
            return None
        return str(value)
