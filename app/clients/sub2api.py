from __future__ import annotations

import logging
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

    CREATE_GROUP_PATHS = ("/api/admin/groups", "/admin/groups")
    CREATE_USER_PATHS = ("/api/admin/users", "/admin/users")
    REPLACE_USER_GROUP_PATHS = (
        "/api/admin/users/{user_id}/groups",
        "/admin/users/{user_id}/groups",
    )
    GENERATE_OPENAI_AUTH_URL_PATHS = (
        "/api/admin/openai/oauth/url",
        "/admin/openai/oauth/url",
    )
    EXCHANGE_OPENAI_CODE_PATHS = (
        "/api/admin/openai/oauth/exchange",
        "/admin/openai/oauth/exchange",
    )
    CREATE_OPENAI_ACCOUNT_PATHS = (
        "/api/admin/openai/accounts",
        "/admin/openai/accounts",
    )
    BIND_ACCOUNT_TO_GROUP_PATHS = (
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
        group_id = self._extract_id(
            data,
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
        user_id = self._extract_id(
            data,
            "id",
            "user_id",
            "data.id",
            "data.user_id",
            "user.id",
            "data.user.id",
        )
        return {"id": user_id, "email": email, "raw": data}

    def replace_user_group(self, user_id: Any, group_id: Any) -> dict[str, Any]:
        payload = {"group_ids": [group_id], "group_id": group_id}
        path_candidates = tuple(
            path.format(user_id=user_id) for path in self.REPLACE_USER_GROUP_PATHS
        )
        data = self._request_candidates("PUT", path_candidates, json=payload)
        return {"user_id": user_id, "group_id": group_id, "raw": data}

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
        auth_url = self._extract_value(
            data,
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
        return {"exchange": data, "raw": data}

    def create_openai_account_from_oauth(
        self,
        name: str,
        oauth_payload: dict[str, Any],
        group_id: Any,
    ) -> dict[str, Any]:
        # IMPORTANT:
        # The account name is intentionally forced to the original flow email.
        # Do not switch this to any email returned by OAuth.
        payload = self._build_openai_oauth_account_payload(
            name=name,
            oauth_payload=oauth_payload,
            group_id=group_id,
        )
        data = self._request_candidates(
            "POST", self.CREATE_OPENAI_ACCOUNT_PATHS, json=payload
        )
        account_id = self._extract_id(
            data,
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
        }

    def _build_openai_oauth_account_payload(
        self,
        *,
        name: str,
        oauth_payload: dict[str, Any],
        group_id: Any,
    ) -> dict[str, Any]:
        # IMPORTANT:
        # These request keys are centralized here because Sub2API admin payload
        # names can vary across deployments. If your deployment expects different
        # names, adjust this builder instead of changing service/controller logic.
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
            if found and current not in (None, "", [], {}):
                return current
        return None
