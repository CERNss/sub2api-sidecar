from __future__ import annotations

import logging
from datetime import date
from typing import Any, Iterable
from urllib.parse import urljoin

import requests

from app.config import Sub2APIProvisioningDefaults, TemporaryUnschedulableRule

logger = logging.getLogger(__name__)


class Sub2APIError(Exception):
    """Raised when Sub2API admin API interactions fail."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class Sub2APIClient:
    """
    Thin admin API wrapper.

    IMPORTANT:
    The exact Sub2API admin endpoints and some OAuth payload field names may differ between deployments.
    Keep any future adjustments inside this client so controllers/services remain stable.
    """

    CREATE_GROUP_PATHS = ("/api/v1/admin/groups", "/api/admin/groups", "/admin/groups")
    CREATE_USER_PATHS = ("/api/v1/admin/users", "/api/admin/users", "/admin/users")
    SET_USER_GROUP_PATHS = (
        "/api/v1/admin/users/{user_id}",
        "/api/admin/users/{user_id}/groups",
        "/admin/users/{user_id}/groups",
    )
    UPDATE_API_KEY_GROUP_PATHS = ("/api/v1/admin/api-keys/{key_id}",)
    REPLACE_EXCLUSIVE_GROUP_PATHS = (
        "/api/v1/admin/users/{user_id}/replace-group",
        "/api/admin/users/{user_id}/replace-group",
        "/admin/users/{user_id}/replace-group",
    )
    LIST_GROUPS_PATHS = (
        "/api/v1/admin/groups/all",
        "/api/v1/admin/groups",
        "/api/admin/groups/all",
        "/api/admin/groups",
        "/admin/groups/all",
        "/admin/groups",
    )
    LIST_USERS_PATHS = (
        "/api/v1/admin/users",
        "/api/v1/admin/users/all",
        "/api/admin/users",
        "/api/admin/users/all",
        "/admin/users",
        "/admin/users/all",
    )
    USER_API_KEYS_PATHS = ("/api/v1/admin/users/{user_id}/api-keys",)
    USAGE_STATS_PATHS = ("/api/v1/admin/usage/stats",)
    GENERATE_OPENAI_AUTH_URL_PATHS = (
        "/api/v1/admin/openai/oauth/url",
        "/api/admin/openai/oauth/url",
        "/admin/openai/oauth/url",
    )
    EXCHANGE_OPENAI_CODE_PATHS = (
        "/api/v1/admin/openai/oauth/exchange",
        "/api/admin/openai/oauth/exchange",
        "/admin/openai/oauth/exchange",
    )
    CREATE_OPENAI_ACCOUNT_PATHS = (
        "/api/v1/admin/openai/accounts",
        "/api/admin/openai/accounts",
        "/admin/openai/accounts",
    )
    BIND_ACCOUNT_TO_GROUP_PATHS = (
        "/api/v1/admin/groups/{group_id}/accounts",
        "/api/admin/groups/{group_id}/accounts",
        "/admin/groups/{group_id}/accounts",
    )

    def __init__(
        self,
        base_url: str,
        admin_api_key: str,
        provisioning_defaults: Sub2APIProvisioningDefaults,
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.provisioning_defaults = provisioning_defaults
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "x-api-key": admin_api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def create_group(self, name: str) -> dict[str, Any]:
        payload = self._build_group_payload(name)
        data = self._request_candidates("POST", self.CREATE_GROUP_PATHS, json=payload)
        body = self._unwrap_data(data)
        group_id = self._extract_id(
            body,
            "id",
            "group_id",
            "data.id",
            "data.group_id",
            "group.id",
            "data.group.id",
        )
        return {"id": group_id, "name": name, "raw": data}

    def create_user(self, email: str, password: str) -> dict[str, Any]:
        payload = {
            "email": email,
            "name": email,
            "username": email,
            "password": password,
        }
        data = self._request_candidates("POST", self.CREATE_USER_PATHS, json=payload)
        body = self._unwrap_data(data)
        user_id = self._extract_id(
            body,
            "id",
            "user_id",
            "data.id",
            "data.user_id",
            "user.id",
            "data.user.id",
        )
        return {"id": user_id, "email": email, "raw": data}

    def set_user_group(self, user_id: Any, group_id: Any) -> dict[str, Any]:
        """Set a user's allowed groups for initial provisioning before API keys exist."""
        payload = {
            "allowed_groups": [group_id],
            "group_ids": [group_id],
            "group_id": group_id,
        }
        path_candidates = tuple(path.format(user_id=user_id) for path in self.SET_USER_GROUP_PATHS)
        data = self._request_candidates("PUT", path_candidates, json=payload)
        return {"user_id": user_id, "group_id": group_id, "raw": data}

    def update_api_key_group(self, *, key_id: Any, group_id: Any) -> dict[str, Any]:
        payload = {"group_id": group_id}
        path_candidates = tuple(
            path.format(key_id=key_id) for path in self.UPDATE_API_KEY_GROUP_PATHS
        )
        data = self._request_candidates("PUT", path_candidates, json=payload)
        return {"key_id": key_id, "group_id": group_id, "raw": data}

    def replace_user_group(
        self,
        *,
        user_id: Any,
        old_group_id: Any,
        new_group_id: Any,
    ) -> dict[str, Any]:
        return self.replace_exclusive_user_group(
            user_id=user_id,
            old_group_id=old_group_id,
            new_group_id=new_group_id,
        )

    def replace_exclusive_user_group(
        self,
        *,
        user_id: Any,
        old_group_id: Any,
        new_group_id: Any,
    ) -> dict[str, Any]:
        payload = {"old_group_id": old_group_id, "new_group_id": new_group_id}
        path_candidates = tuple(
            path.format(user_id=user_id) for path in self.REPLACE_EXCLUSIVE_GROUP_PATHS
        )
        data = self._request_candidates("POST", path_candidates, json=payload)
        body = self._unwrap_data(data)
        migrated_keys = self._extract_value(
            body,
            "migrated_keys",
            "data.migrated_keys",
            "result.migrated_keys",
        )
        return {
            "user_id": user_id,
            "old_group_id": old_group_id,
            "new_group_id": new_group_id,
            "migrated_keys": migrated_keys or 0,
            "raw": data,
        }

    def list_groups(self, platform: str | None = None) -> list[dict[str, Any]]:
        last_error: Sub2APIError | None = None
        params = {"platform": platform} if platform else None
        for path in self.LIST_GROUPS_PATHS:
            try:
                data = self._request("GET", path, params=params)
                return self._parse_group_list(data)
            except Sub2APIError as exc:
                last_error = exc
                if exc.status_code == 404:
                    logger.warning("Sub2API path not found, trying next candidate: %s", path)
                    continue
                raise
        raise last_error or Sub2APIError("No candidate Sub2API path succeeded")

    def list_users(self, email: str | None = None) -> list[dict[str, Any]]:
        last_error: Sub2APIError | None = None
        params: dict[str, Any] = {"page": 1, "page_size": 1000}
        if email:
            params["email"] = email
        for path in self.LIST_USERS_PATHS:
            try:
                data = self._request("GET", path, params=params)
                users = self._parse_user_list(data)
                if email:
                    needle = email.lower()
                    users = [
                        user
                        for user in users
                        if needle in str(user.get("email") or "").lower()
                        or needle in str(user.get("username") or "").lower()
                        or needle in str(user.get("display_name") or "").lower()
                        or needle in str(user.get("name") or "").lower()
                    ]
                return users
            except Sub2APIError as exc:
                last_error = exc
                if exc.status_code == 404:
                    logger.warning("Sub2API path not found, trying next candidate: %s", path)
                    continue
                raise
        raise last_error or Sub2APIError("No candidate Sub2API path succeeded")

    def get_user_api_keys(self, user_id: Any, page_size: int = 1000) -> dict[str, Any]:
        path_candidates = tuple(path.format(user_id=user_id) for path in self.USER_API_KEYS_PATHS)
        data = self._request_candidates(
            "GET",
            path_candidates,
            params={"page": 1, "page_size": page_size},
        )
        envelope = self._unwrap_data(data)
        items: list[dict[str, Any]] = []
        total = 0
        if isinstance(envelope, dict):
            raw_items = envelope.get("items", [])
            if isinstance(raw_items, list):
                items = [item for item in raw_items if isinstance(item, dict)]
            total = int(envelope.get("total", len(items)) or len(items))
        return {"items": items, "total": total, "raw": data}

    def get_usage_stats(
        self,
        *,
        user_id: Any,
        start_date: date,
        end_date: date,
        timezone_name: str,
    ) -> dict[str, Any]:
        params = {
            "user_id": user_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "timezone": timezone_name,
        }
        data = self._request_candidates("GET", self.USAGE_STATS_PATHS, params=params)
        body = self._unwrap_data(data)
        if not isinstance(body, dict):
            raise Sub2APIError("Sub2API usage stats response is not an object")
        return body

    def generate_openai_auth_url(self, email: str, state: str, redirect_uri: str) -> dict[str, Any]:
        payload = {
            "provider": "openai",
            "name": email,
            "email": email,
            "state": state,
            "redirect_uri": redirect_uri,
        }
        data = self._request_candidates(
            "POST", self.GENERATE_OPENAI_AUTH_URL_PATHS, json=payload
        )
        body = self._unwrap_data(data)
        auth_url = self._extract_value(
            body,
            "auth_url",
            "oauth_url",
            "url",
            "data.auth_url",
            "data.oauth_url",
            "data.url",
            "result.auth_url",
            "result.url",
        )
        if not auth_url:
            raise Sub2APIError(
                "Sub2API did not return an OAuth URL. Adjust `generate_openai_auth_url()` parsing."
            )
        return {"url": auth_url, "raw": data}

    def exchange_openai_code(self, code: str, state: str, redirect_uri: str) -> dict[str, Any]:
        payload = {
            "code": code,
            "state": state,
            "redirect_uri": redirect_uri,
            "provider": "openai",
        }
        data = self._request_candidates(
            "POST", self.EXCHANGE_OPENAI_CODE_PATHS, json=payload
        )
        body = self._unwrap_data(data)
        return {"exchange": body if isinstance(body, dict) else data, "raw": data}

    def create_openai_account_from_oauth(
        self,
        name: str,
        oauth_payload: dict[str, Any],
        group_id: Any,
    ) -> dict[str, Any]:
        payload = self._build_openai_oauth_account_payload(
            name=name,
            oauth_payload=oauth_payload,
            group_id=group_id,
        )
        data = self._request_candidates(
            "POST", self.CREATE_OPENAI_ACCOUNT_PATHS, json=payload
        )
        body = self._unwrap_data(data)
        account_id = self._extract_id(
            body,
            "id",
            "account_id",
            "data.id",
            "data.account_id",
            "account.id",
            "data.account.id",
        )
        return {"id": account_id, "name": name, "raw": data}

    def bind_account_to_group(self, account_id: Any, group_id: Any) -> dict[str, Any]:
        payload = {"account_id": account_id, "account_ids": [account_id]}
        path_candidates = tuple(
            path.format(group_id=group_id) for path in self.BIND_ACCOUNT_TO_GROUP_PATHS
        )
        data = self._request_candidates("POST", path_candidates, json=payload)
        return {"account_id": account_id, "group_id": group_id, "raw": data}

    def _build_group_payload(self, name: str) -> dict[str, Any]:
        return {
            "name": name,
            "platform": self.provisioning_defaults.group_platform,
            "is_exclusive": True,
        }

    def _build_openai_oauth_account_payload(
        self,
        *,
        name: str,
        oauth_payload: dict[str, Any],
        group_id: Any,
    ) -> dict[str, Any]:
        return {
            "provider": self.provisioning_defaults.account_provider,
            "platform": self.provisioning_defaults.account_platform,
            "type": self.provisioning_defaults.account_type,
            "name": name,
            "email": name,
            "oauth": oauth_payload,
            "wsmode": self.provisioning_defaults.account_ws_mode,
            "temporary_unschedulable": (
                self.provisioning_defaults.account_temporary_unschedulable
            ),
            "temporary_unschedulable_rules": [
                self._serialize_rule(rule)
                for rule in self.provisioning_defaults.account_temporary_unschedulable_rules
            ],
            "group_id": group_id,
            "group_ids": [group_id],
        }

    def _serialize_rule(self, rule: TemporaryUnschedulableRule) -> dict[str, Any]:
        return {
            "error_code": rule.error_code,
            "duration_minutes": rule.duration_minutes,
            "keywords": list(rule.keywords),
            "description": rule.description,
        }

    def _parse_group_list(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        body = self._unwrap_data(payload)
        if isinstance(body, list):
            raw_groups = body
        elif isinstance(body, dict) and isinstance(body.get("items"), list):
            raw_groups = body["items"]
        else:
            raise Sub2APIError("Unable to parse group list from Sub2API response")

        groups: list[dict[str, Any]] = []
        for item in raw_groups:
            if not isinstance(item, dict):
                continue
            group_kind = self._extract_group_kind(item)
            is_subscription = self._is_subscription_group(item, group_kind)
            is_exclusive = self._coerce_bool(item.get("is_exclusive"))
            groups.append(
                {
                    "id": self._extract_id(item, "id", "group_id"),
                    "name": str(item.get("name") or item.get("group_name") or ""),
                    "group_kind": group_kind,
                    "platform": item.get("platform"),
                    "status": item.get("status"),
                    "is_exclusive": is_exclusive,
                    "is_subscription": is_subscription,
                    "rotation_supported": is_exclusive and not is_subscription,
                    "raw": item,
                }
            )
        return groups

    def _parse_user_list(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        body = self._unwrap_data(payload)
        if isinstance(body, list):
            raw_users = body
        elif isinstance(body, dict) and isinstance(body.get("items"), list):
            raw_users = body["items"]
        elif isinstance(body, dict) and isinstance(body.get("users"), list):
            raw_users = body["users"]
        else:
            raise Sub2APIError("Unable to parse user list from Sub2API response")

        users: list[dict[str, Any]] = []
        for item in raw_users:
            if not isinstance(item, dict):
                continue
            current_group_id, current_group_name = self._extract_user_current_group(item)
            username = str(item.get("username")) if item.get("username") is not None else None
            email = str(item.get("email") or username or item.get("name") or "")
            display_name_value = (
                item.get("display_name")
                or item.get("nickname")
                or item.get("full_name")
                or item.get("name")
                or username
                or email.split("@", 1)[0]
            )
            display_name = str(display_name_value)
            if display_name.lower() == email.lower():
                display_name = email.split("@", 1)[0]
            users.append(
                {
                    "id": self._extract_id(item, "id", "user_id"),
                    "email": email,
                    "username": username,
                    "name": item.get("name") or username or email,
                    "display_name": display_name,
                    "status": item.get("status"),
                    "current_group_id": current_group_id,
                    "current_group_name": current_group_name,
                    "raw": item,
                }
            )
        return users

    def _extract_user_current_group(self, item: dict[str, Any]) -> tuple[Any | None, str | None]:
        for field_name in ("group_id", "current_group_id", "default_group_id"):
            value = item.get(field_name)
            if value not in (None, ""):
                return value, self._extract_user_group_name(item)

        group = item.get("group") or item.get("current_group")
        if isinstance(group, dict):
            group_id = group.get("id") or group.get("group_id")
            if group_id not in (None, ""):
                return group_id, group.get("name") or group.get("group_name")

        for field_name in ("groups", "allowed_groups", "group_ids"):
            raw_groups = item.get(field_name)
            if not isinstance(raw_groups, list) or len(raw_groups) != 1:
                continue
            only_group = raw_groups[0]
            if isinstance(only_group, dict):
                group_id = only_group.get("id") or only_group.get("group_id")
                group_name = only_group.get("name") or only_group.get("group_name")
                if group_id not in (None, ""):
                    return group_id, group_name
            elif only_group not in (None, ""):
                return only_group, self._extract_user_group_name(item)

        return None, self._extract_user_group_name(item)

    def _extract_user_group_name(self, item: dict[str, Any]) -> str | None:
        for field_name in ("group_name", "current_group_name", "default_group_name"):
            value = item.get(field_name)
            if value not in (None, ""):
                return str(value)
        return None

    def _extract_group_kind(self, item: dict[str, Any]) -> str | None:
        for field_name in ("group_kind", "group_type", "type", "kind", "mode"):
            value = item.get(field_name)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    def _is_subscription_group(self, item: dict[str, Any], group_kind: str | None) -> bool:
        if group_kind and "subscription" in group_kind.lower():
            return True

        for field_name in (
            "is_subscription",
            "is_subscription_group",
            "subscription_group",
        ):
            value = item.get(field_name)
            if self._coerce_bool(value):
                return True

        for field_name in ("subscription_id", "subscription_name", "subscription"):
            value = item.get(field_name)
            if value in (None, "", False):
                continue
            return True

        return False

    def _coerce_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        if value is None:
            return False
        return bool(value)

    def _request_candidates(
        self,
        method: str,
        paths: Iterable[str],
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_error: Sub2APIError | None = None
        for path in paths:
            try:
                return self._request(method, path, json=json, params=params)
            except Sub2APIError as exc:
                last_error = exc
                if exc.status_code == 404:
                    logger.warning("Sub2API path not found, trying next candidate: %s", path)
                    continue
                raise

        raise last_error or Sub2APIError("No candidate Sub2API path succeeded")

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        logger.info("Calling Sub2API admin API: %s %s", method, url)
        try:
            response = self.session.request(
                method=method,
                url=url,
                json=json,
                params=params,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.exception(
                "Failed to reach Sub2API admin API | method=%s | url=%s",
                method,
                url,
            )
            raise Sub2APIError(f"Failed to reach Sub2API: {exc}") from exc

        if not response.ok:
            logger.error(
                "Sub2API request failed | status=%s | body=%s",
                response.status_code,
                response.text[:1000],
            )
            raise Sub2APIError(
                f"Sub2API request failed with status {response.status_code}: {response.text}",
                status_code=response.status_code,
            )

        if not response.content:
            return {}

        try:
            data = response.json()
        except ValueError:
            return {"raw_text": response.text}

        logger.info("Sub2API request succeeded: %s %s", method, url)
        return data

    def _unwrap_data(self, payload: Any) -> Any:
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    def _extract_id(self, payload: dict[str, Any], *paths: str) -> Any:
        value = self._extract_value(payload, *paths)
        if value is None:
            raise Sub2APIError(
                "Unable to extract entity id from Sub2API response. Adjust response parsing in Sub2APIClient."
            )
        return value

    def _extract_value(self, payload: Any, *paths: str) -> Any:
        for path in paths:
            current = payload
            found = True
            for part in path.split("."):
                if isinstance(current, dict) and part in current:
                    current = current[part]
                    continue
                found = False
                break
            if not found:
                continue
            if current is None:
                continue
            if isinstance(current, (str, list, dict)) and len(current) == 0:
                continue
            return current
        return None
