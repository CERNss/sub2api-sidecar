from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
import requests
from fastapi.testclient import TestClient

import app.main as main
from conftest import started_test_client
from app.auth import ACCESS_KEY_COOKIE_NAME
from app.clients.sub2api import Sub2APIClient, Sub2APIError
from app.config import Sub2APIProvisioningDefaults, get_settings
from app.models.flow import AssignmentMode
from app.models.operational_data import (
    CreditControlRuntimeSettings,
    OperationalDataRuntimeSettings,
    OperationalDataSnapshot,
    ProvisioningRuntimeSettings,
)
from app.models.rotation import (
    AutoRotationRuntimeConfig,
    AutoRotationUsageWindow,
    OrchestrationRunKind,
    OrchestrationRunRecord,
    RotationPoolGroup,
    RotationPoolKind,
    UserGroupAssignment,
)
from app.services.credit_scheduler import CreditControlScheduler
from app.services.group_usage import GroupUsageService
from app.services.operational_data import (
    OperationalDataCollectionResult,
    OperationalDataRefresher,
)
from app.services.rotation_scheduler import AutoRotationScheduler
from app.services.usage_segmentation import UsageSegmentationService

AUTH_PAYLOAD = {"username": "admin", "password": "test-admin-pass"}
EXPECTED_TEMPORARY_UNSCHEDULABLE_RULES = [
    {
        "error_code": 529,
        "duration_minutes": 60,
        "keywords": ["overloaded", "too many"],
        "description": "服务过载 - 暂停 60 分钟",
    },
    {
        "error_code": 429,
        "duration_minutes": 10,
        "keywords": ["rate limit", "too many requests"],
        "description": "触发限流 - 暂停 10 分钟",
    },
    {
        "error_code": 503,
        "duration_minutes": 30,
        "keywords": ["unavailable", "maintenance"],
        "description": "服务不可用 - 暂停 30 分钟",
    },
]
EXPECTED_MODEL_WHITELIST_MAPPING = {
    "gpt-5.3-codex": "gpt-5.3-codex",
    "gpt-5.4": "gpt-5.4",
    "gpt-5.4-mini": "gpt-5.4-mini",
    "gpt-5.5": "gpt-5.5",
}
EXPECTED_DEFAULT_SCHEDULED_TEST_PLAN = {
    "model_id": "gpt-5.5",
    "cron_expression": "*/5 * * * *",
    "enabled": True,
    "max_results": 100,
    "auto_recover": True,
}
# A grok exchange-code response, with two fields upstream does not put on a grok
# account (`plan_type` is openai's, `raw_response` is debug noise) so the tests can
# show the credential white-list dropping them instead of persisting them.
GROK_EXCHANGE_PAYLOAD = {
    "access_token": "grok-access-1",
    "refresh_token": "grok-refresh-1",
    "id_token": "grok-id-1",
    "token_type": "Bearer",
    "expires_at": "2026-09-01T00:00:00Z",
    "client_id": "grok-client",
    "scope": "openid profile email offline_access",
    "email": "user@example.com",
    "sub": "grok-sub-1",
    "team_id": "grok-team-1",
    "subscription_tier": "SuperGrok",
    "entitlement_status": "active",
    "base_url": "https://api.x.ai/v1",
    "plan_type": "should-be-dropped",
    "raw_response": {"should": "be dropped"},
}
EXPECTED_GROK_MODEL_MAPPING = {
    "composer-2.5": "composer-2.5",
    "grok-4.5": "grok-4.5",
    "grok-4.6": "grok-4.6",
}
# The full shape a freshly created grok OAuth account carries: exchange fields the
# white-list keeps, plus what the provisioning template stamps for grok. Mirrors the
# hand-configured grok accounts upstream, `base_url` included — that one is the
# template's `account_base_url`, never the exchange's (upstream sends none, and the
# api.x.ai value in GROK_EXCHANGE_PAYLOAD must not leak through).
EXPECTED_GROK_CREDENTIALS = {
    "access_token": "grok-access-1",
    "refresh_token": "grok-refresh-1",
    "id_token": "grok-id-1",
    "token_type": "Bearer",
    "expires_at": "2026-09-01T00:00:00Z",
    "client_id": "grok-client",
    "scope": "openid profile email offline_access",
    "email": "user@example.com",
    "sub": "grok-sub-1",
    "team_id": "grok-team-1",
    "subscription_tier": "SuperGrok",
    "entitlement_status": "active",
    "temp_unschedulable_enabled": True,
    "temp_unschedulable_rules": EXPECTED_TEMPORARY_UNSCHEDULABLE_RULES,
    "model_mapping": EXPECTED_GROK_MODEL_MAPPING,
    "base_url": "https://cli-chat-proxy.grok.com/v1",
}
EXPECTED_GROK_EXTRA = {
    "email": "user@example.com",
    "subscription_tier": "SuperGrok",
    "entitlement_status": "active",
    "grok_client_tool_cache_enabled": True,
}


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self.text = json.dumps(payload)
        self.content = self.text.encode()

    def json(self) -> dict[str, object]:
        return self._payload


class FakeRotationSub2API:
    def __init__(self) -> None:
        self.users = [
            {
                "id": 101,
                "email": "rotate@example.com",
                "name": "Rotate Operator",
                "username": "rotator",
                "status": "active",
                "group_id": 11,
                "group_name": "rotation-low",
                "balance": 12.5,
                "balance_display": "12.5 credits",
                "balance_unit": "credits",
            },
            {
                "id": 202,
                "email": "idle@example.com",
                "name": "idle@example.com",
                "status": "active",
                "group_id": 22,
                "group_name": "rotation-high",
                "balance": 3.0,
                "balance_display": "3.0 credits",
                "balance_unit": "credits",
            },
        ]
        self.groups = [
            {
                "id": 11,
                "name": "rotation-low",
                "type": "standard",
                "platform": "openai",
                "status": "active",
                "is_exclusive": True,
                "account_count": 2,
                "active_account_count": 1,
                "rpm_limit": 120,
                "rate_multiplier": 1.5,
                "daily_limit_usd": 10,
                "weekly_limit_usd": 50,
                "monthly_limit_usd": 200,
            },
            {
                "id": 22,
                "name": "rotation-high",
                "type": "standard",
                "platform": "openai",
                "status": "active",
                "is_exclusive": True,
                "account_count": 1,
                "active_account_count": 1,
                "rpm_limit": 0,
                "rate_multiplier": 1,
                "daily_limit_usd": 0,
                "weekly_limit_usd": 0,
                "monthly_limit_usd": 0,
            },
            {
                "id": 33,
                "name": "public-shared",
                "type": "standard",
                "platform": "openai",
                "status": "active",
                "is_exclusive": False,
            },
            {
                "id": 44,
                "name": "subscription-dedicated",
                "type": "subscription",
                "platform": "openai",
                "status": "active",
                "is_exclusive": True,
                "subscription_id": "sub-1",
            },
            # A second platform lives in the same upstream: group names mean
            # nothing, only the platform field does.
            {
                "id": 71,
                "name": "grok-low",
                "type": "standard",
                "platform": "grok",
                "status": "active",
                "is_exclusive": True,
            },
            {
                "id": 72,
                "name": "grok-high",
                "type": "standard",
                "platform": "grok",
                "status": "active",
                "is_exclusive": True,
            },
        ]
        self.accounts = [
            {
                "id": "acct-1",
                "name": "openai-account-low",
                "email": "oa-low@example.com",
                "provider": "openai",
                "platform": "openai",
                "type": "oauth",
                "status": "active",
                "available": True,
                "concurrency": 3,
                "current_concurrency": 1,
                "quota_remaining": 85.5,
                "last_checked_at": "2026-05-11T08:00:00Z",
                "extra": {
                    "codex_5h_used_percent": 39,
                    "codex_7d_used_percent": 85,
                    "codex_usage_updated_at": "2026-05-11T13:59:49+08:00",
                },
                "group_ids": [11],
            },
            {
                "id": "acct-2",
                "name": "openai-account-high",
                "provider": "openai",
                "platform": "openai",
                "type": "oauth",
                "status": "rate_limited",
                "rate_limited": True,
                "concurrency": 7,
                "current_concurrency": 2,
                "extra": {
                    "codex_5h_used_percent": "0%",
                    "codex_7d_used_percent": 25,
                },
                "error_message": "429 too many requests",
                "reset_at": "2026-05-11T09:00:00Z",
                "groups": [{"id": 22, "name": "rotation-high"}],
            },
            {
                "id": 7,
                "name": "numeric-account-id",
                "provider": "openai",
                "platform": "openai",
                "type": "oauth",
                "status": "active",
                "available": True,
            },
            {
                "id": "acct-camel",
                "name": "camel-case-bindings",
                "provider": "openai",
                "platform": "openai",
                "type": "oauth",
                "status": "active",
                "available": True,
                "groupIds": [33],
                "binding": {"id": "binding-1", "groupId": 44, "groupName": "subscription-dedicated"},
            },
            {
                "id": "acct-grok-low",
                "name": "grok-account-low",
                "provider": "grok",
                "platform": "grok",
                "type": "oauth",
                "status": "active",
                "available": True,
                "schedulable": True,
                "group_ids": [71],
            },
            {
                "id": "acct-grok-high",
                "name": "grok-account-high",
                "provider": "grok",
                "platform": "grok",
                "type": "oauth",
                "status": "active",
                "available": True,
                "schedulable": True,
                "group_ids": [72],
            },
        ]
        self.user_api_keys: dict[int, list[dict[str, object]]] = {}
        self.usage_log_items: list[dict[str, object]] | None = None
        self.users_page_size: int | None = None
        self.api_keys_page_size: int | None = None
        self.replace_calls: list[dict[str, object]] = []
        self.user_update_calls: list[dict[str, object]] = []
        self.api_key_group_calls: list[dict[str, object]] = []
        self.api_key_owner_calls: list[dict[str, object]] = []
        self.api_key_create_calls: list[dict[str, object]] = []
        self.balance_calls: list[dict[str, object]] = []
        self.create_group_calls = 0
        self.create_account_calls = 0
        self.create_account_payloads: list[dict[str, object]] = []
        self.generate_auth_url_calls = 0
        self.exchange_code_calls = 0
        self.update_account_calls: list[dict[str, object]] = []
        self.scheduled_test_plans: dict[str, list[dict[str, object]]] = {}
        self.scheduled_test_plan_calls: list[dict[str, object]] = []
        self.group_usage_by_window = {
            "1d": [
                {
                    "group_id": 11,
                    "group_name": "rotation-low",
                    "requests": 5,
                    "total_tokens": 1000,
                    "cost": 1.0,
                    "actual_cost": 1.0,
                    "account_cost": 1.0,
                },
                {
                    "group_id": 22,
                    "group_name": "rotation-high",
                    "requests": 20,
                    "total_tokens": 4000,
                    "cost": 4.0,
                    "actual_cost": 4.0,
                    "account_cost": 4.0,
                },
            ],
            "7d": [
                {
                    "group_id": 11,
                    "group_name": "rotation-low",
                    "requests": 35,
                    "total_tokens": 7000,
                    "cost": 7.0,
                    "actual_cost": 7.0,
                    "account_cost": 7.0,
                },
                {
                    "group_id": 22,
                    "group_name": "rotation-high",
                    "requests": 70,
                    "total_tokens": 14000,
                    "cost": 14.0,
                    "actual_cost": 14.0,
                    "account_cost": 14.0,
                },
            ],
            "30d": [
                {
                    "group_id": 11,
                    "group_name": "rotation-low",
                    "requests": 150,
                    "total_tokens": 30000,
                    "cost": 30.0,
                    "actual_cost": 30.0,
                    "account_cost": 30.0,
                },
                {
                    "group_id": 22,
                    "group_name": "rotation-high",
                    "requests": 300,
                    "total_tokens": 60000,
                    "cost": 60.0,
                    "actual_cost": 60.0,
                    "account_cost": 60.0,
                },
            ],
        }

    def request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        if method == "GET" and path == "/api/v1/admin/groups/all":
            return FakeResponse(200, {"code": 0, "message": "success", "data": self.groups})
        if method == "GET" and path == "/api/v1/admin/users":
            if self.users_page_size:
                page = int((params or {}).get("page") or 1)
                page_size = int((params or {}).get("page_size") or self.users_page_size)
                start = (page - 1) * page_size
                items = [
                    self._user_response_item(user)
                    for user in self.users[start : start + page_size]
                ]
                pages = (len(self.users) + page_size - 1) // page_size
                return FakeResponse(
                    200,
                    {
                        "code": 0,
                        "message": "success",
                        "data": {
                            "items": items,
                            "total": len(self.users),
                            "page": page,
                            "page_size": page_size,
                            "pages": pages,
                        },
                    },
                )
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": [self._user_response_item(user) for user in self.users],
                },
            )
        if (
            method == "GET"
            and path.startswith("/api/v1/admin/users/")
            and len(path.split("/")) == 6
        ):
            user = self._find_user(path.split("/")[5])
            if user is None:
                return FakeResponse(404, {"message": "user not found"})
            return FakeResponse(
                200,
                {"code": 0, "message": "success", "data": self._user_response_item(user)},
            )
        if method == "POST" and path.startswith("/api/v1/admin/users/") and path.endswith("/balance"):
            user_id = int(path.split("/")[5])
            self.balance_calls.append(
                {
                    "user_id": user_id,
                    "balance": json["balance"],
                    "operation": json["operation"],
                    "notes": json["notes"],
                }
            )
            if user_id == 202 and json["operation"] == "subtract":
                return FakeResponse(422, {"message": "insufficient balance"})
            for user in self.users:
                if user["id"] == user_id:
                    current = float(user.get("balance") or 0)
                    delta = float(json["balance"])
                    user["balance"] = current + delta if json["operation"] == "add" else current - delta
                    return FakeResponse(
                        200,
                        {"code": 0, "message": "success", "data": {"balance": user["balance"]}},
                    )
            return FakeResponse(404, {"message": "user not found"})
        if method == "GET" and path == "/api/v1/admin/accounts":
            return FakeResponse(200, {"code": 0, "message": "success", "data": self.accounts})
        if (
            method == "GET"
            and path.startswith("/api/v1/admin/accounts/")
            and len(path.split("/")) == 6
        ):
            account = self._find_account(path.split("/")[5])
            if account is None:
                return FakeResponse(404, {"message": "account not found"})
            return FakeResponse(200, {"code": 0, "message": "success", "data": account})
        if (
            method == "GET"
            and path.startswith("/api/v1/admin/accounts/")
            and path.endswith("/scheduled-test-plans")
        ):
            account_id = path.split("/")[5]
            self.scheduled_test_plan_calls.append({"method": "GET", "account_id": account_id})
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": self.scheduled_test_plans.get(account_id, []),
                },
            )
        if method == "POST" and path == "/api/v1/admin/groups":
            self.create_group_calls += 1
            return FakeResponse(
                200,
                {"code": 0, "message": "success", "data": {"id": 999, "name": json["name"]}},
            )
        if method == "POST" and path == "/api/v1/admin/users":
            return FakeResponse(
                200,
                {"code": 0, "message": "success", "data": {"id": 101, "email": json["email"]}},
            )
        if method == "PUT" and path.startswith("/api/v1/admin/users/"):
            user_id_value = path.split("/")[5]
            try:
                user_id = int(user_id_value)
            except ValueError:
                user_id = user_id_value
            # The upstream update-user request struct carries no top-level
            # group_id, so a client that sends one has it silently discarded.
            # Refuse to model a field the real server would ignore.
            assert "group_id" not in (json or {}), (
                "PUT /api/v1/admin/users/{id} has no top-level group_id field"
            )
            self.user_update_calls.append(
                {"user_id": user_id, "allowed_groups": (json or {}).get("allowed_groups")}
            )
            user = self._find_user(user_id)
            if user is None:
                return FakeResponse(404, {"message": "user not found"})
            if "allowed_groups" in (json or {}):
                # Declarative: whatever list arrives becomes the complete set of
                # authorizations (omitted groups are really revoked), and the
                # upstream migrates no API keys along with it.
                self._set_user_allowed_groups(user, list(json["allowed_groups"]))
            return FakeResponse(200, {"code": 0, "message": "success", "data": {"ok": True}})
        if method == "POST" and path == "/api/v1/admin/openai/generate-auth-url":
            self.generate_auth_url_calls += 1
            upstream_state = f"upstream-{json['state']}"
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "auth_url": (
                            "https://auth.example.com/authorize"
                            f"?client_id=sub2api-demo&state={upstream_state}"
                        ),
                        "session_id": f"session-{json['state']}",
                    },
                },
            )
        if method == "POST" and path == "/api/v1/admin/openai/exchange-code":
            self.exchange_code_calls += 1
            assert json["session_id"] == f"session-{json['state'].removeprefix('upstream-')}"
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "access_token": "token-123",
                        "refresh_token": "refresh-123",
                        "provider_user_id": "provider-1",
                    },
                },
            )
        if method == "POST" and path == "/api/v1/admin/accounts":
            self.create_account_calls += 1
            self.create_account_payloads.append(dict(json or {}))
            assert "provider" not in json
            assert json["platform"] == "openai"
            assert json["type"] == "oauth"
            assert json["credentials"]["access_token"] == "token-123"
            assert json["credentials"]["refresh_token"] == "refresh-123"
            assert json["credentials"]["temp_unschedulable_enabled"] is True
            assert (
                json["credentials"]["temp_unschedulable_rules"]
                == EXPECTED_TEMPORARY_UNSCHEDULABLE_RULES
            )
            assert json["credentials"]["model_mapping"] == EXPECTED_MODEL_WHITELIST_MAPPING
            assert json["group_ids"]
            assert json["concurrency"] == 5
            assert json["extra"]["openai_oauth_responses_websockets_v2_mode"] == "context_pool"
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {"account_id": "oa-1", "name": json["name"]},
                },
            )
        if method == "POST" and path == "/api/v1/admin/scheduled-test-plans":
            self.scheduled_test_plan_calls.append(
                {"method": "POST", "json": dict(json or {})}
            )
            account_id = str(json["account_id"])
            plan = {
                "id": len(self.scheduled_test_plan_calls),
                **dict(json or {}),
            }
            self.scheduled_test_plans.setdefault(account_id, []).append(plan)
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": plan,
                },
            )
        if method == "PUT" and path.startswith("/api/v1/admin/accounts/"):
            account_id = path.rsplit("/", 1)[-1]
            self.update_account_calls.append(
                {"account_id": account_id, "path": path, "json": dict(json or {})}
            )
            account = self._find_account(account_id)
            if account is None:
                return FakeResponse(404, {"message": "account not found"})
            if "group_ids" in (json or {}):
                # Declarative: the list that arrives replaces every existing
                # binding, so any group left out is unbound for real.
                self._set_account_group_ids(account, list(json["group_ids"]))
            if json.get("name") not in (None, ""):
                account["name"] = json["name"]
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {"account_id": account_id, "name": account.get("name")},
                },
            )
        if method == "POST" and path == "/api/v1/admin/users/303/replace-group":
            return FakeResponse(500, {"message": "boom"})
        if (
            method == "POST"
            and path.startswith("/api/v1/admin/users/")
            and path.endswith("/replace-group")
        ):
            user_id_value = path.split("/")[5]
            try:
                user_id = int(user_id_value)
            except ValueError:
                user_id = user_id_value
            old_group_id = json["old_group_id"]
            new_group_id = json["new_group_id"]
            self.replace_calls.append(
                {
                    "user_id": user_id,
                    "old_group_id": old_group_id,
                    "new_group_id": new_group_id,
                }
            )
            user = self._find_user(user_id)
            if user is None:
                return FakeResponse(404, {"message": "user not found"})
            # Targeted replacement inside one transaction: grant the new group,
            # move only the keys sitting in the old group, then revoke only the
            # old group. Every other authorization is left alone. The server
            # validates neither the old group's existence nor its ownership, so a
            # wrong old_group_id is a silent no-op reporting migrated_keys: 0.
            allowed_groups = self._user_allowed_groups(user)
            if not any(str(existing) == str(new_group_id) for existing in allowed_groups):
                allowed_groups.append(new_group_id)
            allowed_groups = [
                existing
                for existing in allowed_groups
                if str(existing) != str(old_group_id)
            ]
            self._set_user_allowed_groups(user, allowed_groups)
            migrated_keys = 0
            for api_key in self._api_keys_for(user_id):
                if str(api_key.get("group_id")) == str(old_group_id):
                    api_key["group_id"] = new_group_id
                    migrated_keys += 1
            return FakeResponse(
                200,
                {"code": 0, "message": "success", "data": {"migrated_keys": migrated_keys}},
            )
        if method == "POST" and path.startswith("/api/v1/admin/api-keys/") and path.endswith("/transfer"):
            key_id = path.split("/")[5]
            self.api_key_owner_calls.append(
                {
                    "key_id": key_id,
                    "user_id": json["target_user_id"],
                    "group_id": json["target_group_id"],
                    "quota": json["quota"],
                    "reset_quota": json["reset_quota"],
                }
            )
            key_record: dict[str, object] | None = None
            source_user_id: int | None = None
            for user_id, keys in self.user_api_keys.items():
                for candidate in keys:
                    if str(candidate.get("id") or candidate.get("key_id")) == key_id:
                        key_record = candidate
                        source_user_id = user_id
                        break
                if key_record is not None:
                    break
            if key_record is None:
                return FakeResponse(404, {"message": "api key not found"})
            if source_user_id is not None:
                self.user_api_keys[source_user_id] = [
                    candidate
                    for candidate in self.user_api_keys[source_user_id]
                    if str(candidate.get("id") or candidate.get("key_id")) != key_id
                ]
            key_record["user_id"] = json["target_user_id"]
            key_record["group_id"] = json["target_group_id"]
            key_record["quota"] = json["quota"]
            if json.get("reset_quota"):
                key_record["quota_used"] = 0.0
            target_user_id = int(json["target_user_id"])
            self.user_api_keys.setdefault(target_user_id, []).append(key_record)
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {"api_key": key_record},
                },
            )
        if method == "POST" and path.startswith("/api/v1/admin/users/") and path.endswith("/api-keys"):
            user_id = int(path.split("/")[5])
            self.api_key_create_calls.append(
                {"user_id": user_id, "path": path, "json": dict(json or {})}
            )
            key_id = f"created-{len(self.api_key_create_calls)}"
            key_record = {
                "id": key_id,
                "key": f"sk-{key_id}",
                "user_id": user_id,
                "name": json["name"],
                "group_id": json.get("group_id"),
                "quota": json.get("quota"),
                "status": "active",
            }
            self.user_api_keys.setdefault(user_id, []).append(key_record)
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {"api_key": key_record},
                },
            )
        if method == "PUT" and path.startswith("/api/v1/admin/api-keys/"):
            key_id = path.split("/")[5]
            self.api_key_group_calls.append({"key_id": key_id, "group_id": json["group_id"]})
            for keys in self.user_api_keys.values():
                for candidate in keys:
                    if str(candidate.get("id") or candidate.get("key_id")) == key_id:
                        candidate["group_id"] = json["group_id"]
            return FakeResponse(200, {"code": 0, "message": "success", "data": {"ok": True}})
        if method == "GET" and path.startswith("/api/v1/admin/users/") and path.endswith("/api-keys"):
            return self._api_keys_response(int(path.split("/")[5]), params=params)
        if method == "GET" and path == "/api/v1/admin/usage/stats":
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {"total_actual_cost": 88.5, "total_requests": 10},
                },
            )
        if method == "GET" and path == "/api/v1/admin/usage":
            return self._usage_logs_response(params=params)
        if method == "GET" and path == "/api/v1/admin/dashboard/users-ranking":
            window_days = {
                "1d": 1,
                "7d": 7,
                "30d": 30,
            }
            start_date = str((params or {}).get("start_date") or "")
            end_date = str((params or {}).get("end_date") or "")
            days = None
            try:
                parsed_start = datetime.fromisoformat(start_date).date()
                parsed_end = datetime.fromisoformat(end_date).date()
                days = (parsed_end - parsed_start).days + 1
            except ValueError:
                days = None
            window = next(
                (key for key, value in window_days.items() if value == days),
                "1d",
            )
            costs = {
                101: {"1d": 2.5, "7d": 6.0, "30d": 20.0},
                202: {"1d": 0.5, "7d": 1.2, "30d": 4.0},
            }
            items = [
                {
                    "user_id": user["id"],
                    "email": user.get("email"),
                    "actual_cost": costs.get(int(user["id"]), {}).get(window, 0.0),
                    "requests": 1,
                    "tokens": 100,
                }
                for user in self.users
            ]
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {"ranking": items, "total": len(items)},
                },
            )
        if method == "GET" and path == "/api/v1/admin/dashboard/groups":
            window_days = {
                "1d": 1,
                "7d": 7,
                "30d": 30,
            }
            start_date = str((params or {}).get("start_date") or "")
            end_date = str((params or {}).get("end_date") or "")
            days = None
            try:
                parsed_start = datetime.fromisoformat(start_date).date()
                parsed_end = datetime.fromisoformat(end_date).date()
                days = (parsed_end - parsed_start).days + 1
            except ValueError:
                days = None
            window = next(
                (key for key, value in window_days.items() if value == days),
                "1d",
            )
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "groups": self.group_usage_by_window.get(window, []),
                        "total_actual_cost": sum(
                            float(item.get("actual_cost") or 0.0)
                            for item in self.group_usage_by_window.get(window, [])
                        ),
                    },
                },
            )
        return FakeResponse(404, {"detail": f"unexpected {method} {path}"})

    def _usage_logs_response(self, params=None) -> FakeResponse:
        all_items = (
            list(self.usage_log_items)
            if self.usage_log_items is not None
            else self._default_usage_log_items()
        )
        page = int((params or {}).get("page") or 1)
        page_size = int((params or {}).get("page_size") or 1000)
        start = (page - 1) * page_size
        page_items = all_items[start : start + page_size]
        pages = (len(all_items) + page_size - 1) // page_size if page_size else 1
        return FakeResponse(
            200,
            {
                "code": 0,
                "message": "success",
                "data": {
                    "items": page_items,
                    "total": len(all_items),
                    "page": page,
                    "page_size": page_size,
                    "pages": pages,
                },
            },
        )

    def _find_user(self, user_id: object) -> dict[str, object] | None:
        for user in self.users:
            if str(user.get("id")) == str(user_id):
                return user
        return None

    def _find_account(self, account_id: object) -> dict[str, object] | None:
        for account in self.accounts:
            if str(account.get("id")) == str(account_id):
                return account
        return None

    def _group_name(self, group_id: object) -> str:
        return next(
            (
                str(group.get("name") or "")
                for group in self.groups
                if str(group.get("id")) == str(group_id)
            ),
            "",
        )

    @staticmethod
    def _dedupe_group_ids(values: object) -> list[object]:
        group_ids: list[object] = []
        for value in values if isinstance(values, list) else []:
            if isinstance(value, dict):
                value = value.get("id") or value.get("group_id") or value.get("groupId")
            if value in (None, ""):
                continue
            if not any(str(existing) == str(value) for existing in group_ids):
                group_ids.append(value)
        return group_ids

    def _user_allowed_groups(self, user: dict[str, object]) -> list[object]:
        for field_name in ("allowed_groups", "allowedGroups", "group_ids"):
            if field_name in user:
                return self._dedupe_group_ids(user.get(field_name))
        group_id = user.get("group_id")
        return [] if group_id in (None, "") else [group_id]

    def _set_user_allowed_groups(
        self, user: dict[str, object], group_ids: list[object]
    ) -> None:
        """Store the new authorizations the way the upstream would report them.

        Upstream users carry `allowed_groups` and nothing else — the "direct
        group" the sidecar reads is simply the case where exactly one group is
        authorized, so it is derived here rather than stored independently.
        """
        group_ids = self._dedupe_group_ids(group_ids)
        user["allowed_groups"] = list(group_ids)
        user["group_ids"] = list(group_ids)
        user.pop("allowedGroups", None)
        user.pop("groups", None)
        if len(group_ids) == 1:
            user["group_id"] = group_ids[0]
            user["group_name"] = self._group_name(group_ids[0])
        else:
            user["group_id"] = None
            user["group_name"] = None

    def _set_account_group_ids(
        self, account: dict[str, object], group_ids: list[object]
    ) -> None:
        group_ids = self._dedupe_group_ids(group_ids)
        account["group_ids"] = list(group_ids)
        account["groups"] = [
            {"id": group_id, "name": self._group_name(group_id)} for group_id in group_ids
        ]
        for legacy_field in ("groupIds", "binding", "bindings", "group_id", "current_group_id"):
            account.pop(legacy_field, None)

    def _api_keys_for(self, user_id: object) -> list[dict[str, object]]:
        """The user's keys, materializing the default fixture on first access.

        Both the api-keys listing and replace-group have to see the same key
        rows, otherwise replace-group would report migrations for keys the
        sidecar never observes (or miss the ones it does).
        """
        try:
            key = int(user_id)
        except (TypeError, ValueError):
            key = user_id
        return self.user_api_keys.setdefault(
            key,
            [
                {
                    "id": f"key-{key}",
                    "name": "primary",
                    "group_id": 11,
                    "group_name": "rotation-low",
                    "usage_5h": 1.0,
                    "usage_1d": 2.0,
                    "usage_7d": 3.0,
                }
            ],
        )

    def _user_response_item(self, user: dict[str, object]) -> dict[str, object]:
        current_group_id = user.get("current_group_id", user.get("group_id"))
        current_group_name = user.get("current_group_name", user.get("group_name"))
        group_ids = user.get("group_ids") or user.get("allowed_groups")
        return {
            **user,
            "current_group_id": current_group_id,
            "current_group_name": current_group_name,
            "group_ids": group_ids
            or ([current_group_id] if current_group_id not in (None, "") else []),
        }

    def _default_usage_log_items(self) -> list[dict[str, object]]:
        costs = {101: 1.5, 202: 0.2}
        items: list[dict[str, object]] = []
        for user in self.users:
            user_id = user.get("id")
            try:
                cost = costs.get(int(user_id), 0.0)
            except (TypeError, ValueError):
                cost = 0.0
            if cost <= 0:
                continue
            group_id = user.get("current_group_id", user.get("group_id"))
            items.append(
                usage_log_item(
                    user_id=user_id,
                    group_id=group_id,
                    actual_cost=cost,
                )
            )
        return items

    def _api_keys_response(self, user_id: int, params=None) -> FakeResponse:
        items = self._api_keys_for(user_id)
        page = int((params or {}).get("page") or 1)
        page_size = int((params or {}).get("page_size") or self.api_keys_page_size or 1000)
        if self.api_keys_page_size:
            start = (page - 1) * page_size
            page_items = items[start : start + page_size]
        else:
            page_items = items
        pages = (len(items) + page_size - 1) // page_size if page_size else 1
        return FakeResponse(
            200,
            {
                "code": 0,
                "message": "success",
                "data": {
                    "items": page_items,
                    "total": len(items),
                    "page": page,
                    "page_size": page_size,
                    "pages": pages,
                },
            },
        )


def clear_caches() -> None:
    get_settings.cache_clear()
    main.get_auth_manager.cache_clear()
    main.get_flow_store.cache_clear()
    main.get_sub2api_client.cache_clear()
    main.get_rotation_service.cache_clear()
    main.get_rotation_service_for_upstream.cache_clear()
    main.get_api_key_automation_service.cache_clear()
    main.get_provisioning_service.cache_clear()
    main.get_notification_service.cache_clear()
    main.get_operational_data_refresher.cache_clear()
    main.get_credit_control_service.cache_clear()
    main.get_usage_segmentation_service.cache_clear()
    main.get_group_usage_service.cache_clear()


def database_config_from_app_env(app_env: dict[str, str]) -> str:
    parsed = urlparse(app_env["database_url"])
    return "\n".join(
        [
            "database:",
            f"  url: {json.dumps(parsed.hostname or '')}",
            f"  port: {parsed.port or 5432}",
            f"  username: {json.dumps(unquote(parsed.username or ''))}",
            f"  name: {json.dumps(unquote(parsed.path.lstrip('/')))}",
            "",
        ]
    )


def save_auto_rotation_config(
    *,
    enabled: bool = True,
    auto_assign_new_users: bool = False,
    usage_window: AutoRotationUsageWindow = AutoRotationUsageWindow.window_5h,
    usage_thresholds: tuple[float, ...] = (),
    imbalance_epsilon: float = 0.0,
    improvement_delta: float = 0.0,
    schedule_source_group_ids: tuple[object, ...] = (),
) -> AutoRotationRuntimeConfig:
    return main.get_flow_store().save_auto_rotation_config(
        AutoRotationRuntimeConfig(
            enabled=enabled,
            auto_assign_new_users=auto_assign_new_users,
            cooldown_minutes=0,
            usage_window=usage_window,
            usage_thresholds=usage_thresholds,
            imbalance_epsilon=imbalance_epsilon,
            improvement_delta=improvement_delta,
            schedule_source_group_ids=schedule_source_group_ids,
        )
    )


def add_available_account_for_group(
    backend: FakeRotationSub2API,
    group_id: object = 22,
    *,
    account_id: object | None = None,
) -> None:
    account_key = account_id or f"acct-{group_id}-available"
    backend.accounts.append(
        {
            "id": account_key,
            "name": f"openai-account-{group_id}-available",
            "provider": "openai",
            "platform": "openai",
            "type": "oauth",
            "status": "active",
            "available": True,
            "schedulable": True,
            "group_ids": [group_id],
        }
    )


def usage_log_item(
    *,
    user_id: object | None = None,
    group_id: object | None = None,
    actual_cost: float,
) -> dict[str, object]:
    item: dict[str, object] = {
        "id": f"usage-{user_id or 'group'}-{group_id or 'none'}-{actual_cost}",
        "created_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "total_cost": actual_cost,
        "actual_cost": actual_cost,
        "input_tokens": 100,
        "output_tokens": 50,
    }
    if user_id is not None:
        item["user_id"] = user_id
    if group_id is not None:
        item["group_id"] = group_id
    return item


def save_operational_snapshots(backend: FakeRotationSub2API) -> None:
    store = main.get_flow_store()
    now = datetime.now(timezone.utc)
    groups = []
    for group in backend.groups:
        group_kind = group.get("group_kind", group.get("type"))
        groups.append(
            {
                **group,
                "group_kind": group_kind,
                "is_subscription": bool(
                    group.get("is_subscription")
                    or str(group_kind or "").strip().lower() == "subscription"
                    or group.get("subscription_id") not in (None, "")
                ),
            }
        )
    users = []
    for user in backend.users:
        current_group_id = user.get("current_group_id", user.get("group_id"))
        current_group_name = user.get("current_group_name", user.get("group_name"))
        # Mirror the client parser: every authorization the user holds survives
        # into the snapshot, which is what the per-platform bucketing reads.
        group_ids = list(user.get("group_ids") or user.get("allowed_groups") or [])
        if current_group_id not in (None, "") and not any(
            str(existing) == str(current_group_id) for existing in group_ids
        ):
            group_ids.insert(0, current_group_id)
        users.append(
            {
                **user,
                "current_group_id": current_group_id,
                "current_group_name": current_group_name,
                "group_ids": group_ids,
            }
        )
    user_usage: dict[str, dict[str, dict[str, float]]] = {}
    for user in users:
        user_id = int(user["id"])
        costs = {
            101: {"5h": 1.5, "1d": 2.5, "7d": 6.0, "30d": 20.0},
            202: {"5h": 0.2, "1d": 0.5, "7d": 1.2, "30d": 4.0},
            808: {"5h": 1.0, "1d": 2.0, "7d": 5.0, "30d": 15.0},
            909: {"5h": 0.1, "1d": 0.3, "7d": 0.8, "30d": 3.0},
        }.get(user_id, {})
        user_usage[str(user_id)] = {
            window: {"total_cost": cost, "total_actual_cost": cost}
            for window, cost in costs.items()
        }
    user_api_keys = {
        str(user["id"]): {
            "items": backend.user_api_keys.get(
                int(user["id"]),
                [
                    {
                        "id": f"key-{user['id']}",
                        "name": "primary",
                        "group_id": 11,
                        "group_name": "rotation-low",
                        "usage_5h": 1.0,
                        "usage_1d": 2.0,
                        "usage_7d": 3.0,
                    }
                ],
            ),
            "total": len(
                backend.user_api_keys.get(
                    int(user["id"]),
                    [{"id": f"key-{user['id']}"}],
                )
            ),
        }
        for user in users
    }
    for source_key, payload in {
        "accounts": backend.accounts,
        "groups": groups,
        "users": users,
        "user_usage": user_usage,
        "group_usage": {
            "11": {
                "5h": {
                    "group_id": 11,
                    "window": "5h",
                    "total_requests": 1,
                    "total_tokens": 100,
                    "total_cost": 0.2,
                    "total_actual_cost": 0.2,
                    "total_account_cost": 0.2,
                    "source": "usage_logs",
                },
                "1d": {
                    "group_id": 11,
                    "window": "1d",
                    "total_requests": 5,
                    "total_tokens": 1000,
                    "total_cost": 1.0,
                    "total_actual_cost": 1.0,
                    "total_account_cost": 1.0,
                    "source": "dashboard_groups",
                },
                "7d": {
                    "group_id": 11,
                    "window": "7d",
                    "total_requests": 35,
                    "total_tokens": 7000,
                    "total_cost": 7.0,
                    "total_actual_cost": 7.0,
                    "total_account_cost": 7.0,
                    "source": "dashboard_groups",
                },
                "30d": {
                    "group_id": 11,
                    "window": "30d",
                    "total_requests": 150,
                    "total_tokens": 30000,
                    "total_cost": 30.0,
                    "total_actual_cost": 30.0,
                    "total_account_cost": 30.0,
                    "source": "dashboard_groups",
                },
            },
            "22": {
                "5h": {
                    "group_id": 22,
                    "window": "5h",
                    "total_requests": 2,
                    "total_tokens": 200,
                    "total_cost": 1.7,
                    "total_actual_cost": 1.7,
                    "total_account_cost": 1.7,
                    "source": "usage_logs",
                },
                "1d": {
                    "group_id": 22,
                    "window": "1d",
                    "total_requests": 20,
                    "total_tokens": 4000,
                    "total_cost": 4.0,
                    "total_actual_cost": 4.0,
                    "total_account_cost": 4.0,
                    "source": "dashboard_groups",
                },
                "7d": {
                    "group_id": 22,
                    "window": "7d",
                    "total_requests": 70,
                    "total_tokens": 14000,
                    "total_cost": 14.0,
                    "total_actual_cost": 14.0,
                    "total_account_cost": 14.0,
                    "source": "dashboard_groups",
                },
                "30d": {
                    "group_id": 22,
                    "window": "30d",
                    "total_requests": 300,
                    "total_tokens": 60000,
                    "total_cost": 60.0,
                    "total_actual_cost": 60.0,
                    "total_account_cost": 60.0,
                    "source": "dashboard_groups",
                },
            },
        },
        "user_api_keys": user_api_keys,
    }.items():
        store.save_operational_data_snapshot(
            OperationalDataSnapshot(
                source_key=source_key,
                observed_at=now,
                collected_at=now,
                payload=payload,
            )
        )


def fake_sub2api_request(self, method: str, url: str, json=None, params=None, timeout=None):
    path = urlparse(url).path
    if method == "GET" and path == "/api/v1/admin/groups/all":
        return FakeResponse(200, {"items": []})
    if method == "GET" and path == "/api/v1/admin/accounts":
        return FakeResponse(200, {"items": []})
    if (
        method == "GET"
        and path.startswith("/api/v1/admin/accounts/")
        and path.endswith("/scheduled-test-plans")
    ):
        return FakeResponse(200, {"data": []})
    if method == "POST" and path == "/api/v1/admin/groups":
        assert json["platform"] == "openai"
        assert json["is_exclusive"] is True
        assert json["subscription_type"] == "standard"
        assert json["rpm_limit"] == 0
        assert json["daily_limit_usd"] is None
        assert json["messages_dispatch_model_config"] == {
            "opus_mapped_model": "gpt-5.4",
            "sonnet_mapped_model": "gpt-5.3-codex",
            "haiku_mapped_model": "gpt-5.4-mini",
            "exact_model_mappings": {},
        }
        assert json["require_oauth_only"] is False
        return FakeResponse(200, {"id": "g-1", "name": json["name"]})
    if method == "POST" and path == "/api/v1/admin/users":
        return FakeResponse(200, {"id": "u-1", "email": json["email"]})
    if method == "POST" and path == "/api/v1/admin/openai/generate-auth-url":
        assert "redirect_uri" not in json
        upstream_state = f"upstream-{json['state']}"
        return FakeResponse(
            200,
            {
                "auth_url": (
                    "https://auth.example.com/authorize"
                    "?redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback"
                    f"&client_id=sub2api-demo&state={upstream_state}"
                ),
                "session_id": f"session-{json['state']}",
            },
        )
    if method == "POST" and path == "/api/v1/admin/openai/exchange-code":
        assert "redirect_uri" not in json
        assert json["session_id"] == f"session-{json['state'].removeprefix('upstream-')}"
        return FakeResponse(
            200,
            {
                "access_token": "token-123",
                "refresh_token": "refresh-123",
                "provider_user_id": "provider-1",
            },
        )
    if method == "POST" and path == "/api/v1/admin/accounts":
        assert "provider" not in json
        assert json["platform"] == "openai"
        assert json["type"] == "oauth"
        assert "email" not in json
        assert "group_id" not in json
        assert json["group_ids"] == ["g-1"]
        assert json["credentials"]["access_token"] == "token-123"
        assert json["credentials"]["refresh_token"] == "refresh-123"
        assert json["credentials"]["temp_unschedulable_enabled"] is True
        assert (
            json["credentials"]["temp_unschedulable_rules"]
            == EXPECTED_TEMPORARY_UNSCHEDULABLE_RULES
        )
        assert json["credentials"]["model_mapping"] == EXPECTED_MODEL_WHITELIST_MAPPING
        assert json["extra"]["openai_oauth_responses_websockets_v2_mode"] == "context_pool"
        assert json["extra"]["openai_oauth_responses_websockets_v2_enabled"] is True
        assert json["concurrency"] == 5
        return FakeResponse(
            200,
            {
                "account_id": "oa-1",
                "name": json["name"],
            },
        )
    if method == "POST" and path == "/api/v1/admin/scheduled-test-plans":
        assert json == {
            "account_id": "oa-1",
            **EXPECTED_DEFAULT_SCHEDULED_TEST_PLAN,
        }
        return FakeResponse(200, {"id": "stp-1", **json})
    # No POST /api/v1/admin/groups/{id}/accounts route exists upstream; anything
    # that still calls it has to fail here the way production 404s.
    return FakeResponse(404, {"detail": f"unexpected {method} {path}"})


def test_sub2api_client_updates_single_api_key_group_with_admin_endpoint() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "json": json})
        return FakeResponse(200, {"code": 0, "message": "success", "data": {"ok": True}})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.update_api_key_group(key_id="key-1", group_id=123)

    assert result["key_id"] == "key-1"
    assert result["group_id"] == 123
    assert calls == [
        {
            "method": "PUT",
            "path": "/api/v1/admin/api-keys/key-1",
            "json": {"group_id": 123},
        }
    ]


def test_sub2api_client_lists_all_user_api_keys_with_user_context() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        calls.append({"method": method, "path": path, "params": dict(params or {})})
        if path == "/api/v1/admin/users":
            page = int(params["page"])
            users = [
                {"id": 1, "email": "admin@example.com", "name": "Admin"},
                {"id": 2, "email": "source@example.com", "name": "Source"},
            ]
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "items": [users[page - 1]],
                        "total": 2,
                        "page": page,
                        "page_size": 1,
                        "pages": 2,
                    },
                },
            )
        if path == "/api/v1/admin/users/1/api-keys":
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "items": [{"id": "admin-key", "name": "admin-key"}],
                        "total": 1,
                        "page": 1,
                        "page_size": 1,
                        "pages": 1,
                    },
                },
            )
        if path == "/api/v1/admin/users/2/api-keys":
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "items": [{"id": "source-key", "name": "source-key"}],
                        "total": 1,
                        "page": 1,
                        "page_size": 1,
                        "pages": 1,
                    },
                },
            )
        return FakeResponse(404, {"detail": f"unexpected {method} {path}"})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.list_all_user_api_keys(page_size=1)

    assert result["total"] == 2
    keys_by_id = {item["id"]: item for item in result["items"]}
    assert keys_by_id["admin-key"]["user_id"] == 1
    assert keys_by_id["admin-key"]["owner_email"] == "admin@example.com"
    assert keys_by_id["source-key"]["user_id"] == 2
    assert keys_by_id["source-key"]["owner_email"] == "source@example.com"
    call_paths = [call["path"] for call in calls]
    # User pagination happens first and in order...
    assert call_paths[:2] == ["/api/v1/admin/users", "/api/v1/admin/users"]
    # ...then per-user api-key fetches fan out across a thread pool, so their relative
    # order is not guaranteed — assert membership rather than sequence.
    assert sorted(call_paths[2:]) == [
        "/api/v1/admin/users/1/api-keys",
        "/api/v1/admin/users/2/api-keys",
    ]


def test_list_all_user_api_keys_fans_out_across_thread_pool() -> None:
    import threading

    worker_threads: set[str] = set()
    lock = threading.Lock()

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        if path == "/api/v1/admin/users":
            users = [{"id": idx, "email": f"u{idx}@example.com"} for idx in range(1, 6)]
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "items": users,
                        "total": len(users),
                        "page": 1,
                        "page_size": 1000,
                        "pages": 1,
                    },
                },
            )
        if path.endswith("/api-keys"):
            with lock:
                worker_threads.add(threading.current_thread().name)
            user_id = path.split("/")[-2]
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "items": [{"id": f"key-{user_id}"}],
                        "total": 1,
                        "page": 1,
                        "page_size": 1000,
                        "pages": 1,
                    },
                },
            )
        return FakeResponse(404, {"detail": f"unexpected {method} {path}"})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
        api_keys_fetch_concurrency=4,
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.list_all_user_api_keys()

    assert result["total"] == 5
    assert result["users_total"] == 5
    # The per-user fetches ran on the bounded "sub2api-apikeys" worker pool rather than
    # the calling thread, proving the N+1 was fanned out.
    assert any(name.startswith("sub2api-apikeys") for name in worker_threads)


def test_sub2api_client_configures_retrying_pooled_adapter() -> None:
    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
        max_retries=3,
        api_keys_fetch_concurrency=12,
    )

    adapter = client.session.get_adapter("https://sub2api.example.com")
    retry = adapter.max_retries

    assert retry.total == 3
    assert 503 in retry.status_forcelist
    assert 429 in retry.status_forcelist
    # Only idempotent reads are retried; POSTs must never be auto-retried.
    assert retry.allowed_methods == frozenset({"GET"})
    # Connection pool is sized to the fan-out width so concurrent fetches don't thrash it.
    assert adapter._pool_maxsize >= 12


def _usage_log_pagination_request(
    total_items: int, page_size: int, metadata: str = "full"
):
    """Build a fake requests.Session.request that paginates /admin/usage.

    Tracks how many usage pages were fetched so tests can assert the client stops
    paginating once the in-memory cap is reached. The counter is lock-protected
    because the client fetches page waves from a thread pool.

    metadata modes: "full" reports the real total/pages; "none" omits them;
    "progressive" mimics the real upstream, which only reports
    total=page*page_size+1 / pages=page+1 as a "has more" hint while more data
    exists (the real total appears only on the final page).
    """

    state = {"pages_fetched": 0}
    state_lock = threading.Lock()

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        if path != "/api/v1/admin/usage":
            return FakeResponse(404, {"detail": f"unexpected {method} {path}"})
        with state_lock:
            state["pages_fetched"] += 1
        page = int((params or {}).get("page", 1))
        start = (page - 1) * page_size
        items = [
            {"id": idx, "user_id": "u1"}
            for idx in range(start, min(start + page_size, total_items))
        ]
        data: dict[str, object] = {
            "items": items,
            "page": page,
            "page_size": page_size,
        }
        if metadata == "full":
            data["total"] = total_items
            data["pages"] = (total_items + page_size - 1) // page_size
        elif metadata == "progressive":
            if start + page_size < total_items:
                data["total"] = page * page_size + 1
                data["pages"] = page + 1
            else:
                data["total"] = total_items
                data["pages"] = page
        return FakeResponse(200, {"code": 0, "message": "success", "data": data})

    return fake_request, state


def test_list_usage_logs_caps_accumulated_items() -> None:
    fake_request, state = _usage_log_pagination_request(total_items=10, page_size=2)
    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
        usage_log_max_items=5,
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.list_usage_logs(
            start_date=datetime(2026, 5, 10).date(),
            end_date=datetime(2026, 5, 10).date(),
            timezone_name="UTC",
            page_size=2,
        )

    # Items are truncated to exactly the cap, and pagination stops early
    # (3 pages of 2 reach 6 >= 5, then trim to 5) rather than fetching all 5 pages.
    assert len(result["items"]) == 5
    assert state["pages_fetched"] == 3


def test_list_usage_logs_unlimited_by_default() -> None:
    fake_request, state = _usage_log_pagination_request(total_items=10, page_size=2)
    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.list_usage_logs(
            start_date=datetime(2026, 5, 10).date(),
            end_date=datetime(2026, 5, 10).date(),
            timezone_name="UTC",
            page_size=2,
        )

    assert len(result["items"]) == 10
    assert result["total"] == 10
    # Page metadata is never trusted for the stop decision: the client requests a
    # full wave of page_fetch_concurrency (8) pages concurrently and discards the
    # empty ones past the end. Bounded waste, in exchange for surviving upstreams
    # whose total/pages fields are lower-bound hints rather than real counts.
    assert state["pages_fetched"] == 8
    # Pages are fetched from a thread pool in waves; the merged order must still
    # match the upstream sort (page order preserved).
    assert [item["id"] for item in result["items"]] == list(range(10))


def test_list_usage_logs_crawls_without_page_metadata() -> None:
    fake_request, state = _usage_log_pagination_request(
        total_items=10, page_size=2, metadata="none"
    )
    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.list_usage_logs(
            start_date=datetime(2026, 5, 10).date(),
            end_date=datetime(2026, 5, 10).date(),
            timezone_name="UTC",
            page_size=2,
        )

    assert [item["id"] for item in result["items"]] == list(range(10))
    assert result["total"] == 10
    # One full wave of 8 concurrent page requests; pages 6-8 come back empty.
    assert state["pages_fetched"] == 8


def test_list_usage_logs_ignores_lying_progressive_page_metadata() -> None:
    # Regression test for the production upstream that reports total=N*page_size+1 /
    # pages=N+1 while more data exists: trusting page-1 metadata would truncate the
    # crawl to 2 pages and silently drop most of the data.
    fake_request, state = _usage_log_pagination_request(
        total_items=9, page_size=2, metadata="progressive"
    )
    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.list_usage_logs(
            start_date=datetime(2026, 5, 10).date(),
            end_date=datetime(2026, 5, 10).date(),
            timezone_name="UTC",
            page_size=2,
        )

    # The final (short) page both ends the crawl and carries the real total.
    assert [item["id"] for item in result["items"]] == list(range(9))
    assert result["total"] == 9
    # One full wave of 8 concurrent page requests; the short page 5 ends the crawl
    # and pages 6-8 are discarded.
    assert state["pages_fetched"] == 8


def _timezone_clear_of_midnight_grace() -> str:
    # The past-day cache skips the first minutes after local midnight; pick a fixed
    # zone where local time is safely mid-day so these tests are never in that window.
    for offset in range(-12, 13):
        name = f"Etc/GMT{offset:+d}"
        if 1 <= datetime.now(ZoneInfo(name)).hour <= 22:
            return name
    return "UTC"


def test_list_usage_logs_caches_immutable_past_day_window() -> None:
    fake_request, state = _usage_log_pagination_request(total_items=10, page_size=2)
    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )
    tz = _timezone_clear_of_midnight_grace()
    past_day = datetime.now(ZoneInfo(tz)).date() - timedelta(days=1)

    with patch.object(requests.Session, "request", new=fake_request):
        first = client.list_usage_logs(
            start_date=past_day, end_date=past_day, timezone_name=tz, page_size=2
        )
        fetched_once = state["pages_fetched"]
        # Caller-side mutation of the returned list must not corrupt the cache.
        first["items"].clear()
        second = client.list_usage_logs(
            start_date=past_day, end_date=past_day, timezone_name=tz, page_size=2
        )

    assert fetched_once > 0
    assert state["pages_fetched"] == fetched_once
    assert [item["id"] for item in second["items"]] == list(range(10))
    assert second["total"] == 10


def test_list_usage_logs_today_stays_live_and_filtered_windows_refetch() -> None:
    fake_request, state = _usage_log_pagination_request(total_items=2, page_size=2)
    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )
    tz = _timezone_clear_of_midnight_grace()
    today = datetime.now(ZoneInfo(tz)).date()
    past_day = today - timedelta(days=1)

    with patch.object(requests.Session, "request", new=fake_request):
        client.list_usage_logs(
            start_date=today, end_date=today, timezone_name=tz, page_size=2
        )
        after_today_first = state["pages_fetched"]
        today_second = client.list_usage_logs(
            start_date=today, end_date=today, timezone_name=tz, page_size=2
        )
        after_today_second = state["pages_fetched"]
        client.list_usage_logs(
            user_id="u1",
            start_date=past_day,
            end_date=past_day,
            timezone_name=tz,
            page_size=2,
        )
        after_filtered_first = state["pages_fetched"]
        client.list_usage_logs(
            user_id="u1",
            start_date=past_day,
            end_date=past_day,
            timezone_name=tz,
            page_size=2,
        )
        after_filtered_second = state["pages_fetched"]

    today_second_pages = after_today_second - after_today_first
    filtered_first_pages = after_filtered_first - after_today_second
    filtered_second_pages = after_filtered_second - after_filtered_first

    # Today's window must stay live: every call revalidates against upstream (the
    # incremental watermark check is at least one request), never serving frozen data.
    assert today_second_pages >= 1
    assert [item["id"] for item in today_second["items"]] == [0, 1]
    # User-filtered windows are never cached and refetch in full each time.
    assert filtered_second_pages == filtered_first_pages
    assert filtered_first_pages > 0


def _live_usage_log_request(initial_ids: list[int], page_size: int):
    """Fake /admin/usage backed by a mutable newest-first id list (append-only feed).

    Prepend ids to state["ids"] to simulate new logs arriving. Metadata mimics the
    real upstream's progressive lie: total/pages only admit one page beyond the
    current one until the final page.
    """

    state = {"ids": list(initial_ids), "pages_fetched": 0}
    state_lock = threading.Lock()

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        if path != "/api/v1/admin/usage":
            return FakeResponse(404, {"detail": f"unexpected {method} {path}"})
        with state_lock:
            state["pages_fetched"] += 1
            ids = list(state["ids"])
        page = int((params or {}).get("page", 1))
        start = (page - 1) * page_size
        chunk = ids[start : start + page_size]
        items = [{"id": value, "user_id": "u1"} for value in chunk]
        if start + page_size < len(ids):
            total, pages = page * page_size + 1, page + 1
        else:
            total, pages = len(ids), page
        return FakeResponse(
            200,
            {
                "code": 0,
                "data": {
                    "items": items,
                    "total": total,
                    "pages": pages,
                    "page": page,
                    "page_size": page_size,
                },
            },
        )

    return fake_request, state


def test_list_usage_logs_current_day_incremental_fetch() -> None:
    fake_request, state = _live_usage_log_request(
        initial_ids=list(range(30, 20, -1)), page_size=4
    )
    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )
    tz = _timezone_clear_of_midnight_grace()
    today = datetime.now(ZoneInfo(tz)).date()

    with patch.object(requests.Session, "request", new=fake_request):
        first = client.list_usage_logs(
            start_date=today, end_date=today, timezone_name=tz, page_size=4
        )
        after_cold = state["pages_fetched"]

        second = client.list_usage_logs(
            start_date=today, end_date=today, timezone_name=tz, page_size=4
        )
        after_no_change = state["pages_fetched"]

        state["ids"][:0] = [33, 32, 31]
        third = client.list_usage_logs(
            start_date=today, end_date=today, timezone_name=tz, page_size=4
        )
        after_growth = state["pages_fetched"]

    assert [item["id"] for item in first["items"]] == list(range(30, 20, -1))
    # No new data: the watermark sits at the top of page 1, one request total.
    assert [item["id"] for item in second["items"]] == list(range(30, 20, -1))
    assert after_no_change - after_cold == 1
    # Three new rows: still a single page 1 fetch, spliced ahead of the cache.
    assert [item["id"] for item in third["items"]] == list(range(33, 20, -1))
    assert after_growth - after_no_change == 1


def test_list_usage_logs_current_day_falls_back_when_watermark_not_found() -> None:
    fake_request, state = _live_usage_log_request(
        initial_ids=list(range(30, 20, -1)), page_size=4
    )
    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )
    tz = _timezone_clear_of_midnight_grace()
    today = datetime.now(ZoneInfo(tz)).date()

    with patch.object(requests.Session, "request", new=fake_request):
        client.list_usage_logs(
            start_date=today, end_date=today, timezone_name=tz, page_size=4
        )
        # The entire feed is replaced (not append-only): the cached watermark is gone
        # and more than CURRENT_DAY_INCREMENTAL_MAX_PAGES of data hides it.
        state["ids"] = list(range(100, 79, -1))
        result = client.list_usage_logs(
            start_date=today, end_date=today, timezone_name=tz, page_size=4
        )

    # The incremental attempt gives up and the full wave crawl returns the truth.
    assert [item["id"] for item in result["items"]] == list(range(100, 79, -1))
    assert result["total"] == 21


def test_sub2api_client_creates_user_api_key_without_forwarding_group_override() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "json": json})
        return FakeResponse(
            200,
            {
                "code": 0,
                "message": "success",
                "data": {
                    "api_key": {
                        "id": "key-1",
                        "key": "sk-created",
                        "name": json["name"],
                        "user_id": 2,
                        "group_id": json["group_id"],
                    }
                },
            },
        )

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.create_user_api_key(
            user_id=2,
            name="svc:prod:obj:v1:user@example.com",
            group_id=22,
            options={"quota": 300, "group_id": 11, "user_id": 1},
        )

    assert result["key"] == "sk-created"
    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/admin/users/2/api-keys",
            "json": {
                "quota": 300,
                "name": "svc:prod:obj:v1:user@example.com",
                "group_id": 22,
            },
        }
    ]


def test_sub2api_client_replace_group_sends_numeric_group_ids_as_numbers() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "json": json})
        return FakeResponse(200, {"code": 0, "message": "success", "data": {"migrated_keys": 1}})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.replace_exclusive_user_group(
            user_id=3,
            old_group_id=15,
            new_group_id="7",
        )

    assert result["migrated_keys"] == 1
    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/admin/users/3/replace-group",
            "json": {"old_group_id": 15, "new_group_id": 7},
        }
    ]


def test_sub2api_client_add_user_allowed_group_keeps_existing_authorizations() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "json": json})
        if method == "GET":
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "id": 3,
                        "email": "keeper@example.com",
                        "allowed_groups": [15, 9],
                    },
                },
            )
        return FakeResponse(200, {"code": 0, "message": "success", "data": {"ok": True}})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.add_user_allowed_group(user_id=3, group_id="7")

    assert result["user_id"] == 3
    assert result["group_id"] == "7"
    assert result["allowed_groups"] == [15, 9, 7]
    # allowed_groups is a declarative overwrite that migrates no keys, so the
    # write has to carry the groups the user already had; and the endpoint has no
    # top-level group_id field to send.
    assert calls == [
        {"method": "GET", "path": "/api/v1/admin/users/3", "json": None},
        {
            "method": "PUT",
            "path": "/api/v1/admin/users/3",
            "json": {"allowed_groups": [15, 9, 7]},
        },
    ]


def test_sub2api_client_add_user_allowed_group_is_idempotent() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "json": json})
        if method == "GET":
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {"id": 3, "email": "member@example.com", "allowed_groups": [7]},
                },
            )
        return FakeResponse(200, {"code": 0, "message": "success", "data": {"ok": True}})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.add_user_allowed_group(user_id=3, group_id=7)

    assert result["allowed_groups"] == [7]
    assert calls[1]["json"] == {"allowed_groups": [7]}


def test_sub2api_client_bind_account_to_group_unions_existing_bindings() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "json": json})
        if method == "GET":
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "id": 42,
                        "name": "shared-account",
                        "platform": "openai",
                        "group_ids": [11, 33],
                    },
                },
            )
        return FakeResponse(200, {"code": 0, "message": "success", "data": {"ok": True}})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.bind_account_to_group(42, "22")

    assert result["group_ids"] == [11, 33, 22]
    # Upstream has no POST /admin/groups/{id}/accounts route, and the account PUT
    # replaces the whole binding list, so the bind reads first and sends a union.
    assert calls == [
        {"method": "GET", "path": "/api/v1/admin/accounts/42", "json": None},
        {
            "method": "PUT",
            "path": "/api/v1/admin/accounts/42",
            "json": {"group_ids": [11, 33, 22], "confirm_mixed_channel_risk": True},
        },
    ]


def test_sub2api_client_create_group_uses_upstream_group_form_payload() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "json": json})
        return FakeResponse(200, {"code": 0, "message": "success", "data": {"id": 123}})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.create_group("provision-user-example-com")

    payload = calls[0]["json"]
    assert result["id"] == 123
    assert calls[0]["path"] == "/api/v1/admin/groups"
    assert payload["name"] == "provision-user-example-com"
    assert payload["platform"] == "openai"
    assert payload["is_exclusive"] is True
    assert payload["subscription_type"] == "standard"
    assert payload["daily_limit_usd"] is None
    assert payload["weekly_limit_usd"] is None
    assert payload["monthly_limit_usd"] is None
    assert payload["allow_image_generation"] is True
    assert payload["image_rate_independent"] is True
    assert payload["image_rate_multiplier"] == 1
    assert payload["image_price_1k"] is None
    assert payload["image_price_2k"] is None
    assert payload["image_price_4k"] is None
    assert payload["require_oauth_only"] is False
    assert payload["messages_dispatch_model_config"] == {
        "opus_mapped_model": "gpt-5.4",
        "sonnet_mapped_model": "gpt-5.3-codex",
        "haiku_mapped_model": "gpt-5.4-mini",
        "exact_model_mappings": {},
    }
    # The model mapping fields exist only inside messages_dispatch_model_config;
    # upstream's group request struct has no top-level counterparts left.
    for dead_field in (
        "opus_mapped_model",
        "sonnet_mapped_model",
        "haiku_mapped_model",
        "exact_model_mappings",
    ):
        assert dead_field not in payload


def test_sub2api_client_create_group_honours_explicit_platform() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "json": json})
        return FakeResponse(200, {"code": 0, "message": "success", "data": {"id": 321}})

    # The configured default is openai; the explicit argument must win so the
    # caller's already-resolved platform is what the group is created on.
    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(group_platform="openai"),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.create_group("user@example.com_grok", platform="grok")

    payload = calls[0]["json"]
    assert result["id"] == 321
    assert payload["name"] == "user@example.com_grok"
    assert payload["platform"] == "grok"
    # The nested dispatch mapping is openai-only, and it keys off the platform
    # actually in effect rather than the configured default.
    assert "messages_dispatch_model_config" not in payload


def test_sub2api_client_create_group_explicit_openai_adds_dispatch_config() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "json": json})
        return FakeResponse(200, {"code": 0, "message": "success", "data": {"id": 322}})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(group_platform="grok"),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        client.create_group("user@example.com_openai", platform="openai")

    payload = calls[0]["json"]
    assert payload["platform"] == "openai"
    assert payload["messages_dispatch_model_config"] == {
        "opus_mapped_model": "gpt-5.4",
        "sonnet_mapped_model": "gpt-5.3-codex",
        "haiku_mapped_model": "gpt-5.4-mini",
        "exact_model_mappings": {},
    }


def test_sub2api_client_create_group_falls_back_to_configured_platform() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "json": json})
        return FakeResponse(200, {"code": 0, "message": "success", "data": {"id": 323}})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(group_platform="grok"),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        client.create_group("legacy-caller-group")

    payload = calls[0]["json"]
    assert payload["platform"] == "grok"
    assert "messages_dispatch_model_config" not in payload


def test_sub2api_client_create_group_scopes_supported_model_scopes_to_openai() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "json": json})
        return FakeResponse(200, {"code": 0, "message": "success", "data": {"id": 324}})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        client.create_group("user@example.com_openai", platform="openai")
        client.create_group("user@example.com_grok", platform="grok")

    # Live grok groups read back `supported_model_scopes: []`, so the openai scope
    # list must not travel with a grok group creation.
    assert calls[0]["json"]["supported_model_scopes"] == ["claude", "gemini_text", "gemini_image"]
    assert "supported_model_scopes" not in calls[1]["json"]


def test_sub2api_client_supported_oauth_platforms_is_endpoints_and_credentials() -> None:
    # The public capability list: a platform needs both an endpoint pair and a
    # credential white-list before it can be offered.
    assert Sub2APIClient.supported_oauth_platforms() == ["grok", "openai"]


def test_sub2api_grok_oauth_requests_use_upstream_grok_paths() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        calls.append({"method": method, "path": path, "json": json})
        if path == "/api/v1/admin/grok/oauth/auth-url":
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "auth_url": "https://accounts.x.ai/authorize?state=grok-state",
                        "session_id": "grok-session-1",
                        "state": "grok-state",
                    },
                },
            )
        if path == "/api/v1/admin/grok/oauth/exchange-code":
            return FakeResponse(
                200,
                {"code": 0, "message": "success", "data": {"access_token": "grok-token"}},
            )
        return FakeResponse(404, {"detail": "not found"})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        oauth = client.generate_oauth_auth_url(
            email="user@example.com", state="sidecar-state", platform="grok"
        )
        client.exchange_oauth_code(
            code="grok-code",
            state=oauth["state"],
            session_id=oauth["session_id"],
            platform="grok",
        )

    assert oauth["state"] == "grok-state"
    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/admin/grok/oauth/auth-url",
            # grok mints its own state and session; a sidecar-supplied state would be
            # an unknown field on this endpoint.
            "json": {},
        },
        {
            "method": "POST",
            "path": "/api/v1/admin/grok/oauth/exchange-code",
            "json": {
                "session_id": "grok-session-1",
                "code": "grok-code",
                "state": "grok-state",
            },
        },
    ]


def test_sub2api_oauth_requests_reject_platform_without_endpoints() -> None:
    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with pytest.raises(Sub2APIError) as excinfo:
        client.generate_oauth_auth_url(
            email="user@example.com", state="state-1", platform="anthropic"
        )

    message = str(excinfo.value)
    assert "anthropic" in message
    assert "grok" in message and "openai" in message


def test_sub2api_client_builds_grok_oauth_account_payload() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "json": json})
        return FakeResponse(200, {"account_id": "grok-acct-1", "name": json["name"]})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        client.create_account_from_oauth(
            "user@example.com",
            GROK_EXCHANGE_PAYLOAD,
            "g-9",
            platform="grok",
        )

    payload = calls[0]["json"]
    assert calls[0]["path"] == "/api/v1/admin/accounts"
    # Upstream has no `provider` field on the account API at all.
    assert "provider" not in payload
    assert payload["platform"] == "grok"
    assert payload["type"] == "oauth"
    assert payload["credentials"] == EXPECTED_GROK_CREDENTIALS
    assert payload["extra"] == EXPECTED_GROK_EXTRA
    # The knobs the provisioning template owns reach grok too — upstream stores them
    # on any platform, and the hand-configured grok accounts carry all three.
    assert payload["credentials"]["temp_unschedulable_enabled"] is True
    assert payload["credentials"]["temp_unschedulable_rules"] == (
        EXPECTED_TEMPORARY_UNSCHEDULABLE_RULES
    )
    assert payload["credentials"]["model_mapping"] == EXPECTED_GROK_MODEL_MAPPING
    assert payload["credentials"]["base_url"] == "https://cli-chat-proxy.grok.com/v1"
    assert payload["extra"]["grok_client_tool_cache_enabled"] is True
    # ...but the openai responses-transport keys stay behind: grok's template clears
    # the ws mode, and live grok accounts carry no such keys.
    assert "openai_oauth_responses_websockets_v2_mode" not in payload["extra"]
    assert "openai_oauth_responses_websockets_v2_enabled" not in payload["extra"]
    # Account-level knobs match the live grok accounts (concurrency comes from the
    # base template, not from a grok override).
    assert payload["priority"] == 1
    assert payload["rate_multiplier"] == 1
    assert payload["concurrency"] == 5


def test_sub2api_client_grok_oauth_credentials_skip_unknown_exchange_fields() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"json": json})
        return FakeResponse(200, {"account_id": "grok-acct-2", "name": json["name"]})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        client.create_account_from_oauth(
            "sparse@example.com",
            {
                "access_token": "grok-access",
                "refresh_token": "",
                "chatgpt_account_id": "not-a-grok-field",
                "surprise_field": {"nested": True},
            },
            "g-9",
            platform="grok",
        )

    # Credentials are stored verbatim upstream, so only white-listed keys with an
    # actual value are forwarded; everything else is dropped rather than persisted.
    # What the template stamps is independent of the exchange and always present.
    assert calls[0]["json"]["credentials"] == {
        "access_token": "grok-access",
        "temp_unschedulable_enabled": True,
        "temp_unschedulable_rules": EXPECTED_TEMPORARY_UNSCHEDULABLE_RULES,
        "model_mapping": EXPECTED_GROK_MODEL_MAPPING,
        "base_url": "https://cli-chat-proxy.grok.com/v1",
    }


def test_sub2api_client_apikey_credentials_default_base_url_per_platform() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"json": json})
        return FakeResponse(200, {"account_id": "acct-1", "name": json["name"]})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        client.create_account_from_apikey(
            name="grok-key", base_url="", api_key="xai-1", group_id="g-1", platform="grok"
        )
        client.create_account_from_apikey(
            name="grok-key-explicit",
            base_url="https://grok-proxy.internal/v1",
            api_key="xai-2",
            group_id="g-1",
            platform="grok",
        )
        client.create_account_from_apikey(
            name="openai-key", base_url="", api_key="sk-1", group_id="g-1", platform="openai"
        )

    assert calls[0]["json"]["credentials"]["base_url"] == "https://api.x.ai/v1"
    # An explicit base URL still wins over the platform default.
    assert calls[1]["json"]["credentials"]["base_url"] == "https://grok-proxy.internal/v1"
    # openai keeps its historical behavior: no base_url key, upstream defaults it.
    assert "base_url" not in calls[2]["json"]["credentials"]


def test_sub2api_client_reads_null_scheduled_test_plan_list_as_empty() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        calls.append({"method": method, "path": path, "json": json})
        if method == "GET":
            # This is what upstream really answers for an account with no plans.
            return FakeResponse(200, {"code": 0, "message": "success", "data": None})
        return FakeResponse(200, {"code": 0, "message": "success", "data": {"id": 601}})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        assert client.list_scheduled_test_plans("77") == []
        result = client.ensure_default_scheduled_test_plan(account_id="77")

    # "no plans" must read as an empty list, not as a parse failure — the latter made
    # every freshly provisioned account log a warning and record a failed event.
    assert result["created"] is True
    assert calls[-1]["path"] == "/api/v1/admin/scheduled-test-plans"


def test_sub2api_openai_oauth_requests_use_upstream_openai_paths() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "json": json})
        if urlparse(url).path == "/api/v1/admin/openai/generate-auth-url":
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "auth_url": "https://auth.example.com/authorize?state=upstream-state",
                        "session_id": "session-1",
                    },
                },
            )
        if urlparse(url).path == "/api/v1/admin/openai/exchange-code":
            assert json["session_id"] == "session-1"
            assert json["state"] == "upstream-state"
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {"access_token": "token-123"},
                },
            )
        return FakeResponse(404, {"detail": "not found"})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        oauth = client.generate_oauth_auth_url(email="user@example.com", state="state-1")
        client.exchange_oauth_code(
            code="code-1",
            state=oauth["state"],
            session_id=oauth["session_id"],
        )

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/admin/openai/generate-auth-url",
            "json": {"state": "state-1"},
        },
        {
            "method": "POST",
            "path": "/api/v1/admin/openai/exchange-code",
            "json": {
                "code": "code-1",
                "state": "upstream-state",
                "session_id": "session-1",
            },
        },
    ]


def test_sub2api_client_creates_default_scheduled_test_plan_when_missing() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        calls.append({"method": method, "path": path, "json": json})
        if method == "GET":
            return FakeResponse(200, {"code": 0, "message": "success", "data": []})
        return FakeResponse(
            200,
            {
                "code": 0,
                "message": "success",
                "data": {"id": 501, **dict(json or {})},
            },
        )

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.ensure_default_scheduled_test_plan(account_id="123")

    assert result["created"] is True
    assert calls == [
        {
            "method": "GET",
            "path": "/api/v1/admin/accounts/123/scheduled-test-plans",
            "json": None,
        },
        {
            "method": "POST",
            "path": "/api/v1/admin/scheduled-test-plans",
            "json": {"account_id": 123, **EXPECTED_DEFAULT_SCHEDULED_TEST_PLAN},
        },
    ]


def test_sub2api_client_reuses_matching_default_scheduled_test_plan() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "json": json})
        return FakeResponse(
            200,
            {
                "code": 0,
                "message": "success",
                "data": [{"id": 501, **EXPECTED_DEFAULT_SCHEDULED_TEST_PLAN}],
            },
        )

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.ensure_default_scheduled_test_plan(account_id=123)

    assert result["created"] is False
    assert calls == [
        {
            "method": "GET",
            "path": "/api/v1/admin/accounts/123/scheduled-test-plans",
            "json": None,
        }
    ]


def test_sub2api_client_configures_existing_oauth_account_preserving_credentials() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "json": json})
        return FakeResponse(
            200,
            {"code": 0, "message": "success", "data": {"account_id": "acct-existing"}},
        )

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )
    account = {
        "id": "acct-existing",
        "name": "old@example.com",
        "group_ids": [11, 33],
        "raw": {
            "credentials": {
                "access_token": "keep-access",
                "refresh_token": "keep-refresh",
                "id_token": "keep-id",
            },
            "extra": {"privacy_mode": "standard", "legacy": "value"},
            "notes": "existing note",
            "proxy_id": "proxy-1",
            "priority": 9,
            "rate_multiplier": 2,
        },
    }

    with patch.object(requests.Session, "request", new=fake_request):
        result = client.configure_existing_oauth_account(
            account=account,
            name="existing@example.com",
            group_id=77,
        )

    payload = calls[0]["json"]
    assert result["id"] == "acct-existing"
    assert calls[0]["method"] == "PUT"
    assert calls[0]["path"] == "/api/v1/admin/accounts/acct-existing"
    assert payload["name"] == "existing@example.com"
    assert "provider" not in payload
    assert payload["platform"] == "openai"
    assert payload["type"] == "oauth"
    # group_ids replaces the whole binding list upstream, so re-configuring an
    # account must not drop the groups it already serves.
    assert payload["group_ids"] == [11, 33, 77]
    assert payload["confirm_mixed_channel_risk"] is True
    assert payload["concurrency"] == 5
    assert payload["credentials"]["access_token"] == "keep-access"
    assert payload["credentials"]["refresh_token"] == "keep-refresh"
    assert payload["credentials"]["id_token"] == "keep-id"
    assert payload["credentials"]["temp_unschedulable_enabled"] is True
    assert payload["credentials"]["temp_unschedulable_rules"] == (
        EXPECTED_TEMPORARY_UNSCHEDULABLE_RULES
    )
    assert payload["credentials"]["model_mapping"] == EXPECTED_MODEL_WHITELIST_MAPPING
    assert payload["extra"]["privacy_mode"] == "standard"
    assert payload["extra"]["legacy"] == "value"
    assert payload["extra"]["openai_oauth_responses_websockets_v2_mode"] == "context_pool"
    assert payload["extra"]["openai_oauth_responses_websockets_v2_enabled"] is True
    assert payload["notes"] == "existing note"
    assert payload["proxy_id"] == "proxy-1"
    assert payload["priority"] == 9
    assert payload["rate_multiplier"] == 2


def test_sub2api_client_sends_no_model_mapping_for_an_unconfigured_platform() -> None:
    """A platform with no template of its own must not inherit openai's models."""
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"json": json})
        return FakeResponse(200, {"account_id": "acct-anthropic"})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        client.create_account_from_apikey(
            name="anthropic-key",
            base_url="https://api.anthropic.com/v1",
            api_key="sk-ant-1",
            group_id="g-1",
            platform="anthropic",
        )

    credentials = calls[0]["json"]["credentials"]
    # openai's gpt-5.x mapping on an anthropic account would misconfigure it, and
    # nothing downstream would flag it — upstream simply stores what it is sent.
    assert "model_mapping" not in credentials
    assert credentials["base_url"] == "https://api.anthropic.com/v1"
    # The cross-platform half of the template still applies.
    assert credentials["temp_unschedulable_enabled"] is True
    assert credentials["temp_unschedulable_rules"] == EXPECTED_TEMPORARY_UNSCHEDULABLE_RULES
    assert calls[0]["json"]["concurrency"] == 5


def test_sub2api_client_sends_a_model_mapping_a_platform_opted_into() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"json": json})
        return FakeResponse(200, {"account_id": "acct-anthropic"})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(
            per_platform={"anthropic": {"account_model_whitelist": ("claude-4",)}}
        ),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        client.create_account_from_apikey(
            name="anthropic-key",
            base_url="https://api.anthropic.com/v1",
            api_key="sk-ant-1",
            group_id="g-1",
            platform="anthropic",
        )

    assert calls[0]["json"]["credentials"]["model_mapping"] == {"claude-4": "claude-4"}


def test_sub2api_client_sends_no_websockets_extras_for_an_unconfigured_platform() -> None:
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"json": json})
        return FakeResponse(200, {"account_id": "acct-anthropic"})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )
    account = {
        "id": "acct-anthropic",
        "name": "old@example.com",
        "group_ids": [11],
        "raw": {"credentials": {"access_token": "keep"}, "extra": {"legacy": "value"}},
    }

    with patch.object(requests.Session, "request", new=fake_request):
        client.configure_existing_oauth_account(
            account=account,
            name="existing@example.com",
            group_id=77,
            platform="anthropic",
        )

    payload = calls[0]["json"]
    # `openai_oauth_responses_websockets_v2_*` is openai's responses transport.
    assert "openai_oauth_responses_websockets_v2_mode" not in payload["extra"]
    assert "openai_oauth_responses_websockets_v2_enabled" not in payload["extra"]
    assert "model_mapping" not in payload["credentials"]
    assert payload["extra"]["legacy"] == "value"
    assert payload["credentials"]["temp_unschedulable_enabled"] is True


def test_sub2api_client_repairs_an_existing_grok_account_onto_the_template() -> None:
    """Re-configuring an account re-applies the template, drift included."""
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append({"json": json})
        return FakeResponse(200, {"account_id": "acct-grok-existing"})

    client = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )
    account = {
        "id": "acct-grok-existing",
        "name": "old@example.com",
        "group_ids": [11],
        "raw": {
            "credentials": {
                "access_token": "keep-access",
                # Hand-configured accounts exist with the API-key host on an OAuth
                # account; the template is what puts them back on the proxy host.
                "base_url": "https://api.x.ai/v1",
            },
            "extra": {"email": "old@example.com"},
        },
    }

    with patch.object(requests.Session, "request", new=fake_request):
        client.configure_existing_oauth_account(
            account=account,
            name="existing@example.com",
            group_id=77,
            platform="grok",
        )

    payload = calls[0]["json"]
    assert payload["credentials"]["access_token"] == "keep-access"
    assert payload["credentials"]["base_url"] == "https://cli-chat-proxy.grok.com/v1"
    assert payload["credentials"]["model_mapping"] == EXPECTED_GROK_MODEL_MAPPING
    assert payload["credentials"]["temp_unschedulable_rules"] == (
        EXPECTED_TEMPORARY_UNSCHEDULABLE_RULES
    )
    assert payload["extra"]["grok_client_tool_cache_enabled"] is True
    assert "openai_oauth_responses_websockets_v2_mode" not in payload["extra"]


def login(client: TestClient) -> dict[str, object]:
    response = client.post("/auth/login", json=AUTH_PAYLOAD)
    assert response.status_code == 200
    return response.json()


def test_root_redirects_to_login_when_unauthenticated(client) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_serves_react_shell(client) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert 'id="root"' in response.text


@pytest.mark.parametrize("path", ["/orchestration/manual", "/provision", "/notifications", "/credit-control"])
def test_operator_pages_redirect_to_login_when_unauthenticated(client, path: str) -> None:
    response = client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next={path}"


def test_base_path_redirects_and_cookie_scope(client, monkeypatch) -> None:
    monkeypatch.setenv("APP_BASE_PATH", "/sidecar")
    clear_caches()

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/sidecar/login"

    response = client.get("/orchestration/manual", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/sidecar/login?next=/sidecar/orchestration/manual"

    response = client.post("/auth/login", json=AUTH_PAYLOAD)

    assert response.status_code == 200
    assert "Path=/sidecar" in response.headers["set-cookie"]

    clear_caches()


def test_base_path_login_next_accepts_prefixed_path(client, monkeypatch) -> None:
    monkeypatch.setenv("APP_BASE_PATH", "/sidecar")
    clear_caches()
    login_response = client.post("/auth/login", json=AUTH_PAYLOAD)
    access_key = login_response.json()["access_key"]

    response = client.get(
        "/login?next=/sidecar/provision",
        headers={"Authorization": f"Bearer {access_key}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/sidecar/provision"

    clear_caches()


def test_base_path_rewrites_react_shell_asset_urls(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_BASE_PATH", "/sidecar")
    ui_index = tmp_path / "index.html"
    ui_index.write_text(
        """
<!doctype html>
<html>
  <head>
    <script type="module" src="/ui-static/assets/index.js"></script>
    <link rel="stylesheet" href="/ui-static/assets/index.css">
  </head>
  <body><div id="root"></div></body>
</html>
        """.strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "UI_INDEX_FILE", ui_index)
    clear_caches()

    response = client.get("/login")

    assert response.status_code == 200
    assert 'window.__SUB2API_SIDECAR_BASE_PATH__ = "/sidecar";' in response.text
    assert 'src="/sidecar/ui-static/assets/index.js"' in response.text
    assert 'href="/sidecar/ui-static/assets/index.css"' in response.text
    assert 'src="/ui-static/' not in response.text
    assert 'href="/ui-static/' not in response.text

    clear_caches()


@pytest.mark.parametrize("path", ["/health", "/ping"])
def test_probe_endpoints_return_ok_without_auth(client, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_login_returns_access_key_and_sets_cookie(client) -> None:
    response = client.post("/auth/login", json=AUTH_PAYLOAD)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["username"] == "admin"
    assert payload["access_key"]
    assert response.cookies.get(ACCESS_KEY_COOKIE_NAME) == payload["access_key"]


def test_auth_session_requires_login(client) -> None:
    response = client.get("/auth/session")

    assert response.status_code == 401


def test_auth_session_returns_current_session(client) -> None:
    login_response = client.post("/auth/login", json=AUTH_PAYLOAD)
    access_key = login_response.json()["access_key"]

    response = client.get("/auth/session", headers={"Authorization": f"Bearer {access_key}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["username"] == "admin"
    assert payload["expires_at"]


def test_upstreams_endpoint_returns_sanitized_config(client, tmp_path, monkeypatch, app_env) -> None:
    config_path = tmp_path / "multi-upstream.yaml"
    config_path.write_text(
        f"""
{database_config_from_app_env(app_env)}
app:
  base_url: http://testserver
openai:
  oauth_redirect_uri: http://localhost:1455/callback
sub2api:
  upstreams:
    - id: main
      name: Main Sub2API
      base_url: http://main-sub2api.local
      admin_api_key_env: SUB2API_ADMIN_API_KEY
    - id: secondary
      name: Secondary Sub2API
      base_url: http://secondary-sub2api.local
      admin_api_key_env: SUB2API_SECONDARY_ADMIN_API_KEY
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("SUB2API_SECONDARY_ADMIN_API_KEY", "secondary-secret")
    clear_caches()
    login(client)

    response = client.get("/api/upstreams")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_upstream_id"] == "main"
    assert payload["items"] == [
        {
            "upstream_id": "main",
            "name": "Main Sub2API",
            "base_url": "http://main-sub2api.local",
            "is_default": True,
        },
        {
            "upstream_id": "secondary",
            "name": "Secondary Sub2API",
            "base_url": "http://secondary-sub2api.local",
            "is_default": False,
        },
    ]
    assert "secret" not in json.dumps(payload)
    assert "admin_api_key" not in json.dumps(payload)

    monkeypatch.setenv("CONFIG_PATH", "__missing_test_config__.yaml")
    clear_caches()


def test_orchestration_discovery_uses_selected_upstream(client, tmp_path, monkeypatch, app_env) -> None:
    config_path = tmp_path / "multi-upstream.yaml"
    config_path.write_text(
        f"""
{database_config_from_app_env(app_env)}
app:
  base_url: http://testserver
openai:
  oauth_redirect_uri: http://localhost:1455/callback
sub2api:
  upstreams:
    - id: main
      name: Main Sub2API
      base_url: http://main-sub2api.local
      admin_api_key_env: SUB2API_ADMIN_API_KEY
    - id: secondary
      name: Secondary Sub2API
      base_url: http://secondary-sub2api.local
      admin_api_key_env: SUB2API_SECONDARY_ADMIN_API_KEY
      request_timeout_seconds: 18
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("SUB2API_SECONDARY_ADMIN_API_KEY", "secondary-key")
    clear_caches()
    login(client)
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        calls.append(
            {
                "method": method,
                "host": urlparse(url).netloc,
                "path": urlparse(url).path,
                "api_key": self.headers.get("x-api-key"),
                "timeout": timeout,
            }
        )
        return FakeResponse(
            200,
            {
                "code": 0,
                "message": "success",
                "data": [
                    {
                        "id": 77,
                        "email": "secondary@example.com",
                        "name": "Secondary User",
                        "group_id": 5,
                        "group_name": "secondary-group",
                    }
                ],
            },
        )

    with patch.object(requests.Session, "request", new=fake_request):
        response = client.get("/orchestration/users?upstream_id=secondary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["upstream_id"] == "secondary"
    assert payload["items"][0]["upstream_id"] == "secondary"
    assert payload["items"][0]["email"] == "secondary@example.com"
    # The users view resolves each user's per-platform group, which needs the
    # selected upstream's group list too (served from the operational snapshot
    # once one exists).
    assert calls == [
        {
            "method": "GET",
            "host": "secondary-sub2api.local",
            "path": "/api/v1/admin/users",
            "api_key": "secondary-key",
            "timeout": 18,
        },
        {
            "method": "GET",
            "host": "secondary-sub2api.local",
            "path": "/api/v1/admin/groups/all",
            "api_key": "secondary-key",
            "timeout": 18,
        },
    ]

    missing_response = client.get("/orchestration/users?upstream_id=missing")
    assert missing_response.status_code == 422
    assert "Unknown Sub2API upstream_id: missing" in missing_response.json()["detail"]
    assert len(calls) == 2

    monkeypatch.setenv("CONFIG_PATH", "__missing_test_config__.yaml")
    clear_caches()


def test_provisioning_flow_uses_selected_upstream_for_start_and_complete(client, tmp_path, monkeypatch, app_env) -> None:
    config_path = tmp_path / "multi-upstream.yaml"
    config_path.write_text(
        f"""
{database_config_from_app_env(app_env)}
app:
  base_url: http://testserver
openai:
  oauth_redirect_uri: http://localhost:1455/callback
sub2api:
  upstreams:
    - id: main
      name: Main Sub2API
      base_url: http://main-sub2api.local
      admin_api_key_env: SUB2API_ADMIN_API_KEY
    - id: secondary
      name: Secondary Sub2API
      base_url: http://secondary-sub2api.local
      admin_api_key_env: SUB2API_SECONDARY_ADMIN_API_KEY
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("SUB2API_SECONDARY_ADMIN_API_KEY", "secondary-key")
    clear_caches()
    login(client)
    calls: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        calls.append(
            {
                "method": method,
                "host": urlparse(url).netloc,
                "path": path,
                "api_key": self.headers.get("x-api-key"),
            }
        )
        if method == "GET" and path in {"/api/v1/admin/groups/all", "/api/v1/admin/accounts"}:
            return FakeResponse(200, {"code": 0, "message": "success", "data": []})
        if (
            method == "GET"
            and path.startswith("/api/v1/admin/accounts/")
            and path.endswith("/scheduled-test-plans")
        ):
            return FakeResponse(200, {"code": 0, "message": "success", "data": []})
        if method == "POST" and path == "/api/v1/admin/groups":
            return FakeResponse(200, {"code": 0, "message": "success", "data": {"id": "secondary-group"}})
        if method == "POST" and path == "/api/v1/admin/openai/generate-auth-url":
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "auth_url": f"https://auth.example.com/authorize?state=secondary-{json['state']}",
                        "session_id": f"session-{json['state']}",
                    },
                },
            )
        if method == "POST" and path == "/api/v1/admin/openai/exchange-code":
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "access_token": "token-123",
                        "refresh_token": "refresh-123",
                        "provider_user_id": "provider-1",
                    },
                },
            )
        if method == "POST" and path == "/api/v1/admin/accounts":
            return FakeResponse(
                200,
                {"code": 0, "message": "success", "data": {"id": "secondary-account", "name": json["name"]}},
            )
        if method == "POST" and path == "/api/v1/admin/scheduled-test-plans":
            assert json == {
                "account_id": "secondary-account",
                **EXPECTED_DEFAULT_SCHEDULED_TEST_PLAN,
            }
            return FakeResponse(200, {"code": 0, "message": "success", "data": {"id": "stp-secondary", **json}})
        if method == "POST" and path == "/api/v1/admin/groups/secondary-group/accounts":
            return FakeResponse(200, {"code": 0, "message": "success", "data": {"ok": True}})
        return FakeResponse(404, {"detail": f"unexpected {method} {path}"})

    with patch.object(requests.Session, "request", new=fake_request):
        start_response = client.post(
            "/provision/start",
            json={"email": "secondary@example.com", "upstream_id": "secondary"},
        )
        state = parse_qs(urlparse(start_response.json()["oauth_url"]).query)["state"][0]
        complete_response = client.post(
            "/provision/oauth/complete",
            json={"callback_url": f"http://localhost:1455/callback?code=mock-code&state={state}"},
        )

    assert start_response.status_code == 200
    assert start_response.json()["upstream_id"] == "secondary"
    stored_flow = main.get_flow_store().get_by_flow_id(start_response.json()["flow_id"])
    assert stored_flow is not None
    assert stored_flow.upstream_id == "secondary"
    assert complete_response.status_code == 200
    assert complete_response.json()["upstream_id"] == "secondary"
    assert complete_response.json()["oauth_account_id"] == "secondary-account"
    assert calls
    assert {call["host"] for call in calls} == {"secondary-sub2api.local"}
    assert {call["api_key"] for call in calls} == {"secondary-key"}

    monkeypatch.setenv("CONFIG_PATH", "__missing_test_config__.yaml")
    clear_caches()


def test_secondary_upstream_provisioning_ignores_default_landing_pool(client, tmp_path, monkeypatch, app_env) -> None:
    config_path = tmp_path / "multi-upstream.yaml"
    config_path.write_text(
        f"""
{database_config_from_app_env(app_env)}
app:
  base_url: http://testserver
openai:
  oauth_redirect_uri: http://localhost:1455/callback
sub2api:
  upstreams:
    - id: main
      name: Main Sub2API
      base_url: http://main-sub2api.local
      admin_api_key_env: SUB2API_ADMIN_API_KEY
    - id: secondary
      name: Secondary Sub2API
      base_url: http://secondary-sub2api.local
      admin_api_key_env: SUB2API_SECONDARY_ADMIN_API_KEY
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("SUB2API_SECONDARY_ADMIN_API_KEY", "secondary-key")
    clear_caches()
    login(client)
    main.get_flow_store().upsert_rotation_pool_group(
        RotationPoolGroup(
            group_id=11,
            pool_kind=RotationPoolKind.landing,
            group_name="main-landing",
            platform="openai",
            status="active",
            is_exclusive=True,
            priority=0,
        )
    )
    create_group_payloads: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        if method == "GET" and path in {"/api/v1/admin/groups/all", "/api/v1/admin/accounts"}:
            return FakeResponse(200, {"code": 0, "message": "success", "data": []})
        if method == "POST" and path == "/api/v1/admin/groups":
            create_group_payloads.append(dict(json or {}))
            return FakeResponse(200, {"code": 0, "message": "success", "data": {"id": 77, "name": json["name"]}})
        if method == "POST" and path == "/api/v1/admin/openai/generate-auth-url":
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "auth_url": f"https://auth.example.com/authorize?state=secondary-{json['state']}",
                        "session_id": f"session-{json['state']}",
                    },
                },
            )
        return FakeResponse(404, {"detail": f"unexpected {method} {path}"})

    with patch.object(requests.Session, "request", new=fake_request):
        response = client.post(
            "/provision/start",
            json={"email": "secondary-new@example.com", "upstream_id": "secondary"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["upstream_id"] == "secondary"
    assert payload["group_id"] == 77
    assert payload["assignment_mode"] == "dedicated"
    assert payload["assignment_reason"] == "dedicated provisioning group"
    assert create_group_payloads[0]["name"] == "secondary-new@example.com_openai"

    monkeypatch.setenv("CONFIG_PATH", "__missing_test_config__.yaml")
    clear_caches()


def test_sub2api_login_exchanges_admin_jwt_for_sidecar_session(client) -> None:
    calls: list[dict[str, object]] = []

    def fake_request(method: str, url: str, headers=None, timeout=None):
        calls.append({"method": method, "path": urlparse(url).path, "headers": headers})
        return FakeResponse(
            200,
            {
                "code": 0,
                "message": "success",
                "data": {
                    "id": 1,
                    "email": "admin@example.com",
                    "username": "admin-user",
                    "role": "admin",
                },
            },
        )

    with patch.object(requests, "request", new=fake_request):
        response = client.post("/auth/sub2api-login", json={"token": "jwt-123"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["username"] == "admin-user"
    assert payload["access_key"]
    assert response.cookies.get(ACCESS_KEY_COOKIE_NAME) == payload["access_key"]
    assert calls == [
        {
            "method": "GET",
            "path": "/api/v1/auth/me",
            "headers": {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer jwt-123",
            },
        }
    ]


def test_sub2api_login_uses_default_upstream_only(client, tmp_path, monkeypatch, app_env) -> None:
    config_path = tmp_path / "multi-upstream-login.yaml"
    config_path.write_text(
        f"""
{database_config_from_app_env(app_env)}
app:
  base_url: http://testserver
openai:
  oauth_redirect_uri: http://localhost:1455/callback
sub2api:
  upstreams:
    - id: main
      name: Main Sub2API
      base_url: http://main-sub2api.local
      admin_api_key_env: SUB2API_ADMIN_API_KEY
    - id: secondary
      name: Secondary Sub2API
      base_url: http://secondary-sub2api.local
      admin_api_key_env: SUB2API_SECONDARY_ADMIN_API_KEY
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("SUB2API_SECONDARY_ADMIN_API_KEY", "secondary-secret")
    clear_caches()
    calls: list[str] = []

    def fake_request(method: str, url: str, headers=None, timeout=None):
        calls.append(url)
        return FakeResponse(
            200,
            {
                "code": 0,
                "message": "success",
                "data": {
                    "id": 1,
                    "email": "admin@example.com",
                    "username": "admin-user",
                    "role": "admin",
                },
            },
        )

    with patch.object(requests, "request", new=fake_request):
        response = client.post("/auth/sub2api-login", json={"token": "jwt-123"})

    assert response.status_code == 200
    assert calls == ["http://main-sub2api.local/api/v1/auth/me"]

    monkeypatch.setenv("CONFIG_PATH", "__missing_test_config__.yaml")
    clear_caches()


def test_sub2api_login_rejects_non_admin_jwt(client) -> None:
    def fake_request(method: str, url: str, headers=None, timeout=None):
        return FakeResponse(
            200,
            {
                "code": 0,
                "message": "success",
                "data": {
                    "id": 2,
                    "email": "user@example.com",
                    "role": "user",
                },
            },
        )

    with patch.object(requests, "request", new=fake_request):
        response = client.post("/auth/sub2api-login", json={"token": "jwt-123"})

    assert response.status_code == 403
    assert response.cookies.get(ACCESS_KEY_COOKIE_NAME) is None
    assert response.json()["detail"] == "Sub2API admin role is required"


def test_login_rejects_invalid_password(client) -> None:
    response = client.post(
        "/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )

    assert response.status_code == 401
    payload = response.json()
    assert payload["success"] is False
    assert payload["detail"] == "Invalid username or password"


def test_provision_start_requires_auth(client) -> None:
    response = client.post("/provision/start", json={"email": "user@example.com"})

    assert response.status_code == 401
    payload = response.json()
    assert payload["success"] is False
    assert payload["detail"] == "Authentication required"


def test_provision_start_persists_flow_in_postgres_with_cookie_auth(client) -> None:
    login(client)

    with patch.object(requests.Session, "request", new=fake_sub2api_request):
        response = client.post("/provision/start", json={"email": "user@example.com"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["account_name"] == "user@example.com"
    assert payload.get("user_id") is None
    assert payload["group_id"] == "g-1"
    assert payload["oauth_redirect_uri"] == "http://localhost:1455/auth/callback"

    stored_flow = main.get_flow_store().get_by_flow_id(payload["flow_id"])
    assert stored_flow is not None
    assert stored_flow.email == "user@example.com"
    assert stored_flow.user_id is None
    assert stored_flow.status.value == "pending_oauth"


def test_provision_start_uses_openai_group_defaults(client) -> None:
    login(client)

    with patch.object(requests.Session, "request", new=fake_sub2api_request):
        response = client.post("/provision/start", json={"email": "user@example.com"})

    assert response.status_code == 200
    assert response.json()["group_id"] == "g-1"


def test_provision_start_uses_email_and_platform_as_dedicated_group_name(client) -> None:
    create_group_payloads: list[dict[str, object]] = []
    email = "testqtest@outlook.my"

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        if method == "POST" and urlparse(url).path == "/api/v1/admin/groups":
            create_group_payloads.append(json)
        return fake_sub2api_request(self, method, url, json=json, params=params, timeout=timeout)

    login(client)

    with patch.object(requests.Session, "request", new=fake_request):
        response = client.post("/provision/start", json={"email": email})

    assert response.status_code == 200
    payload = response.json()
    assert payload["account_name"] == email
    assert create_group_payloads[0]["name"] == f"{email}_openai"


def test_provision_start_group_name_truncation_keeps_the_platform_suffix(client) -> None:
    create_group_payloads: list[dict[str, object]] = []
    email = f"{'a' * 64}@{'b' * 50}.example.com"
    assert len(email) > 128 - len("_openai")

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        if method == "POST" and urlparse(url).path == "/api/v1/admin/groups":
            create_group_payloads.append(json)
        return fake_sub2api_request(self, method, url, json=json, params=params, timeout=timeout)

    login(client)

    with patch.object(requests.Session, "request", new=fake_request):
        response = client.post("/provision/start", json={"email": email})

    assert response.status_code == 200
    group_name = create_group_payloads[0]["name"]
    # The upstream cap is 128 chars; the email loses its tail so the suffix that
    # identifies the platform stays intact.
    assert len(group_name) == 128
    assert group_name.endswith("_openai")
    assert group_name == f"{email[:121]}_openai"


class FakeMultiPlatformUpstream:
    """Fake upstream that keeps the groups it creates, so a flow can be replayed.

    The completion half of an OAuth flow recovers its platform from the target
    group, so a fake that forgets what it created would silently answer every
    callback as the default platform.
    """

    def __init__(self) -> None:
        self.groups: list[dict[str, object]] = []
        self.create_group_payloads: list[dict[str, object]] = []
        self.auth_url_calls: list[dict[str, object]] = []
        self.exchange_calls: list[dict[str, object]] = []
        self.create_account_payloads: list[dict[str, object]] = []
        self.scheduled_test_plan_payloads: list[dict[str, object]] = []

    def request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        if method == "GET" and path == "/api/v1/admin/groups/all":
            wanted = (params or {}).get("platform")
            items = [
                group
                for group in self.groups
                if not wanted or group["platform"] == wanted
            ]
            return FakeResponse(200, {"items": items})
        if method == "POST" and path == "/api/v1/admin/groups":
            self.create_group_payloads.append(dict(json or {}))
            group = {
                "id": f"g-{len(self.groups) + 1}",
                "name": json["name"],
                "platform": json["platform"],
                "is_exclusive": json.get("is_exclusive", True),
            }
            self.groups.append(group)
            return FakeResponse(200, group)
        if method == "GET" and path == "/api/v1/admin/accounts":
            return FakeResponse(200, {"items": []})
        if method == "POST" and path == "/api/v1/admin/grok/oauth/auth-url":
            self.auth_url_calls.append(dict(json or {}))
            return FakeResponse(
                200,
                {
                    "auth_url": (
                        "https://accounts.x.ai/authorize"
                        "?redirect_uri=http%3A%2F%2F127.0.0.1%3A56121%2Fcallback"
                        "&state=grok-upstream-state"
                    ),
                    "session_id": "grok-session-1",
                    "state": "grok-upstream-state",
                },
            )
        if method == "POST" and path == "/api/v1/admin/grok/oauth/exchange-code":
            self.exchange_calls.append(dict(json or {}))
            return FakeResponse(200, dict(GROK_EXCHANGE_PAYLOAD))
        if method == "POST" and path == "/api/v1/admin/accounts":
            self.create_account_payloads.append(dict(json or {}))
            return FakeResponse(200, {"account_id": "grok-acct-1", "name": json["name"]})
        if (
            method == "GET"
            and path.startswith("/api/v1/admin/accounts/")
            and path.endswith("/scheduled-test-plans")
        ):
            # Upstream's real answer for an account with no plans.
            return FakeResponse(200, {"data": None})
        if method == "POST" and path == "/api/v1/admin/scheduled-test-plans":
            self.scheduled_test_plan_payloads.append(dict(json or {}))
            return FakeResponse(200, {"id": "stp-1", **dict(json or {})})
        return FakeResponse(404, {"detail": f"unexpected {method} {path}"})


def test_provision_grok_oauth_flow_end_to_end(client, caplog) -> None:
    backend = FakeMultiPlatformUpstream()
    login(client)

    with caplog.at_level(logging.WARNING), patch.object(
        requests.Session, "request", new=backend.request
    ):
        start_response = client.post(
            "/provision/start",
            json={"email": "user@example.com", "platform": "grok"},
        )
        assert start_response.status_code == 200
        start_payload = start_response.json()
        complete_response = client.post(
            "/provision/oauth/complete",
            json={
                "callback_url": (
                    "http://127.0.0.1:56121/callback"
                    "?code=grok-code-1&state=grok-upstream-state"
                )
            },
        )

    assert start_payload["oauth_required"] is True
    assert start_payload["oauth_url"].startswith("https://accounts.x.ai/authorize")
    # Taken from the auth URL upstream actually minted, not from any local default.
    assert start_payload["oauth_redirect_uri"] == "http://127.0.0.1:56121/callback"
    assert complete_response.status_code == 200
    assert complete_response.json()["oauth_account_id"] == "grok-acct-1"

    group_payload = backend.create_group_payloads[0]
    assert group_payload["name"] == "user@example.com_grok"
    assert group_payload["platform"] == "grok"
    assert "supported_model_scopes" not in group_payload
    assert "messages_dispatch_model_config" not in group_payload

    # grok mints its own state, so nothing sidecar generated is sent to auth-url.
    assert backend.auth_url_calls == [{}]
    assert backend.exchange_calls == [
        {
            "session_id": "grok-session-1",
            "code": "grok-code-1",
            "state": "grok-upstream-state",
        }
    ]

    account_payload = backend.create_account_payloads[0]
    assert "provider" not in account_payload
    assert account_payload["platform"] == "grok"
    assert account_payload["type"] == "oauth"
    assert account_payload["name"] == "user@example.com"
    assert account_payload["group_ids"] == ["g-1"]
    assert account_payload["credentials"] == EXPECTED_GROK_CREDENTIALS
    assert account_payload["extra"] == EXPECTED_GROK_EXTRA
    assert "openai_oauth_responses_websockets_v2_mode" not in account_payload["extra"]

    # The `{"data": null}` plan list is "no plans", so the default plan is created
    # and nothing warns about it.
    assert backend.scheduled_test_plan_payloads == [
        {"account_id": "grok-acct-1", **EXPECTED_DEFAULT_SCHEDULED_TEST_PLAN}
    ]
    assert "scheduled test plan setup failed" not in caplog.text.lower()

    detail = client.get(f"/provision/flows/{start_payload['flow_id']}")
    assert detail.status_code == 200
    events = detail.json()["events"]
    # The platform travels with the flow across the OAuth handoff, so the timeline
    # says which platform every step ran on.
    platform_events = {
        event["event_type"]: (event["details"] or {}).get("platform") for event in events
    }
    for event_type in (
        "start_requested",
        "group_resolved",
        "oauth_url_generated",
        "pending_oauth",
        "oauth_exchanged",
        "account_created",
        "account_bound",
        "completed",
    ):
        assert platform_events[event_type] == "grok", event_type
    assert not any(event["status"] == "failed" for event in events)


def test_provision_start_rejects_oauth_platform_without_support(client) -> None:
    backend = FakeMultiPlatformUpstream()
    login(client)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/provision/start",
            json={"email": "user@example.com", "platform": "anthropic"},
        )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "anthropic" in detail
    assert "openai" in detail and "grok" in detail
    # The refusal lands before anything is written upstream.
    assert backend.create_group_payloads == []
    assert backend.create_account_payloads == []


def test_provision_start_without_platform_uses_configured_default(client) -> None:
    login(client)

    with patch.object(requests.Session, "request", new=fake_sub2api_request):
        response = client.post("/provision/start", json={"email": "user@example.com"})

    assert response.status_code == 200
    # SUB2API_GROUP_PLATFORM is openai in the test config; omitting `platform` must
    # keep the pre-multi-platform behavior.
    assert response.json()["group_id"] == "g-1"


def test_provision_apikey_start_for_grok_defaults_the_base_url(client) -> None:
    backend = FakeMultiPlatformUpstream()
    login(client)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/provision/apikey/start",
            json={"name": "grok-key-1", "api_key": "xai-test-123", "platform": "grok"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["account_id"] == "grok-acct-1"

    assert backend.create_group_payloads[0]["name"] == "grok-key-1_grok"
    assert backend.create_group_payloads[0]["platform"] == "grok"
    account_payload = backend.create_account_payloads[0]
    assert "provider" not in account_payload
    assert account_payload["platform"] == "grok"
    assert account_payload["type"] == "api_key"
    # An API key talks to the platform's public API host, so the template's OAuth
    # base_url (cli-chat-proxy) must not displace it. The rest of the template — the
    # backoff rules and the model mapping — applies here as it does anywhere.
    assert account_payload["credentials"] == {
        "api_key": "xai-test-123",
        "base_url": "https://api.x.ai/v1",
        "temp_unschedulable_enabled": True,
        "temp_unschedulable_rules": EXPECTED_TEMPORARY_UNSCHEDULABLE_RULES,
        "model_mapping": EXPECTED_GROK_MODEL_MAPPING,
    }


def test_provisioning_settings_report_supported_oauth_platforms(client) -> None:
    login(client)

    response = client.get("/api/provisioning/settings")

    assert response.status_code == 200
    payload = response.json()
    # The UI builds its platform picker from this; it never hardcodes a list.
    assert payload["supported_oauth_platforms"] == ["grok", "openai"]
    assert payload["settings"]["assignment_mode"] == "dedicated"


def test_sub2api_client_builds_apikey_account_payload() -> None:
    captured: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        captured.append({"method": method, "path": urlparse(url).path, "json": json})
        return FakeResponse(200, {"account_id": "oa-9", "name": json["name"]})

    sub2api = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        result = sub2api.create_account_from_apikey(
            name="key-acct",
            base_url="https://api.openai.com/v1",
            api_key="sk-abc",
            group_id="g-7",
        )

    assert result["id"] == "oa-9"
    assert captured[0]["path"] == "/api/v1/admin/accounts"
    payload = captured[0]["json"]
    assert "provider" not in payload
    assert payload["platform"] == "openai"
    assert payload["type"] == "api_key"
    assert payload["credentials"]["api_key"] == "sk-abc"
    assert payload["credentials"]["base_url"] == "https://api.openai.com/v1"
    assert payload["credentials"]["model_mapping"] == EXPECTED_MODEL_WHITELIST_MAPPING
    assert payload["credentials"]["temp_unschedulable_enabled"] is True
    assert payload["group_ids"] == ["g-7"]
    assert payload["concurrency"] == 5
    assert payload["extra"] == {}
    # API key accounts must not carry OAuth token fields.
    assert "access_token" not in payload["credentials"]
    assert "refresh_token" not in payload["credentials"]


def test_sub2api_client_apikey_account_payload_sends_numeric_group_ids_as_numbers() -> None:
    captured: list[dict[str, object]] = []

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        captured.append({"method": method, "path": urlparse(url).path, "json": json})
        return FakeResponse(200, {"account_id": "oa-9", "name": json["name"]})

    sub2api = Sub2APIClient(
        base_url="https://sub2api.example.com",
        admin_api_key="admin-key",
        provisioning_defaults=Sub2APIProvisioningDefaults(),
    )

    with patch.object(requests.Session, "request", new=fake_request):
        sub2api.create_account_from_apikey(
            name="key-acct",
            base_url="https://api.openai.com/v1",
            api_key="sk-abc",
            group_id="11",
        )

    assert captured[0]["json"]["group_ids"] == [11]


def test_provision_apikey_start_creates_account_without_oauth(client) -> None:
    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        if method == "POST" and path == "/api/v1/admin/accounts":
            assert "provider" not in json
            assert json["platform"] == "openai"
            assert json["type"] == "api_key"
            assert json["credentials"]["api_key"] == "sk-test-123"
            assert json["credentials"]["base_url"] == "https://api.openai.com/v1"
            assert json["credentials"]["model_mapping"] == EXPECTED_MODEL_WHITELIST_MAPPING
            assert json["credentials"]["temp_unschedulable_enabled"] is True
            assert json["group_ids"] == ["g-1"]
            assert json["concurrency"] == 5
            assert "access_token" not in json["credentials"]
            return FakeResponse(200, {"account_id": "oa-key-1", "name": json["name"]})
        if method == "POST" and path == "/api/v1/admin/scheduled-test-plans":
            return FakeResponse(200, {"id": "stp-1", **json})
        return fake_sub2api_request(self, method, url, json=json, params=params, timeout=timeout)

    login(client)

    with patch.object(requests.Session, "request", new=fake_request):
        response = client.post(
            "/provision/apikey/start",
            json={
                "name": "manual-key-1",
                "api_base_url": "https://api.openai.com/v1",
                "api_key": "sk-test-123",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["status"] == "completed"
    assert payload["name"] == "manual-key-1"
    assert payload["account_name"] == "manual-key-1"
    assert payload["account_id"] == "oa-key-1"
    assert payload["group_id"] == "g-1"

    stored_flow = main.get_flow_store().get_by_flow_id(payload["flow_id"])
    assert stored_flow is not None
    assert stored_flow.status.value == "completed"
    assert stored_flow.account_name == "manual-key-1"
    assert stored_flow.oauth_account_id == "oa-key-1"


def test_provision_apikey_start_succeeds_when_scheduled_test_plan_setup_fails(client) -> None:
    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        if method == "POST" and path == "/api/v1/admin/accounts":
            return FakeResponse(200, {"account_id": "oa-key-1", "name": json["name"]})
        if method == "POST" and path == "/api/v1/admin/scheduled-test-plans":
            return FakeResponse(500, {"detail": "scheduled test plan backend unavailable"})
        return fake_sub2api_request(self, method, url, json=json, params=params, timeout=timeout)

    login(client)

    with patch.object(requests.Session, "request", new=fake_request):
        response = client.post(
            "/provision/apikey/start",
            json={
                "name": "manual-key-1",
                "api_base_url": "https://api.openai.com/v1",
                "api_key": "sk-test-123",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["status"] == "completed"
    assert payload["account_id"] == "oa-key-1"

    stored_flow = main.get_flow_store().get_by_flow_id(payload["flow_id"])
    assert stored_flow is not None
    assert stored_flow.status.value == "completed"
    assert stored_flow.oauth_account_id == "oa-key-1"

    events = main.get_flow_store().list_provision_events(payload["flow_id"])
    schedule_events = [
        event
        for event in events
        if event.message == "Default scheduled test plan setup failed after account provisioning"
    ]
    assert len(schedule_events) == 1
    assert schedule_events[0].status.value == "failed"
    assert "scheduled test plan backend unavailable" in (
        schedule_events[0].details or {}
    ).get("error", "")


def test_provision_apikey_start_requires_auth(client) -> None:
    response = client.post(
        "/provision/apikey/start",
        json={
            "name": "manual-key-1",
            "api_base_url": "https://api.openai.com/v1",
            "api_key": "sk-test-123",
        },
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "auth_headers",
    [
        lambda access_key: {"X-Access-Key": access_key},
        lambda access_key: {"Authorization": f"Bearer {access_key}"},
    ],
    ids=["x-access-key", "bearer"],
)
def test_provision_start_supports_header_auth(client, auth_headers) -> None:
    access_key = login(client)["access_key"]

    with started_test_client() as stateless_client:
        with patch.object(requests.Session, "request", new=fake_sub2api_request):
            response = stateless_client.post(
                "/provision/start",
                json={"email": "header@example.com"},
                headers=auth_headers(access_key),
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["email"] == "header@example.com"
    assert payload["account_name"] == "header@example.com"


def test_oauth_complete_from_pasted_callback_url_after_cache_reset(client) -> None:
    with patch.object(requests.Session, "request", new=fake_sub2api_request):
        login(client)
        start_response = client.post("/provision/start", json={"email": "user@example.com"})
        state = parse_qs(urlparse(start_response.json()["oauth_url"]).query)["state"][0]

        clear_caches()

        with started_test_client() as restarted_client:
            login(restarted_client)
            callback_response = restarted_client.post(
                "/provision/oauth/complete",
                json={
                    "callback_url": (
                        f"http://localhost:1455/callback?code=mock-code&state={state}"
                    )
                },
            )

    assert callback_response.status_code == 200
    payload = callback_response.json()
    assert payload["status"] == "completed"
    assert payload["oauth_account_id"] == "oa-1"

    completed_flow = main.get_flow_store().get_by_state(state)
    assert completed_flow is not None
    assert completed_flow.status.value == "completed"
    assert completed_flow.oauth_account_id == "oa-1"
    assert completed_flow.account_name == "user@example.com"


def test_oauth_complete_uses_openai_oauth_account_defaults(client) -> None:
    with patch.object(requests.Session, "request", new=fake_sub2api_request):
        login(client)
        start_response = client.post("/provision/start", json={"email": "user@example.com"})
        state = parse_qs(urlparse(start_response.json()["oauth_url"]).query)["state"][0]
        callback_response = client.post(
            "/provision/oauth/complete",
            json={
                "callback_url": (
                    f"http://localhost:1455/callback?code=mock-code&state={state}"
                )
            },
        )

    assert callback_response.status_code == 200
    assert callback_response.json()["status"] == "completed"


def test_provision_flow_dashboard_requires_auth(client) -> None:
    list_response = client.get("/provision/flows")
    detail_response = client.get("/provision/flows/missing-flow")

    assert list_response.status_code == 401
    assert detail_response.status_code == 401


def test_provision_flow_dashboard_lists_filters_details_events_and_redacts(client) -> None:
    with patch.object(requests.Session, "request", new=fake_sub2api_request):
        login(client)
        pending_response = client.post(
            "/provision/start", json={"email": "pending-dashboard@example.com"}
        )
        start_response = client.post(
            "/provision/start", json={"email": "dashboard@example.com"}
        )
        state = parse_qs(urlparse(start_response.json()["oauth_url"]).query)["state"][0]
        complete_response = client.post(
            "/provision/oauth/complete",
            json={
                "callback_url": (
                    f"http://localhost:1455/callback?code=mock-code&state={state}"
                )
            },
        )

    assert pending_response.status_code == 200
    assert start_response.status_code == 200
    assert complete_response.status_code == 200

    list_response = client.get("/provision/flows?status=completed&email=dashboard")
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["success"] is True
    assert list_payload["total"] == 1
    assert list_payload["items"][0]["flow_id"] == start_response.json()["flow_id"]
    assert list_payload["items"][0]["status"] == "completed"
    assert "oauth_exchange_payload" not in list_payload["items"][0]

    pending_list = client.get("/provision/flows?status=pending_oauth&limit=1&offset=0")
    assert pending_list.status_code == 200
    assert pending_list.json()["total"] == 1
    assert len(pending_list.json()["items"]) == 1
    assert pending_list.json()["items"][0]["flow_id"] == pending_response.json()["flow_id"]

    detail_response = client.get(f"/provision/flows/{start_response.json()['flow_id']}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    event_types = [event["event_type"] for event in detail_payload["events"]]
    assert detail_payload["state"] == state
    assert detail_payload["oauth_exchange_payload"]["access_token"] == "[redacted]"
    assert detail_payload["oauth_exchange_payload"]["refresh_token"] == "[redacted]"
    assert "start_requested" in event_types
    assert "oauth_exchanged" in event_types
    assert event_types[-1] == "completed"


def test_provision_flow_dashboard_rejects_invalid_filter_and_missing_flow(client) -> None:
    login(client)

    invalid_filter = client.get("/provision/flows?status=not-a-status")
    missing_flow = client.get("/provision/flows/not-found")

    assert invalid_filter.status_code == 422
    assert missing_flow.status_code == 404
    assert missing_flow.json()["detail"] == "Provisioning flow not found"


def test_provision_start_rejects_invalid_email(client) -> None:
    login(client)
    response = client.post("/provision/start", json={"email": "not-an-email"})

    assert response.status_code == 422
    payload = response.json()
    assert payload["success"] is False


def test_oauth_complete_rejects_malformed_callback_url(client) -> None:
    login(client)
    response = client.post(
        "/provision/oauth/complete",
        json={"callback_url": "http://localhost:1455/callback?state=missing-code"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert "Unable to parse code and state" in payload["detail"]


def test_oauth_complete_rejects_callback_url_carrying_an_error(client) -> None:
    login(client)
    response = client.post(
        "/provision/oauth/complete",
        json={"callback_url": "http://localhost:1455/callback?error=access_denied"},
    )

    assert response.status_code == 400
    assert "access_denied" in response.json()["detail"]


def test_oauth_complete_accepts_a_bare_grok_authorization_code(client) -> None:
    """grok's authorization page often prints a code instead of calling back."""
    backend = FakeMultiPlatformUpstream()
    login(client)

    with patch.object(requests.Session, "request", new=backend.request):
        start_response = client.post(
            "/provision/start",
            json={"email": "user@example.com", "platform": "grok"},
        )
        assert start_response.status_code == 200
        start_payload = start_response.json()
        complete_response = client.post(
            "/provision/oauth/complete",
            json={
                # Trailing `=` padding is why the parser tests for a parsed `code`
                # key rather than for parse_qs having produced anything: this string
                # reads as the pair {"ory_ac_grok-1": ["="]}.
                "callback_url": "  ory_ac_grok-1==  ",
                "flow_id": start_payload["flow_id"],
            },
        )

    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "completed"
    # The state was never pasted: it comes off the flow sidecar stored at start.
    assert backend.exchange_calls == [
        {
            "session_id": "grok-session-1",
            "code": "ory_ac_grok-1==",
            "state": "grok-upstream-state",
        }
    ]


def test_oauth_complete_accepts_a_bare_openai_authorization_code(client) -> None:
    with patch.object(requests.Session, "request", new=fake_sub2api_request):
        login(client)
        start_response = client.post("/provision/start", json={"email": "user@example.com"})
        assert start_response.status_code == 200
        start_payload = start_response.json()
        complete_response = client.post(
            "/provision/oauth/complete",
            json={"callback_url": "bare-openai-code", "flow_id": start_payload["flow_id"]},
        )

    assert complete_response.status_code == 200
    payload = complete_response.json()
    assert payload["status"] == "completed"
    assert payload["oauth_account_id"] == "oa-1"

    state = parse_qs(urlparse(start_payload["oauth_url"]).query)["state"][0]
    completed_flow = main.get_flow_store().get_by_state(state)
    assert completed_flow is not None
    assert completed_flow.status.value == "completed"


def test_oauth_complete_bare_code_without_flow_id_is_rejected(client) -> None:
    login(client)
    response = client.post(
        "/provision/oauth/complete",
        json={"callback_url": "ory_ac_orphan-code"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    # Guessing "the only pending flow" is not a fallback, so the message has to say
    # what the operator can actually do about it.
    assert "flow_id" in detail
    assert "callback URL" in detail
    assert "restart the authorization" in detail


def test_oauth_complete_bare_code_with_unknown_flow_id_is_rejected(client) -> None:
    login(client)
    response = client.post(
        "/provision/oauth/complete",
        json={"callback_url": "ory_ac_orphan-code", "flow_id": "no-such-flow"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "no-such-flow" in detail
    assert "restart the authorization" in detail


def test_oauth_complete_prefers_the_pasted_state_over_flow_id(client) -> None:
    """A full callback URL still identifies its own flow; flow_id is ignored."""
    with patch.object(requests.Session, "request", new=fake_sub2api_request):
        login(client)
        start_response = client.post("/provision/start", json={"email": "user@example.com"})
        state = parse_qs(urlparse(start_response.json()["oauth_url"]).query)["state"][0]
        complete_response = client.post(
            "/provision/oauth/complete",
            json={
                "callback_url": f"http://localhost:1455/callback?code=mock-code&state={state}",
                "flow_id": "no-such-flow",
            },
        )

    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "completed"


def test_oauth_complete_reports_that_a_failed_exchange_burns_the_session(client) -> None:
    backend = FakeMultiPlatformUpstream()
    login(client)

    def failing_exchange(self, method: str, url: str, json=None, params=None, timeout=None):
        if method == "POST" and urlparse(url).path.endswith("/grok/oauth/exchange-code"):
            return FakeResponse(400, {"message": "invalid authorization code"})
        return backend.request(method, url, json=json, params=params, timeout=timeout)

    with patch.object(requests.Session, "request", new=failing_exchange):
        start_response = client.post(
            "/provision/start",
            json={"email": "burned@example.com", "platform": "grok"},
        )
        assert start_response.status_code == 200
        complete_response = client.post(
            "/provision/oauth/complete",
            json={
                "callback_url": "ory_ac_wrong-code",
                "flow_id": start_response.json()["flow_id"],
            },
        )

    assert complete_response.status_code == 502
    detail = complete_response.json()["detail"]
    assert "invalid authorization code" in detail
    # The session is spent whether or not the exchange succeeded, so the only way
    # forward is a fresh authorization — say so instead of inviting a re-paste.
    assert "single-use" in detail
    assert "Restart the authorization" in detail


def test_rotation_pool_candidates_and_exclusive_selection(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        add_response = client.post("/rotation/pool/groups", json={"group_id": 11, "priority": 0})
        candidates_response = client.get("/rotation/pool/candidates")

    assert add_response.status_code == 200
    assert candidates_response.status_code == 200
    items = candidates_response.json()["items"]
    selected = {item["group_id"]: item for item in items}
    assert selected[11]["selected"] is True
    assert selected[11]["is_exclusive"] is True
    assert selected[11]["is_subscription"] is False
    assert selected[11]["rotation_supported"] is True
    assert selected[33]["selected"] is False
    assert selected[33]["is_exclusive"] is False
    assert selected[33]["rotation_supported"] is False
    assert selected[44]["is_subscription"] is True
    assert selected[44]["rotation_supported"] is False


def test_rotation_pool_stores_the_upstream_platform_of_the_group(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        openai_group = client.post("/rotation/pool/groups", json={"group_id": 11})
        grok_group = client.post("/rotation/pool/groups", json={"group_id": 72})

    assert openai_group.status_code == 200
    assert grok_group.status_code == 200
    # Pool rows carry the platform they were added with, which is what buckets
    # the balancing decisions later on.
    stored = {
        group.group_id: group.platform
        for group in main.get_flow_store().list_rotation_pool_groups()
    }
    assert stored["11"] == "openai"
    assert stored["72"] == "grok"


def test_landing_and_rotation_pools_are_independent(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        landing = client.post(
            "/rotation/pool/groups",
            json={"group_id": 11, "pool_kind": "landing", "priority": 0},
        )
        rotation = client.post(
            "/rotation/pool/groups",
            json={"group_id": 11, "pool_kind": "rotation", "priority": 0},
        )
        candidates = client.get("/rotation/pool/candidates")
        removed_landing = client.post(
            "/rotation/pool/groups/remove",
            json={"group_id": 11, "pool_kind": "landing"},
        )
        candidates_after_remove = client.get("/rotation/pool/candidates")

    assert landing.status_code == 200
    assert landing.json()["pool_kind"] == "landing"
    assert rotation.status_code == 200
    assert rotation.json()["pool_kind"] == "rotation"
    item = {group["group_id"]: group for group in candidates.json()["items"]}[11]
    assert item["landing_selected"] is True
    assert item["rotation_selected"] is True
    assert removed_landing.status_code == 200
    item_after_remove = {
        group["group_id"]: group for group in candidates_after_remove.json()["items"]
    }[11]
    assert item_after_remove["landing_selected"] is False
    assert item_after_remove["rotation_selected"] is True


def test_rotation_pool_delete_is_idempotent(client) -> None:
    login(client)

    landing_response = client.delete("/rotation/pool/groups/999999?pool_kind=landing")
    rotation_response = client.delete("/rotation/pool/groups/999999")
    landing_post_response = client.post(
        "/rotation/pool/groups/remove",
        json={"group_id": 999999, "pool_kind": "landing"},
    )

    assert landing_response.status_code == 200
    assert landing_response.json() == {
        "success": True,
        "group_id": "999999",
        "pool_kind": "landing",
    }
    assert rotation_response.status_code == 200
    assert rotation_response.json() == {
        "success": True,
        "group_id": "999999",
        "pool_kind": "rotation",
    }
    assert landing_post_response.status_code == 200
    assert landing_post_response.json() == {
        "success": True,
        "group_id": "999999",
        "pool_kind": "landing",
    }


def test_landing_pool_accepts_public_non_subscription_group(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/rotation/pool/groups",
            json={"group_id": 33, "pool_kind": "landing", "priority": 0},
        )
        candidates = client.get("/rotation/pool/candidates")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pool_kind"] == "landing"
    assert payload["is_exclusive"] is False
    assert payload["rotation_supported"] is False
    item = {group["group_id"]: group for group in candidates.json()["items"]}[33]
    assert item["landing_selected"] is True
    assert item["rotation_selected"] is False
    assert item["rotation_supported"] is False


def test_landing_pool_rejects_subscription_group(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/rotation/pool/groups",
            json={"group_id": 44, "pool_kind": "landing"},
        )

    assert response.status_code == 400
    assert "Subscription groups cannot be added to the landing pool" in response.json()["detail"]


def test_pool_candidates_fallback_to_upstream_groups_without_snapshot(client) -> None:
    backend = FakeRotationSub2API()
    login(client)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.get("/rotation/pool/candidates")

    assert response.status_code == 200
    items = response.json()["items"]
    selected = {item["group_id"]: item for item in items}
    # Candidates span every platform; the caller picks by the platform field.
    assert set(selected) == {11, 22, 33, 44, 71, 72}
    assert selected[71]["platform"] == "grok"
    assert selected[33]["is_exclusive"] is False
    assert selected[33]["is_subscription"] is False
    assert selected[33]["rotation_supported"] is False
    assert selected[44]["is_subscription"] is True


def test_landing_pool_add_fallback_to_upstream_without_snapshot(client) -> None:
    backend = FakeRotationSub2API()
    login(client)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/rotation/pool/groups",
            json={"group_id": 33, "pool_kind": "landing"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pool_kind"] == "landing"
    assert payload["group_id"] == "33"
    assert payload["is_exclusive"] is False


def test_rotation_pool_rejects_public_group(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post("/rotation/pool/groups", json={"group_id": 33})

    assert response.status_code == 400
    assert "exclusive groups" in response.json()["detail"]


def test_rotation_pool_rejects_subscription_group(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post("/rotation/pool/groups", json={"group_id": 44})

    assert response.status_code == 400
    assert "Subscription groups cannot be added" in response.json()["detail"]


def test_existing_orchestration_lists_users_groups_and_keys(client) -> None:
    backend = FakeRotationSub2API()
    login(client)

    with patch.object(requests.Session, "request", new=backend.request):
        users_response = client.get("/orchestration/users?email=rotate")
        groups_response = client.get("/orchestration/groups")
        accounts_response = client.get("/orchestration/accounts")
        keys_response = client.get("/orchestration/users/101/api-keys")

    assert users_response.status_code == 200
    assert users_response.json()["total"] == 1
    assert users_response.json()["items"][0]["user_id"] == 101
    assert users_response.json()["items"][0]["username"] == "rotator"
    assert users_response.json()["items"][0]["display_name"] == "Rotate Operator"
    assert users_response.json()["items"][0]["current_group_id"] == 11
    assert groups_response.status_code == 200
    groups = {item["group_id"]: item for item in groups_response.json()["items"]}
    assert groups[11]["rotation_supported"] is True
    assert groups[11]["account_count"] == 2
    assert groups[11]["active_account_count"] == 1
    assert groups[11]["rpm_limit"] == 120
    assert groups[11]["rate_multiplier"] == 1.5
    assert groups[11]["daily_limit_usd"] == 10.0
    assert groups[11]["weekly_limit_usd"] == 50.0
    assert groups[11]["monthly_limit_usd"] == 200.0
    assert groups[44]["rotation_supported"] is False
    assert accounts_response.status_code == 200
    accounts = {item["account_id"]: item for item in accounts_response.json()["items"]}
    assert accounts["acct-1"]["group_ids"] == [11]
    assert accounts["acct-1"]["availability_status"] == "available"
    assert accounts["acct-1"]["is_available"] is True
    assert accounts["acct-1"]["concurrency"] == 3.0
    assert accounts["acct-1"]["current_concurrency"] == 1.0
    assert accounts["acct-1"]["quota_remaining"] == 85.5
    assert accounts["acct-1"]["usage_5h_percent"] == 39.0
    assert accounts["acct-1"]["usage_7d_percent"] == 85.0
    assert accounts["acct-1"]["usage_updated_at"] == "2026-05-11T13:59:49+08:00"
    assert accounts["acct-2"]["group_ids"] == [22]
    assert accounts["acct-2"]["availability_status"] == "rate_limited"
    assert accounts["acct-2"]["is_available"] is False
    assert accounts["acct-2"]["rate_limited"] is True
    assert accounts["acct-2"]["concurrency"] == 7.0
    assert accounts["acct-2"]["current_concurrency"] == 2.0
    assert accounts["acct-2"]["last_error"] == "429 too many requests"
    assert accounts["acct-2"]["usage_5h_percent"] == 0.0
    assert accounts["acct-2"]["usage_7d_percent"] == 25.0
    assert accounts[7]["group_ids"] == []
    assert accounts["acct-camel"]["group_ids"] == [44, 33]
    assert accounts["acct-camel"]["group_names"] == ["subscription-dedicated", ""]
    assert keys_response.status_code == 200
    assert keys_response.json()["items"][0]["key_id"] == "key-101"


def test_existing_orchestration_parses_camel_case_user_group_fields(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 909,
            "email": "camel-user@example.com",
            "name": "Camel User",
            "status": "active",
            "currentGroup": {"groupId": 22, "groupName": "rotation-high"},
            "allowedGroups": [{"groupId": 22, "groupName": "rotation-high"}],
        }
    ]
    login(client)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.get("/orchestration/users?email=camel-user")

    assert response.status_code == 200
    payload = response.json()["items"][0]
    assert payload["current_group_id"] == 22
    assert payload["current_group_name"] == "rotation-high"
    assert payload["group_ids"] == [22]


def test_credit_control_lists_filters_and_details_users(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)
    UsageSegmentationService(main.get_flow_store()).refresh()

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.get(
            "/api/credit-control/users?window=7d&search=rotate&balance_min=10&limit=10"
        )
        segment_response = client.get(
            "/api/credit-control/users?usage_segment=active&limit=10"
        )
        spike_response = client.get(
            "/api/credit-control/users?usage_segment=spike&limit=10"
        )
        detail_response = client.get("/api/credit-control/users/101?window=5h")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["total"] == 1
    assert payload["items"][0]["user_id"] == 101
    assert payload["items"][0]["balance"] == 12.5
    assert payload["items"][0]["balance_display"] == "12.5 credits"
    assert payload["items"][0]["balance_unit"] == "credits"
    assert payload["items"][0]["consumption"] == 6.0
    assert payload["items"][0]["usage_segment"] == "spike"
    assert payload["items"][0]["usage_segment_label"] == "短期突增"
    assert payload["items"][0]["usage_profile"]["daily_average_by_window"]["30d"] == pytest.approx(20.0 / 30.0)
    assert payload["aggregates"]["total_balance"] == 12.5
    assert payload["aggregates"]["total_consumption"] == 6.0
    assert payload["aggregates"]["segment_counts"]["spike"] == 1
    assert segment_response.status_code == 200
    assert segment_response.json()["total"] == 0
    assert spike_response.status_code == 200
    assert spike_response.json()["total"] == 2
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["item"]["user_id"] == 101
    assert detail_payload["item"]["usage_segment"] == "spike"
    assert detail_payload["item"]["api_keys"][0]["key_id"] == "key-101"
    assert detail_payload["item"]["api_keys"][0]["usage"] == 1.0


def test_credit_control_group_filter_matches_every_group_a_user_holds(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 707,
            "email": "dual@example.com",
            "name": "dual@example.com",
            "status": "active",
            # Multi-platform: no single "current" group, one group per platform.
            "allowed_groups": [11, 71],
            "balance": 5.0,
        },
        {
            "id": 202,
            "email": "idle@example.com",
            "name": "idle@example.com",
            "status": "active",
            "group_id": 22,
            "balance": 3.0,
        },
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        openai_response = client.get("/api/credit-control/users?group_id=11&limit=10")
        grok_response = client.get("/api/credit-control/users?group_id=71&limit=10")
        other_response = client.get("/api/credit-control/users?group_id=22&limit=10")

    # Same rule the group-scoped recharge policies use: holding the group counts.
    assert openai_response.status_code == 200
    assert [item["user_id"] for item in openai_response.json()["items"]] == [707]
    assert grok_response.status_code == 200
    assert [item["user_id"] for item in grok_response.json()["items"]] == [707]
    assert other_response.status_code == 200
    assert [item["user_id"] for item in other_response.json()["items"]] == [202]


def test_usage_segmentation_apis_require_auth_and_refresh(client) -> None:
    backend = FakeRotationSub2API()
    unauthenticated = client.get("/api/usage-segmentation/users")
    login(client)
    save_operational_snapshots(backend)

    refresh = client.post("/api/usage-segmentation/refresh")
    users = client.get("/api/usage-segmentation/users")
    scheduler = client.get("/api/usage-segmentation/scheduler")

    assert unauthenticated.status_code == 401
    assert refresh.status_code == 200
    assert refresh.json()["user_count"] == 2
    assert users.status_code == 200
    assert users.json()["total"] == 2
    assert users.json()["segment_counts"]["spike"] == 2
    assert scheduler.status_code == 200


def test_group_usage_apis_require_auth_and_refresh(client) -> None:
    backend = FakeRotationSub2API()
    unauthenticated = client.get("/api/group-usage/groups")
    login(client)
    save_operational_snapshots(backend)

    refresh = client.post("/api/group-usage/refresh")
    groups = client.get("/api/group-usage/groups")
    scheduler = client.get("/api/group-usage/scheduler")

    assert unauthenticated.status_code == 401
    assert refresh.status_code == 200
    # Six groups now: the four openai ones plus the two grok ones.
    assert refresh.json()["group_count"] == 6
    assert refresh.json()["window_counts"]["5h"] >= 2
    assert groups.status_code == 200
    payload = groups.json()
    assert payload["total"] == 6
    by_id = {str(item["group_id"]): item for item in payload["items"]}
    assert by_id["11"]["group_name"] == "rotation-low"
    assert by_id["11"]["usage_by_window"]["5h"] == 0.2
    assert by_id["11"]["source_by_window"]["1d"] == "dashboard_groups"
    assert scheduler.status_code == 200
    assert scheduler.json()["cadence_seconds"] > 0


def test_credit_control_manual_adjustment_preview_execute_and_audit(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)

    payload = {
        "amount": 5,
        "reason": "top up selected users",
        "target": {"mode": "users", "user_ids": [101]},
    }
    with patch.object(requests.Session, "request", new=backend.request):
        preview = client.post("/api/credit-control/adjustments/preview", json=payload)
        execute = client.post("/api/credit-control/adjustments", json=payload)
        audit = client.get("/api/credit-control/audit?user_id=101")

    assert preview.status_code == 200
    assert preview.json()["dry_run"] is True
    assert preview.json()["items"][0]["balance_after"] == 17.5
    assert backend.balance_calls == [
        {"user_id": 101, "balance": 5.0, "operation": "add", "notes": "top up selected users"}
    ]
    assert execute.status_code == 200
    execute_payload = execute.json()
    assert execute_payload["status"] == "succeeded"
    assert execute_payload["items"][0]["operation"] == "add"
    assert execute_payload["items"][0]["balance_after"] == 17.5
    assert audit.status_code == 200
    audit_payload = audit.json()
    assert audit_payload["total"] == 1
    assert audit_payload["items"][0]["action"] == "manual_adjustment"
    assert audit_payload["items"][0]["reason"] == "top up selected users"


def test_credit_control_filter_execution_forces_refresh_before_resolving_targets(client) -> None:
    backend = FakeRotationSub2API()
    call_order: list[str] = []
    login(client)

    def tracked_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        if method == "GET" and path == "/api/v1/admin/users":
            call_order.append("list_users")
        if method == "POST" and path.endswith("/balance"):
            call_order.append("balance")
        return backend.request(method, url, json=json, params=params, timeout=timeout)

    payload = {
        "amount": 5,
        "reason": "top up filtered users",
        "target": {"mode": "filter", "window": "1d"},
    }
    with patch.object(requests.Session, "request", new=tracked_request):
        response = client.post("/api/credit-control/adjustments", json=payload)

    assert response.status_code == 200
    assert response.json()["affected_count"] == 2
    assert call_order[0] == "list_users"
    assert "balance" in call_order
    assert call_order.index("list_users") < call_order.index("balance")


def test_credit_control_manual_adjustment_partial_failure_records_audit(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)

    payload = {
        "amount": -5,
        "reason": "deduct cohort",
        "target": {"mode": "users", "user_ids": [101, 202]},
    }
    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post("/api/credit-control/adjustments", json=payload)
        audit = client.get("/api/credit-control/audit?run_id=" + response.json()["run_id"])

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "partial_failed"
    statuses = {item["user_id"]: item["status"] for item in payload["items"]}
    assert statuses == {101: "succeeded", 202: "failed"}
    assert backend.balance_calls == [
        {"user_id": 101, "balance": 5.0, "operation": "subtract", "notes": "deduct cohort"},
        {"user_id": 202, "balance": 5.0, "operation": "subtract", "notes": "deduct cohort"},
    ]
    assert audit.status_code == 200
    assert audit.json()["total"] == 3


def test_credit_control_rejects_invalid_adjustment_without_upstream_call(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        zero = client.post(
            "/api/credit-control/adjustments",
            json={"amount": 0, "reason": "noop", "target": {"mode": "users", "user_ids": [101]}},
        )
        duplicate = client.post(
            "/api/credit-control/adjustments",
            json={
                "amount": 1,
                "reason": "duplicate ids",
                "target": {"mode": "users", "user_ids": [101, 101]},
            },
        )

    assert zero.status_code == 422
    assert duplicate.status_code == 422
    assert backend.balance_calls == []


def test_credit_control_policy_crud_preview_schedule_and_dedup(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)
    start_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    policy_payload = {
        "name": "low balance recharge",
        "enabled": True,
        "amount": 2,
        "schedule_type": "one_time",
        "schedule": start_at,
        "timezone": "Asia/Shanghai",
        "target_scope": "balance_threshold",
        "target_balance_below": 5,
        "reason_template": "auto top up low balance",
    }

    with patch.object(requests.Session, "request", new=backend.request):
        create = client.post("/api/credit-control/policies", json=policy_payload)
        preview = client.post("/api/credit-control/policies/preview", json=policy_payload)
        policies = client.get("/api/credit-control/policies")

    assert create.status_code == 200
    policy_id = create.json()["item"]["policy_id"]
    assert create.json()["item"]["target_scope"] == "balance_threshold"
    assert create.json()["item"]["target_balance_below"] == 5.0
    assert preview.status_code == 200
    assert preview.json()["dry_run"] is True
    assert preview.json()["affected_count"] == 1
    assert preview.json()["items"][0]["user_id"] == 202
    assert policies.status_code == 200
    assert policies.json()["total"] == 1

    stored = main.get_flow_store().get_credit_policy(policy_id)
    assert stored is not None
    due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    stored.schedule.start_at = due_at
    stored.next_run_at = due_at
    stored.enabled = True
    main.get_flow_store().save_credit_policy(stored)

    with patch.object(requests.Session, "request", new=backend.request):
        runs = main.get_credit_control_service().tick()
        duplicate_runs = main.get_credit_control_service().tick()
        runs_response = client.get("/api/credit-control/runs")
        audit_response = client.get(f"/api/credit-control/audit?policy_id={policy_id}")

    assert len(runs) == 1
    assert duplicate_runs == []
    assert backend.balance_calls == [
        {"user_id": 202, "balance": 2.0, "operation": "add", "notes": "auto top up low balance"}
    ]
    assert runs_response.status_code == 200
    assert runs_response.json()["total"] == 1
    assert runs_response.json()["items"][0]["policy_id"] == policy_id
    assert audit_response.status_code == 200
    assert audit_response.json()["total"] >= 2

    delete = client.delete(f"/api/credit-control/policies/{policy_id}")
    assert delete.status_code == 200
    assert main.get_flow_store().get_credit_policy(policy_id) is None


def test_credit_control_interval_policy_scans_with_users_only_refresh(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)
    policy_payload = {
        "name": "interval low balance recharge",
        "enabled": True,
        "amount": 2,
        "schedule_type": "interval",
        "schedule": "5m",
        "timezone": "Asia/Shanghai",
        "target_scope": "balance_threshold",
        "target_balance_below": 5,
        "reason_template": "interval top up",
    }

    with patch.object(requests.Session, "request", new=backend.request):
        create = client.post("/api/credit-control/policies", json=policy_payload)

    assert create.status_code == 200
    item = create.json()["item"]
    policy_id = item["policy_id"]
    assert item["schedule_type"] == "interval"
    assert item["schedule"] == "every 5m"
    assert item["next_run_at"] is not None

    service = main.get_credit_control_service()

    class _RefreshSpy:
        def __init__(self, real) -> None:
            self.real = real
            self.users_refresh_calls = 0
            self.full_refresh_calls = 0

        def refresh_users_snapshot(self):
            self.users_refresh_calls += 1
            return self.real.refresh_users_snapshot()

        def refresh_before_mutation(self):
            self.full_refresh_calls += 1
            return self.real.refresh_before_mutation()

    spy = _RefreshSpy(main.get_operational_data_refresher())
    service.operational_data_refresher = spy
    store = main.get_flow_store()

    def force_due() -> None:
        stored = store.get_credit_policy(policy_id)
        stored.next_run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        store.save_credit_policy(stored)

    force_due()
    with patch.object(requests.Session, "request", new=backend.request):
        first_runs = service.tick()

    assert len(first_runs) == 1
    assert backend.balance_calls == [
        {"user_id": 202, "balance": 2.0, "operation": "add", "notes": "interval top up"}
    ]
    advanced = store.get_credit_policy(policy_id)
    assert advanced.next_run_at is not None
    assert advanced.next_run_at > datetime.now(timezone.utc)

    # The fake upstream applied the recharge (3.0 -> 5.0), so the next scan
    # matches nobody and must not persist a run record.
    force_due()
    with patch.object(requests.Session, "request", new=backend.request):
        second_runs = service.tick()
        runs_response = client.get("/api/credit-control/runs")

    assert second_runs == []
    assert runs_response.status_code == 200
    assert runs_response.json()["total"] == 1
    assert spy.users_refresh_calls == 2
    assert spy.full_refresh_calls == 0
    empty_scan = store.get_credit_policy(policy_id)
    assert empty_scan.next_run_at is not None
    assert empty_scan.next_run_at > datetime.now(timezone.utc)


def test_credit_control_interval_policy_rejects_invalid_intervals(client) -> None:
    login(client)
    payload = {
        "name": "too fast",
        "enabled": True,
        "amount": 2,
        "schedule_type": "interval",
        "schedule": "30s",
        "target_scope": "balance_threshold",
        "target_balance_below": 5,
    }
    too_fast = client.post("/api/credit-control/policies", json=payload)
    assert too_fast.status_code == 422
    missing = client.post("/api/credit-control/policies", json={**payload, "schedule": None})
    assert missing.status_code == 422
    garbage = client.post("/api/credit-control/policies", json={**payload, "schedule": "soon"})
    assert garbage.status_code == 422


def test_credit_control_scheduler_status_requires_auth(client) -> None:
    response = client.get("/api/credit-control/scheduler")
    assert response.status_code == 401


def test_credit_control_scheduler_status_reports_disabled_scheduler(client) -> None:
    login(client)

    response = client.get("/api/credit-control/scheduler")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["running"] is True
    assert payload["cadence_seconds"] == 60
    assert payload["tick_count"] == 0


def test_credit_control_scheduler_runs_startup_tick_and_reports_snapshot() -> None:
    class _FakeCreditService:
        def __init__(self) -> None:
            self.calls = 0

        def tick(self):
            self.calls += 1
            return []

    service = _FakeCreditService()
    scheduler = CreditControlScheduler(service, cadence_seconds=60)

    scheduler.start()
    snapshot = scheduler.snapshot()
    scheduler.stop()

    assert service.calls == 1
    assert snapshot.enabled is True
    assert snapshot.cadence_seconds == 60
    assert snapshot.tick_count == 1
    assert snapshot.last_tick_started_at is not None
    assert snapshot.last_tick_error is None


def test_operation_data_refresher_collects_and_refreshes_derived_views() -> None:
    calls: list[str] = []

    class _FakeCollector:
        def collect(self, *, now=None):
            calls.append("collect")
            started_at = now or datetime.now(timezone.utc)
            return OperationalDataCollectionResult(
                samples=[],
                source_statuses=[],
                started_at=started_at,
                finished_at=started_at,
            )

    class _FakeUsageSegmentationService:
        def refresh(self, *, now=None):
            calls.append("segments")

            class _Result:
                user_count = 2

            return _Result()

    class _FakeGroupUsageService:
        def refresh(self, *, now=None):
            calls.append("groups")

            class _Result:
                group_count = 4

            return _Result()

    result = OperationalDataRefresher(
        operational_data_collector=_FakeCollector(),
        usage_segmentation_service=_FakeUsageSegmentationService(),
        group_usage_service=_FakeGroupUsageService(),
    ).refresh_before_mutation()

    assert calls == ["collect", "segments", "groups"]
    assert result.usage_segment_count == 2
    assert result.group_usage_count == 4


def test_credit_control_recurring_policy_round_trips_dashboard_schedule(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)
    policy_payload = {
        "name": "weekly group recharge",
        "enabled": True,
        "amount": 1,
        "schedule_type": "recurring",
        "schedule": "weekly 09:30",
        "timezone": "Asia/Shanghai",
        "target_scope": "group",
        "target_group_id": 11,
    }

    with patch.object(requests.Session, "request", new=backend.request):
        create = client.post("/api/credit-control/policies", json=policy_payload)
        policy = create.json()["item"]
        update = client.put(f"/api/credit-control/policies/{policy['policy_id']}", json=policy)

    assert create.status_code == 200
    assert policy["schedule_type"] == "recurring"
    assert policy["schedule"] == "weekly 09:30"
    assert policy["target_scope"] == "group"
    assert policy["target_group_id"] == 11
    assert update.status_code == 200
    assert update.json()["item"]["schedule"] == "weekly 09:30"


def test_existing_user_group_orchestration_uses_replace_group_not_allowed_groups(client) -> None:
    backend = FakeRotationSub2API()
    backend.user_api_keys[101] = [
        {"id": "key-101", "name": "primary", "group_id": 11},
        {"id": "key-101-unassigned", "name": "unassigned", "group_id": None},
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/assignments/replace-group",
            json={
                "user_id": 101,
                "email": "rotate@example.com",
                "source_group_id": 11,
                "target_group_id": 22,
                "reason": "rebalance",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "moved"
    # replace-group only moves the keys that actually sit in the old group
    # (key-101), the unassigned one is picked up by the supplemental sync.
    assert response.json()["migrated_keys"] == 2
    assert response.json()["metadata"]["supplemental_migrated_keys"] == 1
    assert backend.user_update_calls == []
    assert backend.replace_calls == [
        {"user_id": 101, "old_group_id": 11, "new_group_id": 22}
    ]
    assert backend.api_key_group_calls == [{"key_id": "key-101-unassigned", "group_id": 22}]
    assignment = main.get_flow_store().get_user_assignment(101)
    assert assignment is not None
    assert assignment.current_group_id == 22
    runs = main.get_flow_store().list_orchestration_runs()
    assert runs[0].run_kind.value == "manual"
    assert runs[0].tag == "manual_user_group"
    assert runs[0].moved[0]["user_id"] == 101


def test_existing_user_group_orchestration_requires_direct_source_group(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/assignments/replace-group",
            json={
                "user_id": 101,
                "email": "rotate@example.com",
                "source_group_id": 22,
                "target_group_id": 11,
                "reason": "wrong source",
            },
        )

    assert response.status_code == 400
    assert "direct current group" in response.json()["detail"]
    assert backend.replace_calls == []
    assert backend.user_update_calls == []


def test_existing_user_group_orchestration_assigns_user_without_source_group(client) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = None
    backend.users[0]["group_name"] = None
    backend.users[0]["group_ids"] = []
    backend.accounts.append(
        {
            "id": "acct-ungrouped",
            "name": "rotate@example.com",
            "provider": "openai",
            "platform": "openai",
            "type": "oauth",
            "status": "active",
            "available": True,
        }
    )
    backend.user_api_keys[101] = [
        {"id": "key-101-primary", "name": "primary", "group_id": None, "account_id": "acct-ungrouped"},
        {"id": "key-101-extra", "name": "extra", "group_id": None, "account_id": "acct-ungrouped"},
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/assignments/replace-group",
            json={
                "user_id": 101,
                "email": "rotate@example.com",
                "source_group_id": None,
                "target_group_id": 22,
                "reason": "assign ungrouped user",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "moved"
    assert payload["source_group_id"] is None
    assert payload["target_group_id"] == 22
    assert payload["migrated_keys"] == 2
    assert backend.replace_calls == []
    assert backend.user_update_calls == [{"user_id": 101, "allowed_groups": [22]}]
    assert backend.api_key_group_calls == [
        {"key_id": "key-101-primary", "group_id": 22},
        {"key_id": "key-101-extra", "group_id": 22},
    ]
    assert backend.update_account_calls == [
        {
            "account_id": "acct-ungrouped",
            "path": "/api/v1/admin/accounts/acct-ungrouped",
            "json": {"group_ids": [22], "confirm_mixed_channel_risk": True},
        }
    ]
    assert payload["metadata"]["bound_accounts"] == 1
    assignment = main.get_flow_store().get_user_assignment(101)
    assert assignment is not None
    assert assignment.current_group_id == 22
    assert assignment.current_group_name == "rotation-high"


def test_existing_user_group_orchestration_resyncs_resources_when_target_already_matches(client) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 22
    backend.users[0]["group_name"] = "rotation-high"
    backend.users[0]["group_ids"] = [22]
    backend.accounts.append(
        {
            "id": "acct-resync",
            "name": "rotate@example.com",
            "provider": "openai",
            "platform": "openai",
            "type": "oauth",
            "status": "active",
            "available": True,
        }
    )
    backend.user_api_keys[101] = [
        {"id": "key-101-resync", "name": "resync", "group_id": None, "account_id": "acct-resync"},
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/assignments/replace-group",
            json={
                "user_id": 101,
                "email": "rotate@example.com",
                "source_group_id": None,
                "target_group_id": 22,
                "reason": "resync ungrouped resources",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "moved"
    assert payload["source_group_id"] == 22
    assert payload["target_group_id"] == 22
    assert payload["migrated_keys"] == 1
    assert payload["metadata"]["bound_accounts"] == 1
    assert backend.replace_calls == []
    assert backend.user_update_calls == []
    assert backend.api_key_group_calls == [{"key_id": "key-101-resync", "group_id": 22}]
    assert backend.update_account_calls == [
        {
            "account_id": "acct-resync",
            "path": "/api/v1/admin/accounts/acct-resync",
            "json": {"group_ids": [22], "confirm_mixed_channel_risk": True},
        }
    ]


def test_existing_user_group_orchestration_rejects_ambiguous_allowed_groups_as_source(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 404,
            "email": "allowed-only@example.com",
            "name": "allowed-only@example.com",
            "status": "active",
            "allowed_groups": [11, 22],
        }
    ]
    now = datetime.now(timezone.utc)
    main.get_flow_store().upsert_user_assignment(
        UserGroupAssignment(
            user_id=404,
            email="allowed-only@example.com",
            current_group_id=11,
            current_group_name="rotation-low",
            assignment_mode=AssignmentMode.managed_pool,
            created_at=now,
            updated_at=now,
        )
    )
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        users_response = client.get("/orchestration/users?email=allowed-only")
        response = client.post(
            "/orchestration/assignments/replace-group",
            json={
                "user_id": 404,
                "email": "allowed-only@example.com",
                "source_group_id": 11,
                "target_group_id": 22,
                "reason": "ambiguous allowed groups are not direct current group",
            },
        )

    assert users_response.status_code == 200
    user_payload = users_response.json()["items"][0]
    assert user_payload["current_group_id"] is None
    assert user_payload["local_group_id"] == 11
    assert response.status_code == 400
    assert "direct current group" in response.json()["detail"]
    assert backend.replace_calls == []
    assert backend.user_update_calls == []


def test_existing_single_key_orchestration_uses_api_key_group_update(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/api-keys/update-group",
            json={
                "user_id": 101,
                "email": "rotate@example.com",
                "key_id": "key-101",
                "source_group_id": 11,
                "target_group_id": 22,
                "reason": "single key move",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "moved"
    assert response.json()["run_id"]
    assert response.json()["run_kind"] == "manual"
    assert response.json()["tag"] == "manual_api_key"
    assert response.json()["migrated_keys"] == 1
    assert backend.replace_calls == []
    assert backend.user_update_calls == []
    assert backend.api_key_group_calls == [{"key_id": "key-101", "group_id": 22}]
    runs = main.get_flow_store().list_orchestration_runs()
    assert runs[0].tag == "manual_api_key"
    assert runs[0].moved[0]["metadata"]["key_id"] == "key-101"


def test_group_migration_moves_direct_source_users_and_all_keys(client) -> None:
    backend = FakeRotationSub2API()
    backend.users[1]["group_id"] = 11
    backend.users[1]["group_name"] = "rotation-low"
    backend.user_api_keys[101] = [
        {"id": "key-101-source", "name": "primary", "group_id": 11},
        {"id": "key-101-extra", "name": "extra-route", "group_id": 33},
        {"id": "key-101-unassigned", "name": "unassigned", "group_id": None},
    ]
    backend.user_api_keys[202] = [
        {"id": "key-202-source", "name": "primary", "group_id": 11},
        {"id": "key-202-target", "name": "already-target", "group_id": 22},
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/groups/migrate",
            json={
                "source_group_id": 11,
                "target_group_id": 22,
                "reason": "move full group",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_kind"] == "manual"
    assert payload["tag"] == "manual_group_migration"
    assert payload["status"] == "moved"
    assert payload["config"]["mode"] == "move"
    assert payload["config"]["target_direct_user_count_before"] == 0
    assert len(payload["moved"]) == 2
    assert payload["moved"][0]["user_id"] == 202
    assert payload["moved"][1]["user_id"] == 101
    assert backend.replace_calls == [
        {"user_id": 202, "old_group_id": 11, "new_group_id": 22},
        {"user_id": 101, "old_group_id": 11, "new_group_id": 22},
    ]
    assert backend.api_key_group_calls == [
        {"key_id": "key-101-extra", "group_id": 22},
        {"key_id": "key-101-unassigned", "group_id": 22},
    ]
    assert main.get_flow_store().get_user_assignment(101).current_group_id == 22
    assert main.get_flow_store().get_user_assignment(202).current_group_id == 22
    runs = main.get_flow_store().list_orchestration_runs()
    assert runs[0].tag == "manual_group_migration"
    assert len(runs[0].moved) == 2


def test_group_migration_moves_users_with_source_group_keys_without_direct_group(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 404,
            "email": "allowed-only@example.com",
            "name": "allowed-only@example.com",
            "status": "active",
            "allowed_groups": [11, 22],
        },
        {
            "id": 505,
            "email": "target-existing@example.com",
            "name": "target-existing@example.com",
            "status": "active",
            "group_id": 22,
            "group_name": "rotation-high",
        }
    ]
    backend.user_api_keys[404] = [
        {"id": "key-404", "name": "allowed-key", "group_id": 11},
        {"id": "key-404-extra", "name": "extra-key", "group_id": 33},
    ]
    backend.user_api_keys[505] = [
        {"id": "key-505-target", "name": "target-key", "group_id": 22},
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/groups/migrate",
            json={
                "source_group_id": 11,
                "target_group_id": 22,
                "reason": "move by source key route",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "moved"
    assert payload["config"]["mode"] == "merge"
    assert payload["config"]["target_direct_user_count_before"] == 1
    assert len(payload["moved"]) == 1
    assert payload["moved"][0]["user_id"] == 404
    assert payload["moved"][0]["migrated_keys"] == 2
    assert payload["moved"][0]["metadata"]["source_match"] == "api_key_route"
    assert payload["moved"][0]["metadata"]["source_api_key_count"] == 1
    # The user has no *direct* source group but is still authorized for group 11,
    # so replace-group hands that authorization over transactionally: it moves the
    # keys sitting in group 11 and revokes only that grant, leaving no stale
    # authorization behind.
    assert next(user for user in backend.users if user["id"] == 404)["allowed_groups"] == [22]
    assert next(user for user in backend.users if user["id"] == 505)["group_id"] == 22
    assert backend.user_api_keys[505] == [
        {"id": "key-505-target", "name": "target-key", "group_id": 22},
    ]
    assert backend.replace_calls == [
        {"user_id": 404, "old_group_id": 11, "new_group_id": 22}
    ]
    assert backend.user_update_calls == []
    # key-404 travelled with replace-group; only the key routed through group 33
    # still needs an explicit move (same platform, so it may follow).
    assert backend.api_key_group_calls == [
        {"key_id": "key-404-extra", "group_id": 22},
    ]


def test_group_migration_does_not_treat_ambiguous_allowed_groups_as_direct_source(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 404,
            "email": "allowed-only@example.com",
            "name": "allowed-only@example.com",
            "status": "active",
            "allowedGroups": [{"groupId": 11}, {"groupId": 22}],
        }
    ]
    backend.user_api_keys[404] = [
        {"id": "key-404", "name": "allowed-key", "group_id": 11},
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/groups/migrate",
            json={
                "source_group_id": 11,
                "target_group_id": 22,
                "reason": "move by source key route",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "moved"
    assert payload["moved"][0]["user_id"] == 404
    assert payload["moved"][0]["metadata"]["source_match"] == "api_key_route"
    # Two groups on the same platform is a rule violation, so neither counts as
    # the direct group; the source authorization is still handed over with
    # replace-group rather than left behind.
    assert backend.replace_calls == [
        {"user_id": 404, "old_group_id": 11, "new_group_id": 22}
    ]
    assert backend.user_update_calls == []


def test_group_migration_rejects_same_source_and_target(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/groups/migrate",
            json={
                "source_group_id": 11,
                "target_group_id": 11,
            },
        )

    assert response.status_code == 400
    assert "different" in response.json()["detail"]
    assert backend.replace_calls == []


def test_existing_user_group_orchestration_rejects_cross_platform_target(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/assignments/replace-group",
            json={
                "user_id": 101,
                "email": "rotate@example.com",
                "source_group_id": 11,
                "target_group_id": 72,
                "reason": "openai user must not land in a grok group",
            },
        )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "openai" in detail and "grok" in detail
    assert backend.replace_calls == []
    assert backend.user_update_calls == []
    assert backend.api_key_group_calls == []


def test_group_migration_rejects_cross_platform_target(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/groups/migrate",
            json={
                "source_group_id": 11,
                "target_group_id": 72,
                "reason": "whole group must not cross platforms",
            },
        )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "openai" in detail and "grok" in detail
    assert backend.replace_calls == []
    assert backend.user_update_calls == []


def test_dual_platform_user_group_changes_stay_inside_their_platform(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 707,
            "email": "dual@example.com",
            "name": "dual@example.com",
            "status": "active",
            "allowed_groups": [11, 71],
        }
    ]
    backend.user_api_keys[707] = [
        {"id": "key-openai", "name": "openai-key", "group_id": 11},
        {"id": "key-grok", "name": "grok-key", "group_id": 71},
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        openai_move = client.post(
            "/orchestration/assignments/replace-group",
            json={
                "user_id": 707,
                "email": "dual@example.com",
                "source_group_id": 11,
                "target_group_id": 22,
                "reason": "openai rebalance",
            },
        )
        grok_move = client.post(
            "/orchestration/assignments/replace-group",
            json={
                "user_id": 707,
                "email": "dual@example.com",
                "source_group_id": 71,
                "target_group_id": 72,
                "reason": "grok rebalance",
            },
        )

    assert openai_move.status_code == 200
    assert openai_move.json()["status"] == "moved"
    assert grok_move.status_code == 200
    assert grok_move.json()["status"] == "moved"
    # Each change replaces only the binding of its own platform, and the upstream
    # moves only the keys that lived in the replaced group.
    assert backend.replace_calls == [
        {"user_id": 707, "old_group_id": 11, "new_group_id": 22},
        {"user_id": 707, "old_group_id": 71, "new_group_id": 72},
    ]
    assert backend.user_update_calls == []
    assert backend.api_key_group_calls == []
    assert backend.users[0]["allowed_groups"] == [22, 72]
    keys_by_id = {key["id"]: key for key in backend.user_api_keys[707]}
    assert keys_by_id["key-openai"]["group_id"] == 22
    assert keys_by_id["key-grok"]["group_id"] == 72
    store = main.get_flow_store()
    assert store.get_user_assignment(707, "openai").current_group_id == 22
    assert store.get_user_assignment(707, "grok").current_group_id == 72
    assert {
        assignment.platform for assignment in store.list_user_assignments(707)
    } == {"openai", "grok"}


def test_first_platform_assignment_leaves_other_platform_keys_alone(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 707,
            "email": "grok-only@example.com",
            "name": "grok-only@example.com",
            "status": "active",
            "group_id": 71,
            "group_name": "grok-low",
        }
    ]
    backend.user_api_keys[707] = [
        {"id": "key-grok", "name": "grok-key", "group_id": 71},
        {"id": "key-orphan", "name": "no-group-yet", "group_id": None},
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/assignments/replace-group",
            json={
                "user_id": 707,
                "email": "grok-only@example.com",
                "source_group_id": None,
                "target_group_id": 22,
                "reason": "first openai assignment",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "moved"
    assert payload["source_group_id"] is None
    # The openai slot was empty, so the user only *gains* group 22 and keeps grok.
    assert backend.replace_calls == []
    assert backend.user_update_calls == [{"user_id": 707, "allowed_groups": [71, 22]}]
    # The grok key stays where it is; only the key that serves no platform yet
    # follows the user into the new openai group.
    assert backend.api_key_group_calls == [{"key_id": "key-orphan", "group_id": 22}]
    assert payload["migrated_keys"] == 1
    store = main.get_flow_store()
    assert store.get_user_assignment(707, "openai").current_group_id == 22


def test_platform_assignment_does_not_bind_other_platform_accounts(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 707,
            "email": "openai-only@example.com",
            "name": "openai-only@example.com",
            "status": "active",
            "group_id": 11,
            "group_name": "rotation-low",
        }
    ]
    backend.user_api_keys[707] = [
        {"id": "key-openai", "name": "openai-key", "group_id": 11},
        {"id": "key-orphan", "name": "no-group-yet", "group_id": None},
    ]
    # An openai account named after the user. Nothing but the name links it to the
    # grok assignment below, and a name is not a platform.
    backend.accounts.append(
        {
            "id": "acct-x",
            "name": "openai-only@example.com",
            "email": "openai-only@example.com",
            "provider": "openai",
            "platform": "openai",
            "type": "oauth",
            "status": "active",
            "available": True,
            "group_ids": [11],
        }
    )
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/assignments/replace-group",
            json={
                "user_id": 707,
                "email": "openai-only@example.com",
                "source_group_id": None,
                "target_group_id": 72,
                "reason": "first grok assignment",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "moved"
    # The user gains the grok group and their unassigned key follows.
    assert backend.user_update_calls == [{"user_id": 707, "allowed_groups": [11, 72]}]
    assert backend.api_key_group_calls == [{"key_id": "key-orphan", "group_id": 72}]
    # The openai account must not be dragged into a grok group just because it
    # carries the user's email.
    assert backend.update_account_calls == []
    assert payload["metadata"]["bound_accounts"] == 0
    assert backend.accounts[-1]["group_ids"] == [11]


def test_platform_assignment_binds_only_the_same_platform_account(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 707,
            "email": "shared-name@example.com",
            "name": "shared-name@example.com",
            "status": "active",
        }
    ]
    backend.user_api_keys[707] = [
        {"id": "key-orphan", "name": "no-group-yet", "group_id": None},
    ]
    # Two accounts share the user's email and differ only by platform.
    backend.accounts.append(
        {
            "id": "acct-openai-twin",
            "name": "shared-name@example.com",
            "provider": "openai",
            "platform": "openai",
            "type": "oauth",
            "status": "active",
            "available": True,
            "group_ids": [],
        }
    )
    backend.accounts.append(
        {
            "id": "acct-grok-twin",
            "name": "shared-name@example.com",
            "provider": "grok",
            "platform": "grok",
            "type": "oauth",
            "status": "active",
            "available": True,
            "group_ids": [],
        }
    )
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/assignments/replace-group",
            json={
                "user_id": 707,
                "email": "shared-name@example.com",
                "source_group_id": None,
                "target_group_id": 72,
                "reason": "grok assignment",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "moved"
    assert payload["metadata"]["bound_accounts"] == 1
    # Only the grok twin is written; the openai twin is left untouched.
    assert backend.update_account_calls == [
        {
            "account_id": "acct-grok-twin",
            "path": "/api/v1/admin/accounts/acct-grok-twin",
            "json": {"group_ids": [72], "confirm_mixed_channel_risk": True},
        }
    ]


def test_platform_less_target_group_adopts_only_ungrouped_keys(client) -> None:
    backend = FakeRotationSub2API()
    # A legacy upstream group that never got a platform field.
    backend.groups.append(
        {
            "id": 88,
            "name": "legacy-no-platform",
            "type": "standard",
            "status": "active",
            "is_exclusive": True,
        }
    )
    backend.users = [
        {
            "id": 909,
            "email": "orphan-target@example.com",
            "name": "orphan-target@example.com",
            "status": "active",
        }
    ]
    backend.user_api_keys[909] = [
        {"id": "key-909-openai", "name": "already-routed", "group_id": 11},
        {"id": "key-909-orphan", "name": "no-group-yet", "group_id": None},
    ]
    backend.accounts.append(
        {
            "id": "acct-909",
            "name": "orphan-target@example.com",
            "provider": "openai",
            "platform": "openai",
            "type": "oauth",
            "status": "active",
            "available": True,
            "group_ids": [11],
        }
    )
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/assignments/replace-group",
            json={
                "user_id": 909,
                "email": "orphan-target@example.com",
                "source_group_id": None,
                "target_group_id": 88,
                "reason": "legacy group assignment",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "moved"
    assert backend.user_update_calls == [{"user_id": 909, "allowed_groups": [88]}]
    # A platform-less target cannot claim a key that already serves a platform;
    # orphan adoption is the only move that stays safe.
    assert backend.api_key_group_calls == [{"key_id": "key-909-orphan", "group_id": 88}]
    assert payload["migrated_keys"] == 1
    # Account binding needs a platform to compare against, so it is skipped whole.
    assert backend.update_account_calls == []
    assert payload["metadata"]["bound_accounts"] == 0


def test_single_api_key_group_change_rejects_cross_platform(client) -> None:
    backend = FakeRotationSub2API()
    backend.user_api_keys[101] = [
        {"id": "key-101", "name": "primary", "group_id": 11},
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/api-keys/update-group",
            json={
                "user_id": 101,
                "email": "rotate@example.com",
                "key_id": "key-101",
                "source_group_id": 11,
                "target_group_id": 72,
                "reason": "single key must not cross platforms",
            },
        )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "openai" in detail and "grok" in detail
    assert backend.api_key_group_calls == []


def test_single_api_key_group_change_reads_the_keys_real_group(client) -> None:
    backend = FakeRotationSub2API()
    # The key actually lives on grok; the caller-supplied source group is a stale
    # openai hint, which must not be what the platform check trusts.
    backend.user_api_keys[101] = [
        {"id": "key-101", "name": "primary", "group_id": 71},
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/api-keys/update-group",
            json={
                "user_id": 101,
                "email": "rotate@example.com",
                "key_id": "key-101",
                "source_group_id": 11,
                "target_group_id": 22,
            },
        )

    assert response.status_code == 400
    assert "grok" in response.json()["detail"]
    assert backend.api_key_group_calls == []


def test_single_api_key_group_change_allows_same_platform(client) -> None:
    backend = FakeRotationSub2API()
    backend.user_api_keys[101] = [
        {"id": "key-101", "name": "primary", "group_id": 71},
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/api-keys/update-group",
            json={
                "user_id": 101,
                "email": "rotate@example.com",
                "key_id": "key-101",
                "source_group_id": 71,
                "target_group_id": 72,
                "reason": "grok to grok",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "moved"
    assert backend.api_key_group_calls == [{"key_id": "key-101", "group_id": 72}]


def test_unknown_group_platform_warns_once_per_group(client, caplog) -> None:
    backend = FakeRotationSub2API()
    backend.groups.append(
        {
            "id": 88,
            "name": "legacy-no-platform",
            "type": "standard",
            "status": "active",
            "is_exclusive": True,
        }
    )
    backend.users = [
        {
            "id": 707 + index,
            "email": f"legacy{index}@example.com",
            "name": f"legacy{index}@example.com",
            "status": "active",
            "allowed_groups": [88],
        }
        for index in range(4)
    ]
    login(client)
    save_operational_snapshots(backend)

    with caplog.at_level(logging.WARNING, logger="app.services.rotation"):
        with patch.object(requests.Session, "request", new=backend.request):
            response = client.get("/orchestration/users?email=legacy")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 4
    # Four users x the same platform-less group: one warning, not four.
    warnings = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and "has no upstream platform" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert "88" in warnings[0].getMessage()


def test_multiple_groups_on_one_platform_report_no_direct_group(client, caplog) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 404,
            "email": "two-openai@example.com",
            "name": "two-openai@example.com",
            "status": "active",
            "allowed_groups": [11, 22, 71],
        }
    ]
    login(client)
    save_operational_snapshots(backend)

    with caplog.at_level(logging.WARNING, logger="app.services.rotation"):
        with patch.object(requests.Session, "request", new=backend.request):
            users_response = client.get("/orchestration/users?email=two-openai")
            rejected = client.post(
                "/orchestration/assignments/replace-group",
                json={
                    "user_id": 404,
                    "email": "two-openai@example.com",
                    "source_group_id": 11,
                    "target_group_id": 22,
                },
            )

    assert users_response.status_code == 200
    user_payload = users_response.json()["items"][0]
    # The grok slot is still a clean single binding, the openai one is not.
    assert user_payload["assignments"] == [
        {"platform": "grok", "group_id": 71, "group_name": "grok-low"}
    ]
    assert user_payload["current_group_id"] is None
    assert rejected.status_code == 400
    assert "direct current group" in rejected.json()["detail"]
    assert backend.replace_calls == []
    assert backend.user_update_calls == []
    assert any(
        "bound to 2 groups on platform openai" in record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    )


def test_orchestration_users_report_one_assignment_per_platform(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 707,
            "email": "dual@example.com",
            "name": "dual@example.com",
            "status": "active",
            "allowed_groups": [11, 71],
        }
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.get("/orchestration/users?email=dual")
        groups_response = client.get("/orchestration/groups")

    assert response.status_code == 200
    payload = response.json()["items"][0]
    assert payload["assignments"] == [
        {"platform": "grok", "group_id": 71, "group_name": "grok-low"},
        {"platform": "openai", "group_id": 11, "group_name": "rotation-low"},
    ]
    # Transitional fields keep reporting the openai binding.
    assert payload["current_group_id"] == 11
    assert payload["current_group_name"] == "rotation-low"
    assert groups_response.status_code == 200
    platforms = {
        item["group_id"]: item["platform"] for item in groups_response.json()["items"]
    }
    # The group list is no longer narrowed to the provisioning platform.
    assert platforms[11] == "openai"
    assert platforms[71] == "grok"


def test_key_transfer_moves_matching_admin_keys_and_preserves_key_value(client) -> None:
    backend = FakeRotationSub2API()
    backend.users.insert(
        0,
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "status": "active",
            "group_id": 11,
            "group_name": "rotation-low",
        },
    )
    backend.users[2]["group_ids"] = [22, 11]
    backend.user_api_keys[1] = [
        {
            "id": "9001",
            "user_id": 1,
            "key": "sk-keep-this-value",
            "name": "rotom:prod:codex:v1:idle@example.com",
            "group_id": 11,
            "quota": 50.0,
        }
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        preview = client.post(
            "/orchestration/api-keys/transfer",
            json={"dry_run": True},
        )
        execute = client.post(
            "/orchestration/api-keys/transfer",
            json={"dry_run": False},
        )

    assert preview.status_code == 200
    assert preview.json()["planned_count"] == 1
    assert preview.json()["moved_count"] == 0
    assert backend.api_key_owner_calls == [
        {
            "key_id": "9001",
            "user_id": 202,
            "group_id": 22,
            "quota": 0.0,
            "reset_quota": True,
        }
    ]
    assert execute.status_code == 200
    payload = execute.json()
    assert payload["moved_count"] == 1
    item = payload["items"][0]
    assert item["key_id"] == "9001"
    assert "key_value" not in item
    assert item["target_user_id"] == 202
    assert item["target_group_id"] == 22
    assert item["quota"] == 0.0
    assert backend.user_api_keys[202][-1]["key"] == "sk-keep-this-value"
    assert backend.user_api_keys[202][-1]["quota"] == 0.0
    runs = main.get_flow_store().list_orchestration_runs()
    assert runs[0].tag == "key_transfer"


def test_key_transfer_limits_processing_to_selected_key_ids(client) -> None:
    backend = FakeRotationSub2API()
    backend.users.insert(
        0,
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "status": "active",
            "group_id": 11,
            "group_name": "rotation-low",
        },
    )
    backend.users.extend(
        [
            {
                "id": 808,
                "email": "xuzhilin@jihuanshe.com",
                "name": "xuzhilin",
                "status": "active",
                "group_ids": [22, 11],
            },
            {
                "id": 909,
                "email": "luozhaobin@jihuanshe.com",
                "name": "luozhaobin",
                "status": "active",
                "group_id": 11,
            },
            {
                "id": 1001,
                "email": "unselected@jihuanshe.com",
                "name": "unselected",
                "status": "active",
                "group_id": 22,
            },
        ]
    )
    backend.user_api_keys[1] = [
        {
            "id": "xuzhilin",
            "user_id": 1,
            "key": "sk-xuzhilin",
            "name": "rotom:prod:codex:v1:xuzhilin@jihuanshe.com",
            "group_id": 11,
            "quota": 10.0,
        },
        {
            "id": "luozhaobin",
            "user_id": 1,
            "key": "sk-luozhaobin",
            "name": "rotom:prod:codex:v1:luozhaobin@jihuanshe.com",
            "group_id": 11,
            "quota": 10.0,
        },
        {
            "id": "unselected",
            "user_id": 1,
            "key": "sk-unselected",
            "name": "rotom:prod:codex:v1:unselected@jihuanshe.com",
            "group_id": 11,
            "quota": 10.0,
        },
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/api-keys/transfer",
            json={
                "source_user_id": 1,
                "dry_run": False,
                "key_ids": ["xuzhilin", "luozhaobin"],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["moved_count"] == 2
    assert payload["skipped_count"] == 0
    assert {item["key_id"] for item in payload["items"]} == {"xuzhilin", "luozhaobin"}
    assert [call["key_id"] for call in backend.api_key_owner_calls] == ["xuzhilin", "luozhaobin"]
    assert [key["id"] for key in backend.user_api_keys[1]] == ["unselected"]


def test_key_transfer_accepts_explicit_non_admin_source_user(client) -> None:
    backend = FakeRotationSub2API()
    backend.groups.append(
        {
            "id": 17,
            "name": "target-yuchenning",
            "type": "standard",
            "platform": "openai",
            "status": "active",
            "is_exclusive": True,
        }
    )
    backend.users = [
        {
            "id": 1,
            "email": "dev-ai@jihuanshe.com",
            "name": "AI",
            "status": "active",
            "allowed_groups": [2, 4, 6, 7, 8, 17],
        },
        {
            "id": 2,
            "email": "yuchenning@jihuanshe.com",
            "name": "yuchenning",
            "status": "active",
            "group_id": 17,
        },
    ]
    backend.user_api_keys[1] = [
        {
            "id": 36,
            "user_id": 1,
            "key": "sk-dev-ai",
            "name": "rotom:prod:shelley:v1:yuchenning@jihuanshe.com",
            "group_id": 6,
            "quota": 200.0,
        },
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/api-keys/transfer",
            json={"source_user_id": 1, "key_ids": [36], "dry_run": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "admin"
    assert payload["source_user_id"] == 1
    assert payload["planned_count"] == 1
    assert payload["items"][0]["source_user_id"] == 1
    assert payload["items"][0]["target_user_id"] == 2
    assert payload["items"][0]["target_group_id"] == 17


def test_key_transfer_replaces_group_with_target_user_first_allowed_group(client) -> None:
    backend = FakeRotationSub2API()
    backend.groups.extend(
        [
            {
                "id": 2,
                "name": "target-first",
                "type": "standard",
                "platform": "openai",
                "status": "active",
                "is_exclusive": True,
            },
            {
                "id": 17,
                "name": "target-current",
                "type": "standard",
                "platform": "openai",
                "status": "active",
                "is_exclusive": True,
            },
        ]
    )
    backend.users = [
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "status": "active",
            "group_id": 11,
        },
        {
            "id": 2,
            "email": "target@example.com",
            "name": "Target",
            "status": "active",
            "current_group_id": 17,
            "allowed_groups": [2, 11, 17],
        },
    ]
    backend.user_api_keys[1] = [
        {
            "id": "migrate-first-group",
            "user_id": 1,
            "key": "sk-migrate-first-group",
            "name": "rotom:prod:codex:v1:target@example.com",
            "group_id": 11,
            "quota": 10.0,
        },
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/api-keys/transfer",
            json={"source_user_id": 1, "key_ids": ["migrate-first-group"], "dry_run": False},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["moved_count"] == 1
    assert payload["items"][0]["target_user_id"] == 2
    assert payload["items"][0]["target_group_id"] == 2
    assert backend.api_key_owner_calls == [
        {
            "key_id": "migrate-first-group",
            "user_id": 2,
            "group_id": 2,
            "quota": 0.0,
            "reset_quota": True,
        }
    ]


def test_key_transfer_replaces_group_with_camelcase_first_allowed_group(client) -> None:
    backend = FakeRotationSub2API()
    backend.groups.extend(
        [
            {
                "id": 2,
                "name": "target-first",
                "type": "standard",
                "platform": "openai",
                "status": "active",
                "is_exclusive": True,
            },
            {
                "id": 17,
                "name": "target-current",
                "type": "standard",
                "platform": "openai",
                "status": "active",
                "is_exclusive": True,
            },
        ]
    )
    backend.users = [
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "status": "active",
            "group_id": 11,
        },
        {
            "id": 2,
            "email": "target@example.com",
            "name": "Target",
            "status": "active",
            "currentGroup": {"groupId": 17, "groupName": "target-current"},
            "allowedGroups": [
                {"groupId": 2, "groupName": "target-first"},
                {"groupId": 11, "groupName": "rotation-low"},
                {"groupId": 17, "groupName": "target-current"},
            ],
        },
    ]
    backend.user_api_keys[1] = [
        {
            "id": "migrate-camel-group",
            "user_id": 1,
            "key": "sk-migrate-camel-group",
            "name": "rotom:prod:codex:v1:target@example.com",
            "group_id": 11,
            "quota": 10.0,
        },
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/api-keys/transfer",
            json={"source_user_id": 1, "key_ids": ["migrate-camel-group"], "dry_run": False},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["moved_count"] == 1
    assert payload["items"][0]["target_user_id"] == 2
    assert payload["items"][0]["target_group_id"] == 2
    assert backend.api_key_owner_calls == [
        {
            "key_id": "migrate-camel-group",
            "user_id": 2,
            "group_id": 2,
            "quota": 0.0,
            "reset_quota": True,
        }
    ]


def test_key_transfer_ignores_source_group_when_target_first_group_differs(client) -> None:
    backend = FakeRotationSub2API()
    backend.groups.append(
        {
            "id": 17,
            "name": "target-second",
            "type": "standard",
            "platform": "openai",
            "status": "active",
            "is_exclusive": True,
        }
    )
    backend.users = [
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "status": "active",
            "group_id": 2,
        },
        {
            "id": 2,
            "email": "target@example.com",
            "name": "Target",
            "status": "active",
            "allowed_groups": [11, 17],
        },
    ]
    backend.user_api_keys[1] = [
        {
            "id": "migrate-source-group-not-target",
            "user_id": 1,
            "key": "sk-migrate-source-group-not-target",
            "name": "rotom:prod:codex:v1:target@example.com",
            "group_id": 2,
            "quota": 10.0,
        },
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/api-keys/transfer",
            json={
                "source_user_id": 1,
                "key_ids": ["migrate-source-group-not-target"],
                "dry_run": False,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["moved_count"] == 1
    assert payload["items"][0]["source_group_id"] == 2
    assert payload["items"][0]["target_group_id"] == 11
    assert backend.api_key_owner_calls == [
        {
            "key_id": "migrate-source-group-not-target",
            "user_id": 2,
            "group_id": 11,
            "quota": 0.0,
            "reset_quota": True,
        }
    ]


def test_key_transfer_selected_keys_without_source_falls_back_to_all_users(client) -> None:
    backend = FakeRotationSub2API()
    backend.groups.append(
        {
            "id": 17,
            "name": "target-yuchenning",
            "type": "standard",
            "platform": "openai",
            "status": "active",
            "is_exclusive": True,
        }
    )
    backend.users = [
        {
            "id": 1,
            "email": "dev-ai@jihuanshe.com",
            "name": "AI",
            "status": "active",
            "allowed_groups": [2, 4, 6, 7, 8, 17],
        },
        {
            "id": 2,
            "email": "yuchenning@jihuanshe.com",
            "name": "yuchenning",
            "status": "active",
            "group_id": 17,
        },
    ]
    backend.user_api_keys[1] = [
        {
            "id": 36,
            "user_id": 1,
            "key": "sk-dev-ai",
            "name": "rotom:prod:shelley:v1:yuchenning@jihuanshe.com",
            "group_id": 6,
            "quota": 200.0,
        },
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/api-keys/transfer",
            json={"key_ids": [36], "dry_run": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "all_users"
    assert payload["source_user_id"] is None
    assert payload["planned_count"] == 1
    assert payload["items"][0]["source_user_id"] == 1
    assert payload["items"][0]["target_user_id"] == 2
    assert payload["items"][0]["target_group_id"] == 17


def test_key_transfer_skips_missing_users_groups_duplicates_and_invalid_names(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "status": "active",
            "group_id": 11,
            "group_name": "rotation-low",
        },
        {
            "id": 505,
            "email": "nogroup@example.com",
            "name": "nogroup@example.com",
            "status": "active",
        },
        {
            "id": 606,
            "email": "duplicate@example.com",
            "name": "duplicate-a@example.com",
            "status": "active",
            "group_id": 11,
        },
        {
            "id": 707,
            "email": "duplicate@example.com",
            "name": "duplicate-b@example.com",
            "status": "active",
            "group_id": 22,
        },
    ]
    backend.user_api_keys[1] = [
        {
            "id": "bad-name",
            "user_id": 1,
            "key": "sk-bad-name",
            "name": "ordinary-key",
            "group_id": 11,
            "quota": 10.0,
        },
        {
            "id": "old-format",
            "user_id": 1,
            "key": "sk-old-format",
            "name": "rotom:codex:v1:idle@example.com",
            "group_id": 11,
            "quota": 10.0,
        },
        {
            "id": "missing-user",
            "user_id": 1,
            "key": "sk-missing-user",
            "name": "rotom:prod:codex:v1:missing@example.com",
            "group_id": 11,
            "quota": 10.0,
        },
        {
            "id": "no-group",
            "user_id": 1,
            "key": "sk-no-group",
            "name": "rotom:prod:codex:v1:nogroup@example.com",
            "group_id": 11,
            "quota": 10.0,
        },
        {
            "id": "duplicate-email",
            "user_id": 1,
            "key": "sk-duplicate-email",
            "name": "rotom:prod:codex:v1:duplicate@example.com",
            "group_id": 11,
            "quota": 10.0,
        },
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/api-keys/transfer",
            json={"source_user_id": 1},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["moved_count"] == 0
    assert payload["skipped_count"] == 5
    reasons = {item["key_id"]: item["reason"] for item in payload["items"]}
    assert reasons["bad-name"] == "API key name does not match the service:environment:object:version:email pattern"
    assert reasons["old-format"] == "API key name does not match the service:environment:object:version:email pattern"
    assert reasons["missing-user"] == "USER_NOT_FOUND"
    assert reasons["no-group"] == "TARGET_USER_GROUP_NOT_FOUND"
    assert reasons["duplicate-email"] == "USER_EMAIL_NOT_UNIQUE"
    assert backend.api_key_owner_calls == []


def test_key_transfer_accepts_any_service_environment_object_version_email_prefix(client) -> None:
    backend = FakeRotationSub2API()
    backend.users.insert(
        0,
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "status": "active",
            "group_id": 11,
            "group_name": "rotation-low",
        },
    )
    backend.users.extend(
        [
            {
                "id": 808,
                "email": "xuzhilin@jihuanshe.com",
                "name": "xuzhilin",
                "status": "active",
                "group_ids": [22, 11],
            },
            {
                "id": 909,
                "email": "luozhaobin@jihuanshe.com",
                "name": "luozhaobin",
                "status": "active",
                "group_id": 11,
            },
        ]
    )
    backend.user_api_keys[1] = [
        {
            "id": "xuzhilin",
            "user_id": 1,
            "key": "sk-xuzhilin",
            "name": "rotom:prod:codex:v1:xuzhilin@jihuanshe.com",
            "group_id": 11,
            "quota": 10.0,
        },
        {
            "id": "luozhaobin",
            "user_id": 1,
            "key": "sk-luozhaobin",
            "name": "svc:prod:object:v2:luozhaobin@jihuanshe.com",
            "group_id": 11,
            "quota": 10.0,
        },
        {
            "id": "invalid-email",
            "user_id": 1,
            "key": "sk-invalid-email",
            "name": "rotom:prod:codex:v1:not-email",
            "group_id": 11,
            "quota": 10.0,
        },
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/api-keys/transfer",
            json={"source_user_id": 1, "dry_run": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["key_name_pattern"] == "service:environment:object:version:email"
    assert payload["planned_count"] == 2
    assert payload["skipped_count"] == 1
    statuses = {item["key_id"]: item["status"] for item in payload["items"]}
    assert statuses["xuzhilin"] == "planned"
    assert statuses["luozhaobin"] == "planned"
    assert statuses["invalid-email"] == "skipped"
    xuzhilin_item = next(item for item in payload["items"] if item["key_id"] == "xuzhilin")
    assert xuzhilin_item["key_service"] == "rotom"
    assert xuzhilin_item["key_environment"] == "prod"
    assert xuzhilin_item["key_object"] == "codex"
    assert xuzhilin_item["key_version"] == "v1"


def test_all_user_api_keys_endpoint_aggregates_paginated_users_and_keys(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "status": "active",
            "group_id": 11,
        },
        {
            "id": 2,
            "email": "source@example.com",
            "name": "Source",
            "status": "active",
            "group_id": 22,
        },
    ]
    backend.users_page_size = 1
    backend.api_keys_page_size = 1
    backend.user_api_keys[1] = [
        {"id": "admin-a", "user_id": 1, "name": "admin-a", "group_id": 11},
        {"id": "admin-b", "user_id": 1, "name": "admin-b", "group_id": 11},
    ]
    backend.user_api_keys[2] = [
        {"id": "source-a", "user_id": 2, "name": "source-a", "group_id": 22},
    ]
    login(client)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.get("/orchestration/api-keys")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    keys_by_id = {item["key_id"]: item for item in payload["items"]}
    assert keys_by_id["admin-a"]["user_email"] == "admin@example.com"
    assert keys_by_id["source-a"]["user_id"] == 2
    assert keys_by_id["source-a"]["user_email"] == "source@example.com"


def test_key_transfer_all_users_moves_matching_keys_from_non_admin_sources(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "status": "active",
            "group_id": 11,
        },
        {
            "id": 2,
            "email": "source@example.com",
            "name": "Source",
            "status": "active",
            "group_id": 11,
        },
        {
            "id": 202,
            "email": "idle@example.com",
            "name": "idle@example.com",
            "status": "active",
            "group_id": 22,
        },
    ]
    backend.user_api_keys[1] = [
        {
            "id": "admin-key",
            "user_id": 1,
            "key": "sk-admin",
            "name": "rotom:prod:codex:v1:missing@example.com",
            "group_id": 11,
            "quota": 10.0,
        }
    ]
    backend.user_api_keys[2] = [
        {
            "id": "source-key",
            "user_id": 2,
            "key": "sk-source",
            "name": "rotom:prod:codex:v1:idle@example.com",
            "group_id": 11,
            "quota": 10.0,
        }
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/api-keys/transfer",
            json={
                "scope": "all_users",
                "dry_run": False,
                "key_ids": ["source-key"],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "all_users"
    assert payload["source_user_id"] is None
    assert payload["moved_count"] == 1
    assert payload["items"][0]["source_user_id"] == 2
    assert payload["items"][0]["target_user_id"] == 202
    assert backend.api_key_owner_calls == [
        {
            "key_id": "source-key",
            "user_id": 202,
            "group_id": 22,
            "quota": 0.0,
            "reset_quota": True,
        }
    ]
    assert [key["id"] for key in backend.user_api_keys[2]] == []
    assert backend.user_api_keys[202][-1]["key"] == "sk-source"


def test_api_token_endpoint_issues_bearer_compatible_token(client) -> None:
    login_payload = login(client)
    login_access_key = login_payload["access_key"]

    response = client.post("/auth/api-token")

    assert response.status_code == 200
    payload = response.json()
    assert payload["access_key"]
    assert payload["token_type"] == "bearer"
    assert "expires_at" not in payload
    session_response = client.get(
        "/auth/session",
        headers={"Authorization": f"Bearer {payload['access_key']}"},
    )
    assert session_response.status_code == 200
    assert session_response.json()["expires_at"] is None

    rotated_response = client.post("/auth/api-token")

    assert rotated_response.status_code == 200
    rotated_payload = rotated_response.json()
    assert rotated_payload["access_key"]
    assert rotated_payload["access_key"] != payload["access_key"]
    old_token_response = client.get(
        "/auth/session",
        headers={"Authorization": f"Bearer {payload['access_key']}"},
    )
    assert old_token_response.status_code == 401
    new_token_response = client.get(
        "/auth/session",
        headers={"Authorization": f"Bearer {rotated_payload['access_key']}"},
    )
    assert new_token_response.status_code == 200
    browser_session_response = client.get(
        "/auth/session",
        headers={"Authorization": f"Bearer {login_access_key}"},
    )
    assert browser_session_response.status_code == 200


def test_api_token_persists_across_auth_manager_restart(client) -> None:
    login_payload = login(client)
    login_access_key = login_payload["access_key"]
    token_response = client.post("/auth/api-token")

    assert token_response.status_code == 200
    access_key = token_response.json()["access_key"]

    main.get_auth_manager.cache_clear()

    persisted_token_response = client.get(
        "/auth/session",
        headers={"Authorization": f"Bearer {access_key}"},
    )
    browser_session_response = client.get(
        "/auth/session",
        headers={"Authorization": f"Bearer {login_access_key}"},
    )

    assert persisted_token_response.status_code == 200
    assert persisted_token_response.json()["expires_at"] is None
    assert browser_session_response.status_code == 401


def test_token_apikey_api_creates_key_for_matching_email_user(client) -> None:
    backend = FakeRotationSub2API()
    backend.users.insert(
        0,
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "username": "admin",
            "status": "active",
            "group_id": 11,
            "group_name": "rotation-low",
        },
    )
    backend.users.append(
        {
            "id": 303,
            "email": "target@example.com",
            "name": "Target",
            "status": "active",
            "allowed_groups": [22, 11],
        }
    )
    access_key = login(client)["access_key"]

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/api/v1/apikey",
            headers={"Authorization": f"Bearer {access_key}"},
            json={
                "action": "create",
                "name": "svc:prod:obj:v1:target@example.com",
                "quota": 250,
                "group_id": 11,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "create"
    assert payload["key_name_pattern"] == "service:environment:object:version:email"
    assert payload["fallback_to_admin"] is False
    assert payload["item"]["key_value"] == "sk-created-1"
    assert payload["item"]["user_id"] == 303
    assert payload["item"]["key_service"] == "svc"
    assert payload["item"]["key_environment"] == "prod"
    assert payload["item"]["key_object"] == "obj"
    assert payload["item"]["key_version"] == "v1"
    assert payload["item"]["target_email"] == "target@example.com"
    assert payload["item"]["group_id"] == 22
    assert backend.api_key_create_calls == [
        {
            "user_id": 303,
            "path": "/api/v1/admin/users/303/api-keys",
            "json": {"quota": 250, "name": "svc:prod:obj:v1:target@example.com", "group_id": 22},
        }
    ]


def test_token_apikey_api_creates_key_in_the_requested_platform_group(client) -> None:
    backend = FakeRotationSub2API()
    backend.users.insert(
        0,
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "username": "admin",
            "status": "active",
            "group_id": 11,
            "group_name": "rotation-low",
        },
    )
    backend.users.append(
        {
            "id": 303,
            "email": "target@example.com",
            "name": "Target",
            "status": "active",
            # Authorized on both platforms; group 71 is the grok one.
            "allowed_groups": [22, 11, 71],
        }
    )
    access_key = login(client)["access_key"]

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/api/v1/apikey",
            headers={"Authorization": f"Bearer {access_key}"},
            json={
                "action": "create",
                "name": "svc:prod:obj:v1:target@example.com",
                "platform": "grok",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["item"]["user_id"] == 303
    assert payload["item"]["group_id"] == 71
    assert backend.api_key_create_calls[0]["json"]["group_id"] == 71


def test_token_apikey_api_reports_the_platform_when_the_user_has_no_group_there(client) -> None:
    backend = FakeRotationSub2API()
    backend.users.insert(
        0,
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "username": "admin",
            "status": "active",
            "group_id": 11,
            "group_name": "rotation-low",
        },
    )
    backend.users.append(
        {
            "id": 303,
            "email": "target@example.com",
            "name": "Target",
            "status": "active",
            "allowed_groups": [22, 11],
        }
    )
    access_key = login(client)["access_key"]

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/api/v1/apikey",
            headers={"Authorization": f"Bearer {access_key}"},
            json={
                "action": "create",
                "name": "svc:prod:obj:v1:target@example.com",
                "platform": "grok",
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Target user has no available group on platform grok"
    assert backend.api_key_create_calls == []


def test_token_apikey_api_falls_back_to_admin_when_email_account_missing(client) -> None:
    backend = FakeRotationSub2API()
    backend.users.insert(
        0,
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "username": "admin",
            "status": "active",
            "group_id": 11,
            "group_name": "rotation-low",
        },
    )
    access_key = login(client)["access_key"]

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/sidecar/api/v1/apikey",
            headers={"x-access-key": access_key},
            json={
                "action": "create",
                "name": "svc:prod:obj:v1:missing@example.com",
                "options": {"quota": 100},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["fallback_to_admin"] is True
    assert payload["fallback_reason"] == "USER_NOT_FOUND"
    assert payload["item"]["user_id"] == 1
    assert payload["item"]["group_id"] == 11
    assert backend.api_key_create_calls == [
        {
            "user_id": 1,
            "path": "/api/v1/admin/users/1/api-keys",
            "json": {
                "quota": 100,
                "name": "svc:prod:obj:v1:missing@example.com",
                "group_id": 11,
            },
        }
    ]


def test_token_apikey_api_target_overrides_name_email(client) -> None:
    backend = FakeRotationSub2API()
    backend.users.insert(
        0,
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "username": "admin",
            "status": "active",
            "group_id": 11,
            "group_name": "rotation-low",
        },
    )
    backend.users.append(
        {
            "id": 404,
            "email": "forced@example.com",
            "name": "Forced Target",
            "status": "active",
            "allowed_groups": [22, 11],
        }
    )
    access_key = login(client)["access_key"]

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/api/v1/apikey",
            headers={"Authorization": f"Bearer {access_key}"},
            json={
                "action": "create",
                "name": "svc:prod:obj:v1:name@example.com",
                "target": "forced@example.com",
                "quota": 0,
                "group_id": 11,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["status"] == "ok"
    assert payload["fallback_to_admin"] is False
    assert payload["item"]["user_id"] == 404
    assert payload["item"]["target_email"] == "forced@example.com"
    assert payload["item"]["group_id"] == 22
    assert backend.api_key_create_calls == [
        {
            "user_id": 404,
            "path": "/api/v1/admin/users/404/api-keys",
            "json": {"quota": 0, "name": "svc:prod:obj:v1:name@example.com", "group_id": 22},
        }
    ]


def test_token_apikey_api_rejects_old_key_name_format_even_with_target(client) -> None:
    backend = FakeRotationSub2API()
    backend.users.insert(
        0,
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "username": "admin",
            "status": "active",
            "group_id": 11,
            "group_name": "rotation-low",
        },
    )
    backend.users.append(
        {
            "id": 404,
            "email": "forced@example.com",
            "name": "Forced Target",
            "status": "active",
            "allowed_groups": [22, 11],
        }
    )
    access_key = login(client)["access_key"]

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/api/v1/apikey",
            headers={"Authorization": f"Bearer {access_key}"},
            json={
                "action": "create",
                "name": "svc:obj:v1:name@example.com",
                "target": "forced@example.com",
                "quota": 0,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["status"] == "INVALID_KEY_NAME_FORMAT"
    assert payload["key_name_pattern"] == "service:environment:object:version:email"
    assert payload["fallback_to_admin"] is False
    assert payload["item"]["status"] == "INVALID_KEY_NAME_FORMAT"
    assert backend.api_key_create_calls == []


def test_token_apikey_api_can_randomly_select_available_user_group(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUB2API_API_KEY_GROUP_SELECTION", "random")
    get_settings.cache_clear()
    main.get_sub2api_client.cache_clear()
    main.get_rotation_service_for_upstream.cache_clear()
    main.get_api_key_automation_service.cache_clear()

    backend = FakeRotationSub2API()
    backend.users.insert(
        0,
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "username": "admin",
            "status": "active",
            "group_id": 11,
            "group_name": "rotation-low",
        },
    )
    backend.users.append(
        {
            "id": 505,
            "email": "multi@example.com",
            "name": "Multi Group",
            "status": "active",
            "allowed_groups": [22, 11],
        }
    )
    access_key = login(client)["access_key"]

    with (
        patch.object(requests.Session, "request", new=backend.request),
        patch("app.services.api_key_automation.random.choice", side_effect=lambda values: values[-1]),
    ):
        response = client.post(
            "/api/v1/apikey",
            headers={"Authorization": f"Bearer {access_key}"},
            json={
                "action": "create",
                "name": "svc:prod:obj:v1:multi@example.com",
                "quota": 0,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["item"]["user_id"] == 505
    assert payload["item"]["group_id"] == 11
    assert backend.api_key_create_calls == [
        {
            "user_id": 505,
            "path": "/api/v1/admin/users/505/api-keys",
            "json": {"quota": 0, "name": "svc:prod:obj:v1:multi@example.com", "group_id": 11},
        }
    ]


def test_token_apikey_api_forced_missing_target_returns_status_without_admin_fallback(client) -> None:
    backend = FakeRotationSub2API()
    backend.users.insert(
        0,
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "username": "admin",
            "status": "active",
            "group_id": 11,
            "group_name": "rotation-low",
        },
    )
    access_key = login(client)["access_key"]

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/api/v1/apikey",
            headers={"Authorization": f"Bearer {access_key}"},
            json={
                "action": "create",
                "name": "svc:prod:obj:v1:name@example.com",
                "target": "missing@example.com",
                "quota": 0,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["status"] == "USER_NOT_FOUND"
    assert "detail" not in payload
    assert payload["fallback_to_admin"] is False
    assert payload["item"]["target_email"] == "missing@example.com"
    assert payload["item"]["status"] == "USER_NOT_FOUND"
    assert backend.api_key_create_calls == []


def test_token_apikey_api_lists_encoded_keys_and_filters_by_email(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {"id": 1, "email": "admin@example.com", "name": "Admin", "group_id": 11},
        {"id": 2, "email": "source@example.com", "name": "Source", "group_id": 22},
    ]
    backend.user_api_keys[1] = [
        {
            "id": "admin-a",
            "user_id": 1,
            "name": "svc:prod:obj:v1:target@example.com",
            "key": "sk-secret",
            "group_id": 11,
            "quota": 10,
        },
        {
            "id": "old-format",
            "user_id": 1,
            "name": "svc:obj:v1:target@example.com",
            "key": "sk-old-secret",
            "group_id": 11,
        },
        {
            "id": "ordinary",
            "user_id": 1,
            "name": "ordinary",
            "key": "sk-secret-2",
            "group_id": 11,
        },
    ]
    backend.user_api_keys[2] = [
        {
            "id": "source-a",
            "user_id": 2,
            "name": "svc:prod:obj:v1:other@example.com",
            "key": "sk-secret-3",
            "group_id": 22,
        },
    ]
    access_key = login(client)["access_key"]

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/api/v1/apikey",
            headers={"Authorization": f"Bearer {access_key}"},
            json={"action": "list", "email": "target@example.com"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["key_name_pattern"] == "service:environment:object:version:email"
    assert payload["total"] == 1
    assert payload["items"][0]["key_id"] == "admin-a"
    assert payload["items"][0]["key_service"] == "svc"
    assert payload["items"][0]["key_environment"] == "prod"
    assert payload["items"][0]["key_object"] == "obj"
    assert payload["items"][0]["key_version"] == "v1"
    assert payload["items"][0]["target_email"] == "target@example.com"
    assert payload["items"][0]["user_email"] == "admin@example.com"
    assert payload["items"][0]["key_value"] is None


def test_token_apikey_api_requires_auth(client) -> None:
    response = client.post(
        "/api/v1/apikey",
        json={"action": "list"},
    )

    assert response.status_code == 401


def test_provisioning_ignores_managed_pool_setting_and_uses_email_group(client) -> None:
    backend = FakeRotationSub2API()

    with started_test_client() as managed_client:
        login(managed_client)
        main.get_flow_store().save_provisioning_runtime_settings(
            ProvisioningRuntimeSettings(assignment_mode=AssignmentMode.managed_pool)
        )
        main.get_flow_store().upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                pool_kind=RotationPoolKind.landing,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            start_response = managed_client.post(
                "/provision/start", json={"email": "managed@example.com"}
            )
            state = parse_qs(urlparse(start_response.json()["oauth_url"]).query)["state"][0]
            complete_response = managed_client.post(
                "/provision/oauth/complete",
                json={
                    "callback_url": (
                        f"http://localhost:1455/callback?code=managed-code&state={state}"
                    )
                },
            )

    assert start_response.status_code == 200
    start_payload = start_response.json()
    assert start_payload["group_id"] == "11"
    assert start_payload["assignment_mode"] == "managed_pool"
    assert start_payload["assignment_reason"] == "landing pool assignment"
    assert backend.create_group_calls == 0
    assert complete_response.status_code == 200
    assert backend.scheduled_test_plan_calls == [
        {"method": "GET", "account_id": "oa-1"},
        {"method": "POST", "json": {"account_id": "oa-1", **EXPECTED_DEFAULT_SCHEDULED_TEST_PLAN}},
    ]
    assert backend.create_account_payloads[0]["group_ids"] == [11]
    # A freshly created account already carries the group, so nothing re-binds it.
    assert backend.update_account_calls == []
    completed_flow = main.get_flow_store().get_by_flow_id(start_payload["flow_id"])
    assert completed_flow is not None
    assert completed_flow.user_id is None
    assert completed_flow.group_id == "11"
    assert completed_flow.assignment_mode == AssignmentMode.managed_pool


def test_provision_start_uses_first_landing_pool_group_for_new_user(client) -> None:
    backend = FakeRotationSub2API()

    with started_test_client() as managed_client:
        login(managed_client)
        store = main.get_flow_store()
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                pool_kind=RotationPoolKind.landing,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=2,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                pool_kind=RotationPoolKind.landing,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )

        with patch.object(requests.Session, "request", new=backend.request):
            start_response = managed_client.post(
                "/provision/start", json={"email": "landing-new@example.com"}
            )
            state = parse_qs(urlparse(start_response.json()["oauth_url"]).query)["state"][0]
            complete_response = managed_client.post(
                "/provision/oauth/complete",
                json={
                    "callback_url": (
                        f"http://localhost:1455/callback?code=landing-code&state={state}"
                    )
                },
            )

    assert start_response.status_code == 200
    payload = start_response.json()
    assert payload["group_id"] == "11"
    assert payload["assignment_mode"] == "managed_pool"
    assert payload["assignment_reason"] == "landing pool assignment"
    assert backend.create_group_calls == 0
    assert complete_response.status_code == 200
    assert backend.create_account_calls == 1
    assert backend.create_account_payloads[0]["group_ids"] == [11]
    # A freshly created account already carries the group, so nothing re-binds it.
    assert backend.update_account_calls == []
    completed_flow = main.get_flow_store().get_by_flow_id(payload["flow_id"])
    assert completed_flow is not None
    assert completed_flow.group_id == "11"
    assert completed_flow.assignment_mode == AssignmentMode.managed_pool
    assert completed_flow.assignment_reason == "landing pool assignment"


def test_provision_start_prefers_existing_email_group_over_landing_pool(client) -> None:
    backend = FakeRotationSub2API()
    backend.groups.append(
        {
            "id": 77,
            "name": "repeat@example.com_openai",
            "type": "standard",
            "platform": "openai",
            "status": "active",
            "is_exclusive": True,
        }
    )

    with started_test_client() as managed_client:
        login(managed_client)
        main.get_flow_store().upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                pool_kind=RotationPoolKind.landing,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            response = managed_client.post("/provision/start", json={"email": "repeat@example.com"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["group_id"] == 77
    assert payload["assignment_mode"] == "dedicated"
    assert payload["assignment_reason"] == "existing dedicated provisioning group"
    assert backend.create_group_calls == 0


def test_provision_start_reuses_existing_email_named_group(client) -> None:
    backend = FakeRotationSub2API()
    backend.groups.append(
        {
            "id": 77,
            "name": "repeat@example.com_openai",
            "type": "standard",
            "platform": "openai",
            "status": "active",
            "is_exclusive": True,
        }
    )
    login(client)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post("/provision/start", json={"email": "repeat@example.com"})

    assert response.status_code == 200
    assert response.json()["group_id"] == 77
    assert backend.create_group_calls == 0


def test_provision_start_reuses_legacy_bare_email_group_on_the_same_platform(client) -> None:
    backend = FakeRotationSub2API()
    # Pre-suffix naming: the dedicated group is just the email. It is still this
    # user's openai slot, so provisioning reuses it instead of opening a second one.
    backend.groups.append(
        {
            "id": 77,
            "name": "Repeat@Example.com",
            "type": "standard",
            "platform": "openai",
            "status": "active",
            "is_exclusive": True,
        }
    )
    login(client)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post("/provision/start", json={"email": "repeat@example.com"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["group_id"] == 77
    assert payload["assignment_reason"] == "existing dedicated provisioning group"
    assert backend.create_group_calls == 0


def test_provision_start_ignores_legacy_bare_email_group_on_another_platform(client) -> None:
    backend = FakeRotationSub2API()
    backend.groups.append(
        {
            "id": 88,
            "name": "repeat@example.com",
            "type": "standard",
            "platform": "grok",
            "status": "active",
            "is_exclusive": True,
        }
    )
    login(client)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post("/provision/start", json={"email": "repeat@example.com"})

    assert response.status_code == 200
    assert response.json()["group_id"] == 999
    assert backend.create_group_calls == 1


def test_provision_start_does_not_reuse_an_account_from_another_platform(client) -> None:
    backend = FakeRotationSub2API()
    backend.groups.append(
        {
            "id": 77,
            "name": "hijack@example.com_openai",
            "type": "standard",
            "platform": "openai",
            "status": "active",
            "is_exclusive": True,
        }
    )
    # A grok account already carries this email. Reusing it would PUT the openai
    # provisioning defaults over it and hijack a grok account.
    backend.accounts.append(
        {
            "id": "acct-grok-hijack",
            "name": "hijack@example.com",
            "email": "hijack@example.com",
            "provider": "grok",
            "platform": "grok",
            "type": "oauth",
            "status": "active",
            "group_ids": [71],
        }
    )
    login(client)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post("/provision/start", json={"email": "hijack@example.com"})

    assert response.status_code == 200
    payload = response.json()
    # No reuse: the flow falls through to the normal OAuth handoff.
    assert payload["status"] == "pending_oauth"
    assert payload["oauth_required"] is True
    assert backend.generate_auth_url_calls == 1
    # The grok account is never written to.
    assert backend.update_account_calls == []
    assert backend.accounts[-1]["platform"] == "grok"
    assert backend.accounts[-1]["group_ids"] == [71]


def test_provision_start_ignores_same_named_group_on_another_platform(client) -> None:
    backend = FakeRotationSub2API()
    # Same name, wrong platform: a name collision across platforms is not a reuse
    # candidate, and the decision is made on the platform field alone.
    backend.groups.append(
        {
            "id": 88,
            "name": "repeat@example.com_openai",
            "type": "standard",
            "platform": "grok",
            "status": "active",
            "is_exclusive": True,
        }
    )
    login(client)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post("/provision/start", json={"email": "repeat@example.com"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["group_id"] == 999
    assert payload["assignment_reason"] == "dedicated provisioning group"
    assert backend.create_group_calls == 1


def test_provision_start_landing_pool_only_considers_same_platform_groups(client) -> None:
    backend = FakeRotationSub2API()

    with started_test_client() as managed_client:
        login(managed_client)
        store = main.get_flow_store()
        # A grok group sits at the front of the landing pool; the openai
        # provisioning flow must still land on the openai pool group behind it.
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=71,
                pool_kind=RotationPoolKind.landing,
                group_name="grok-low",
                platform="grok",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                pool_kind=RotationPoolKind.landing,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=5,
            )
        )

        with patch.object(requests.Session, "request", new=backend.request):
            response = managed_client.post(
                "/provision/start", json={"email": "mixed-pool@example.com"}
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["group_id"] == "22"
    assert payload["assignment_mode"] == "managed_pool"
    assert payload["assignment_reason"] == "landing pool assignment"
    assert backend.create_group_calls == 0


def test_provision_start_configures_existing_oauth_account_without_authorization(client) -> None:
    backend = FakeRotationSub2API()
    backend.groups.append(
        {
            "id": 77,
            "name": "repeat@example.com_openai",
            "type": "standard",
            "platform": "openai",
            "status": "active",
            "is_exclusive": True,
        }
    )
    backend.accounts.append(
        {
            "id": "acct-repeat",
            "name": "repeat@example.com",
            "email": "repeat@example.com",
            "provider": "openai",
            "platform": "openai",
            "type": "oauth",
            "status": "active",
            "credentials": {
                "access_token": "keep-access",
                "refresh_token": "keep-refresh",
            },
            "extra": {"privacy_mode": "standard"},
            "group_ids": [77],
        }
    )

    login(client)
    with patch.object(requests.Session, "request", new=backend.request):
        start_response = client.post("/provision/start", json={"email": "repeat@example.com"})

    assert start_response.status_code == 200
    payload = start_response.json()
    assert payload["status"] == "completed"
    assert payload["oauth_required"] is False
    assert payload["oauth_url"] is None
    assert payload["oauth_account_id"] == "acct-repeat"
    assert payload["group_id"] == 77
    assert backend.generate_auth_url_calls == 0
    assert backend.exchange_code_calls == 0
    assert backend.create_account_calls == 0
    assert len(backend.update_account_calls) == 1
    update_payload = backend.update_account_calls[0]["json"]
    assert update_payload["name"] == "repeat@example.com"
    assert "provider" not in update_payload
    assert update_payload["platform"] == "openai"
    assert update_payload["type"] == "oauth"
    assert update_payload["group_ids"] == [77]
    assert update_payload["concurrency"] == 5
    assert update_payload["credentials"]["access_token"] == "keep-access"
    assert update_payload["credentials"]["refresh_token"] == "keep-refresh"
    assert update_payload["credentials"]["temp_unschedulable_enabled"] is True
    assert update_payload["credentials"]["temp_unschedulable_rules"] == (
        EXPECTED_TEMPORARY_UNSCHEDULABLE_RULES
    )
    assert update_payload["credentials"]["model_mapping"] == EXPECTED_MODEL_WHITELIST_MAPPING
    assert update_payload["extra"]["privacy_mode"] == "standard"
    assert update_payload["extra"]["openai_oauth_responses_websockets_v2_mode"] == "context_pool"
    stored_flow = main.get_flow_store().get_by_flow_id(payload["flow_id"])
    assert stored_flow is not None
    assert stored_flow.status.value == "completed"
    assert stored_flow.oauth_account_id == "acct-repeat"
    assert stored_flow.oauth_url is None


def test_provision_start_configures_existing_oauth_account_and_binds_missing_group(client) -> None:
    backend = FakeRotationSub2API()
    backend.groups.append(
        {
            "id": 77,
            "name": "repeat@example.com_openai",
            "type": "standard",
            "platform": "openai",
            "status": "active",
            "is_exclusive": True,
        }
    )
    backend.accounts.append(
        {
            "id": "acct-repeat",
            "name": "repeat@example.com",
            "email": "repeat@example.com",
            "provider": "openai",
            "platform": "openai",
            "type": "oauth",
            "status": "active",
            "group_ids": [11],
        }
    )

    login(client)
    with patch.object(requests.Session, "request", new=backend.request):
        start_response = client.post("/provision/start", json={"email": "repeat@example.com"})

    assert start_response.status_code == 200
    payload = start_response.json()
    assert payload["status"] == "completed"
    assert payload["oauth_required"] is False
    assert payload["oauth_url"] is None
    assert payload["oauth_account_id"] == "acct-repeat"
    assert backend.generate_auth_url_calls == 0
    assert backend.create_account_calls == 0
    # The configure PUT already unions the missing group in, so it is the only
    # write: no follow-up bind re-sending the identical group_ids.
    assert [call["path"] for call in backend.update_account_calls] == [
        "/api/v1/admin/accounts/acct-repeat",
    ]
    configure_payload = backend.update_account_calls[0]["json"]
    assert configure_payload["name"] == "repeat@example.com"
    assert configure_payload["group_ids"] == [11, 77]
    assert configure_payload["confirm_mixed_channel_risk"] is True
    # The original group survives the write instead of being silently unbound.
    assert backend.accounts[-1]["group_ids"] == [11, 77]


def test_provisioning_settings_api_updates_assignment_mode(client) -> None:
    login(client)

    initial = client.get("/api/provisioning/settings")
    saved = client.put(
        "/api/provisioning/settings",
        json={"assignment_mode": "managed_pool"},
    )
    reloaded = client.get("/api/provisioning/settings")

    assert initial.status_code == 200
    assert initial.json()["settings"]["assignment_mode"] == "dedicated"
    assert saved.status_code == 200
    assert saved.json()["settings"]["assignment_mode"] == "managed_pool"
    assert reloaded.status_code == 200
    assert reloaded.json()["settings"]["assignment_mode"] == "managed_pool"


def test_provisioning_settings_api_rejects_invalid_assignment_mode(client) -> None:
    login(client)

    response = client.put(
        "/api/provisioning/settings",
        json={"assignment_mode": "surprise_pool"},
    )

    assert response.status_code == 422


def test_auto_rotation_scheduler_status_requires_auth(client) -> None:
    response = client.get("/rotation/auto/scheduler")
    assert response.status_code == 401


def test_auto_rotation_scheduler_status_reports_disabled_scheduler(client) -> None:
    login(client)

    response = client.get("/rotation/auto/scheduler")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["running"] is True
    assert payload["cadence_seconds"] == 60
    assert payload["tick_count"] == 0


def test_auto_rotation_scheduler_reports_tick_errors() -> None:
    class _FakeRotationService:
        def run_auto_rotation(self, trigger_type):
            raise RuntimeError(f"boom: {trigger_type.value}")

    scheduler = AutoRotationScheduler(_FakeRotationService(), cadence_seconds=60)

    scheduler._tick_once()
    snapshot = scheduler.snapshot()

    assert snapshot.enabled is True
    assert snapshot.cadence_seconds == 60
    assert snapshot.tick_count == 0
    assert snapshot.last_tick_started_at is not None
    assert "boom: automatic_interval" in (snapshot.last_tick_error or "")


def test_auto_rotation_scheduler_runs_startup_tick_and_reports_snapshot() -> None:
    class _FakeRotationService:
        def __init__(self) -> None:
            self.calls = 0

        def run_auto_rotation(self, trigger_type):
            self.calls += 1
            return OrchestrationRunRecord(
                run_kind=OrchestrationRunKind.automatic,
                tag="automatic_execution",
                trigger_type=trigger_type,
                status="empty",
            )

    service = _FakeRotationService()
    scheduler = AutoRotationScheduler(service, cadence_seconds=60)

    scheduler.start()
    deadline = time.monotonic() + 1
    snapshot = scheduler.snapshot()
    while snapshot.tick_count == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
        snapshot = scheduler.snapshot()
    scheduler.stop()

    assert service.calls == 1
    assert snapshot.enabled is True
    assert snapshot.cadence_seconds == 60
    assert snapshot.tick_count == 1
    assert snapshot.last_tick_started_at is not None
    assert snapshot.last_tick_error is None


def test_app_startup_refreshes_operational_data_before_auto_rotation(
    app_env, monkeypatch
) -> None:
    calls: list[str] = []

    class _FakeOperationalDataCollector:
        def collect(self, *, now=None):
            calls.append("collect")

            class _Result:
                started_at = datetime.now(timezone.utc)
                finished_at = started_at
                error_message = None
                samples = []
                source_statuses = []

                @property
                def sampled_signal_count(self):
                    return 0

            return _Result()

    class _FakeNotificationService:
        def __init__(self) -> None:
            self.operational_data_collector = _FakeOperationalDataCollector()
            self.last_collection_result = None

        def refresh_samples(self, *, now=None):
            self.last_collection_result = self.operational_data_collector.collect(now=now)

        def operational_data_runtime_settings(self):
            return OperationalDataRuntimeSettings(enabled=True)

    class _FakeRotationService:
        def get_auto_rotation_config(self):
            return AutoRotationRuntimeConfig(enabled=True)

        def run_auto_rotation(self, trigger_type):
            calls.append("rotate")
            return OrchestrationRunRecord(
                run_kind=OrchestrationRunKind.automatic,
                tag="automatic_execution",
                trigger_type=trigger_type,
                status="empty",
            )

    def fake_notification_service():
        return _FakeNotificationService()

    def fake_rotation_service():
        return _FakeRotationService()

    fake_notification_service.cache_clear = lambda: None
    fake_rotation_service.cache_clear = lambda: None
    main.get_notification_service.cache_clear()
    main.get_rotation_service.cache_clear()
    monkeypatch.setattr(main, "get_notification_service", fake_notification_service)
    monkeypatch.setattr(main, "get_rotation_service", fake_rotation_service)

    with TestClient(main.app):
        # The refresh now runs on the startup warmup thread, so give the ordering a few
        # seconds instead of assuming it already happened before startup returned.
        deadline = time.monotonic() + 5
        while calls != ["collect", "rotate"] and time.monotonic() < deadline:
            time.sleep(0.01)

    assert calls[:2] == ["collect", "rotate"]


def test_app_startup_does_not_block_on_slow_operational_refresh(
    app_env, monkeypatch
) -> None:
    refresh_started = threading.Event()
    refresh_finished = threading.Event()
    release_refresh = threading.Event()

    class _BlockingNotificationService:
        def __init__(self) -> None:
            self.operational_data_collector = None
            self.last_collection_result = None

        def refresh_samples(self, *, now=None):
            refresh_started.set()
            release_refresh.wait(timeout=5)
            refresh_finished.set()

        def operational_data_runtime_settings(self):
            return OperationalDataRuntimeSettings(enabled=True)

    def fake_notification_service():
        return _BlockingNotificationService()

    fake_notification_service.cache_clear = lambda: None
    main.get_notification_service.cache_clear()
    monkeypatch.setattr(main, "get_notification_service", fake_notification_service)

    try:
        with TestClient(main.app) as test_client:
            assert refresh_started.wait(timeout=5)
            # Startup already returned while the slow refresh is still in flight. Uvicorn
            # binds its socket only after this point, so a blocking refresh would leave
            # port 8000 closed and every proxied request answered with 502.
            assert refresh_finished.is_set() is False
            assert test_client.get("/ping").status_code == 200
            # Schedulers are alive for the status endpoints, but auto-rotation still
            # holds its first tick until the warmup has fresh snapshots to rotate on.
            rotation_snapshot = main.app.state.auto_rotation_scheduler.snapshot()
            assert rotation_snapshot.running is True
            assert rotation_snapshot.tick_count == 0
            release_refresh.set()
    finally:
        release_refresh.set()


def test_manual_rotation_success_skip_and_failure(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)
    now = datetime.now(timezone.utc)
    store = main.get_flow_store()
    store.upsert_rotation_pool_group(
        RotationPoolGroup(
            group_id=11,
            group_name="rotation-low",
            platform="openai",
            status="active",
            is_exclusive=True,
            priority=0,
        )
    )
    store.upsert_rotation_pool_group(
        RotationPoolGroup(
            group_id=22,
            group_name="rotation-high",
            platform="openai",
            status="active",
            is_exclusive=True,
            priority=1,
        )
    )
    store.upsert_user_assignment(
        UserGroupAssignment(
            user_id=101,
            email="rotate@example.com",
            current_group_id=11,
            current_group_name="rotation-low",
            assignment_mode=AssignmentMode.managed_pool,
            created_at=now,
            updated_at=now,
        )
    )
    store.upsert_user_assignment(
        UserGroupAssignment(
            user_id=303,
            email="failure@example.com",
            current_group_id=11,
            current_group_name="rotation-low",
            assignment_mode=AssignmentMode.managed_pool,
            created_at=now,
            updated_at=now,
        )
    )

    with patch.object(requests.Session, "request", new=backend.request):
        moved = client.post("/rotation/manual", json={"user_id": 101, "target_group_id": 22})
        skipped = client.post("/rotation/manual", json={"user_id": 101, "target_group_id": 22})
        failed = client.post("/rotation/manual", json={"user_id": 303, "target_group_id": 22})

    assert moved.status_code == 200
    assert moved.json()["status"] == "moved"
    # The user owns one key and it sits in the old group, so replace-group moves
    # exactly that one.
    assert moved.json()["migrated_keys"] == 1
    assert skipped.status_code == 200
    assert skipped.json()["status"] == "skipped"
    assert "matches the current assignment" in skipped.json()["reason"]
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert "replace-group failed" in failed.json()["reason"]
    assert backend.user_update_calls == []
    assert backend.replace_calls == [
        {"user_id": 101, "old_group_id": 11, "new_group_id": 22}
    ]

    updated_assignment = store.get_user_assignment(101)
    assert updated_assignment is not None
    assert updated_assignment.current_group_id == 22
    events = store.list_rotation_events()
    assert len(events) >= 3


def test_manual_rotation_does_not_require_dynamic_rotation_pool(client) -> None:
    backend = FakeRotationSub2API()
    login(client)
    save_operational_snapshots(backend)
    now = datetime.now(timezone.utc)
    store = main.get_flow_store()
    store.upsert_user_assignment(
        UserGroupAssignment(
            user_id=101,
            email="rotate@example.com",
            current_group_id=11,
            current_group_name="rotation-low",
            assignment_mode=AssignmentMode.managed_pool,
            created_at=now,
            updated_at=now,
        )
    )

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post("/rotation/manual", json={"user_id": 101, "target_group_id": 22})

    assert response.status_code == 200
    assert response.json()["status"] == "moved"
    assert response.json()["target_group_id"] == 22
    assert backend.replace_calls == [
        {"user_id": 101, "old_group_id": 11, "new_group_id": 22}
    ]
    updated_assignment = store.get_user_assignment(101)
    assert updated_assignment is not None
    assert updated_assignment.current_group_id == 22


def test_manual_rotation_uses_refreshed_current_group(client) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 22
    backend.users[0]["group_name"] = "rotation-high"
    login(client)
    now = datetime.now(timezone.utc)
    store = main.get_flow_store()
    store.upsert_rotation_pool_group(
        RotationPoolGroup(
            group_id=11,
            group_name="rotation-low",
            platform="openai",
            status="active",
            is_exclusive=True,
            priority=0,
        )
    )
    store.upsert_rotation_pool_group(
        RotationPoolGroup(
            group_id=22,
            group_name="rotation-high",
            platform="openai",
            status="active",
            is_exclusive=True,
            priority=1,
        )
    )
    store.upsert_user_assignment(
        UserGroupAssignment(
            user_id=101,
            email="rotate@example.com",
            current_group_id=11,
            current_group_name="rotation-low",
            assignment_mode=AssignmentMode.managed_pool,
            created_at=now,
            updated_at=now,
        )
    )

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post("/rotation/manual", json={"user_id": 101, "target_group_id": 11})

    assert response.status_code == 200
    assert response.json()["status"] == "moved"
    assert backend.replace_calls == [
        {"user_id": 101, "old_group_id": 22, "new_group_id": 11}
    ]
    updated_assignment = store.get_user_assignment(101)
    assert updated_assignment is not None
    assert updated_assignment.current_group_id == 11


def test_auto_rotation_balances_usage_across_rotation_pool(
    client, monkeypatch
) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 22
    backend.users[0]["group_name"] = "rotation-high"
    backend.user_api_keys[101] = [{"id": 1, "usage_5h": 8.0, "usage_1d": 80.0, "usage_7d": 200.0}]
    backend.user_api_keys[202] = [{"id": 2, "usage_5h": 1.0, "usage_1d": 10.0, "usage_7d": 20.0}]
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config()
        save_operational_snapshots(backend)
        UsageSegmentationService(store).refresh()
        now = datetime.now(timezone.utc)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=101,
                email="busy@example.com",
                current_group_id=22,
                current_group_name="rotation-high",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=202,
                email="newbie@example.com",
                current_group_id=22,
                current_group_name="rotation-high",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            response = auto_client.post("/rotation/auto/run")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["moved"]) == 1
    assert backend.replace_calls[0]["user_id"] == 101
    assert backend.replace_calls[0]["old_group_id"] == 22
    assert backend.replace_calls[0]["new_group_id"] == 11
    assert payload["moved"][0]["usage_window"] == "5h"
    assert payload["moved"][0]["usage_value"] == 1.5
    assert payload["moved"][0]["usage_snapshot"]["usage_source"] == "usage_segmentation"
    assert payload["moved"][0]["usage_snapshot"]["segment"] == "spike"
    assert payload["moved"][0]["metadata"]["decision_type"] == "usage_balancing"
    assert "usage_loads_before" in payload["moved"][0]["metadata"]
    assert len(payload["skipped"]) == 1
    runs_response = auto_client.get("/rotation/auto/runs?limit=5")
    assert runs_response.status_code == 200
    run = runs_response.json()["items"][0]
    assert run["run_kind"] == "automatic"
    assert run["tag"] == "automatic_execution"
    assert run["status"] == "moved"
    assert len(run["moved"]) == 1
    assert run["moved"][0]["user_id"] == 101


def test_auto_rotation_execution_forces_refresh_before_using_snapshots(client) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 22
    backend.users[0]["group_name"] = "rotation-high"
    call_order: list[str] = []
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config()
        now = datetime.now(timezone.utc)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=101,
                email="busy@example.com",
                current_group_id=22,
                current_group_name="rotation-high",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )

        def tracked_request(self, method: str, url: str, json=None, params=None, timeout=None):
            path = urlparse(url).path
            if method == "GET" and path == "/api/v1/admin/users":
                call_order.append("list_users")
            if method == "POST" and path.endswith("/replace-group"):
                call_order.append("replace_group")
            return backend.request(method, url, json=json, params=params, timeout=timeout)

        with patch.object(requests.Session, "request", new=tracked_request):
            response = auto_client.post("/rotation/auto/run")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["moved"]) == 1
    assert payload["moved"][0]["user_id"] == 101
    assert call_order[0] == "list_users"
    assert "replace_group" in call_order
    assert call_order.index("list_users") < call_order.index("replace_group")


def test_auto_rotation_refreshes_usage_segments_before_execution(client) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 22
    backend.users[0]["group_name"] = "rotation-high"
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config()
        save_operational_snapshots(backend)
        now = datetime.now(timezone.utc)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=101,
                email="busy@example.com",
                current_group_id=22,
                current_group_name="rotation-high",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=202,
                email="newbie@example.com",
                current_group_id=22,
                current_group_name="rotation-high",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            response = auto_client.post("/rotation/auto/run")

    assert response.status_code == 200
    payload = response.json()
    assert payload["moved"][0]["usage_snapshot"]["usage_source"] == "usage_segmentation"


def test_auto_rotation_prefers_collected_user_usage_over_api_key_usage(client) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 22
    backend.users[0]["group_name"] = "rotation-high"
    backend.user_api_keys[101] = [{"id": 1, "usage_5h": 0.0, "usage_1d": 0.0, "usage_7d": 0.0}]
    backend.user_api_keys[202] = [{"id": 2, "usage_5h": 0.0, "usage_1d": 0.0, "usage_7d": 0.0}]
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config()
        save_operational_snapshots(backend)
        now = datetime.now(timezone.utc)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=101,
                email="busy@example.com",
                current_group_id=22,
                current_group_name="rotation-high",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=202,
                email="idle@example.com",
                current_group_id=22,
                current_group_name="rotation-high",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            response = auto_client.post("/rotation/auto/run")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["moved"]) == 1
    assert payload["moved"][0]["user_id"] == 101
    assert payload["moved"][0]["usage_value"] == 1.5
    assert payload["moved"][0]["usage_snapshot"]["usage_source"] == "usage_segmentation"
    assert backend.replace_calls == [
        {"user_id": 101, "old_group_id": 22, "new_group_id": 11}
    ]


def test_auto_rotation_uses_persisted_group_usage_for_balancing(client) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 11
    backend.users[0]["group_name"] = "rotation-low"
    backend.users[1]["group_id"] = 11
    backend.users[1]["group_name"] = "rotation-low"
    backend.usage_log_items = [
        usage_log_item(user_id=101, group_id=11, actual_cost=2.0),
        usage_log_item(user_id=202, group_id=11, actual_cost=1.0),
        usage_log_item(group_id=22, actual_cost=0.2),
    ]
    add_available_account_for_group(backend, 22)
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config()
        save_operational_snapshots(backend)
        store.save_operational_data_snapshot(
            OperationalDataSnapshot(
                source_key="group_usage",
                observed_at=datetime.now(timezone.utc),
                collected_at=datetime.now(timezone.utc),
                payload={
                    "11": {
                        "5h": {
                            "group_id": 11,
                            "window": "5h",
                            "total_actual_cost": 3.0,
                            "total_requests": 30,
                            "total_tokens": 3000,
                            "source": "usage_logs",
                        }
                    },
                    "22": {
                        "5h": {
                            "group_id": 22,
                            "window": "5h",
                            "total_actual_cost": 0.2,
                            "total_requests": 2,
                            "total_tokens": 200,
                            "source": "usage_logs",
                        }
                    },
                },
            )
        )
        UsageSegmentationService(store).refresh()
        GroupUsageService(store).refresh()
        now = datetime.now(timezone.utc)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=101,
                email="busy@example.com",
                current_group_id=11,
                current_group_name="rotation-low",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=202,
                email="idle@example.com",
                current_group_id=11,
                current_group_name="rotation-low",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            response = auto_client.post("/rotation/auto/run")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["moved"]) == 1
    assert payload["moved"][0]["source_group_id"] == 11
    assert payload["moved"][0]["target_group_id"] == "22"
    assert payload["moved"][0]["metadata"]["source_group_load_before"] == 3.0
    assert payload["moved"][0]["metadata"]["target_group_load_before"] == 0.2
    assert payload["moved"][0]["metadata"]["source_group_load_source"] == "group_usage:usage_logs"
    assert payload["moved"][0]["metadata"]["target_group_load_source"] == "group_usage:usage_logs"
    assert backend.replace_calls == [
        {"user_id": 101, "old_group_id": 11, "new_group_id": 22}
    ]


def test_auto_rotation_fails_when_target_group_missing_upstream(client) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 11
    backend.users[0]["group_name"] = "rotation-low"
    backend.users[1]["group_id"] = 11
    backend.users[1]["group_name"] = "rotation-low"
    backend.usage_log_items = [
        usage_log_item(user_id=101, group_id=11, actual_cost=1.0),
        usage_log_item(group_id=11, actual_cost=2.0),
        usage_log_item(group_id=99, actual_cost=0.1),
    ]
    add_available_account_for_group(backend, 99)
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config()
        save_operational_snapshots(backend)
        store.save_operational_data_snapshot(
            OperationalDataSnapshot(
                source_key="group_usage",
                observed_at=datetime.now(timezone.utc),
                collected_at=datetime.now(timezone.utc),
                payload={
                    "11": {
                        "5h": {
                            "group_id": 11,
                            "window": "5h",
                            "total_actual_cost": 3.0,
                            "source": "usage_logs",
                        }
                    },
                    "99": {
                        "5h": {
                            "group_id": 99,
                            "window": "5h",
                            "total_actual_cost": 0.1,
                            "source": "usage_logs",
                        }
                    },
                },
            )
        )
        GroupUsageService(store).refresh()
        now = datetime.now(timezone.utc)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=99,
                group_name="stale-target",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=101,
                email="busy@example.com",
                current_group_id=11,
                current_group_name="rotation-low",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            response = auto_client.post("/rotation/auto/run")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["moved"] == []
    assert payload["failed"][0]["target_group_id"] == "99"
    assert payload["failed"][0]["reason"] == "Target group does not exist in upstream Sub2API"
    assert backend.replace_calls == []


def test_auto_rotation_fails_when_target_group_has_no_upstream_accounts(client) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 11
    backend.users[0]["group_name"] = "rotation-low"
    backend.users[1]["group_id"] = 11
    backend.users[1]["group_name"] = "rotation-low"
    backend.usage_log_items = [
        usage_log_item(user_id=101, group_id=11, actual_cost=1.0),
        usage_log_item(group_id=11, actual_cost=2.0),
        usage_log_item(group_id=55, actual_cost=0.1),
    ]
    backend.groups.append(
        {
            "id": 55,
            "name": "empty-target",
            "type": "standard",
            "platform": "openai",
            "status": "active",
            "is_exclusive": True,
        }
    )
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config()
        save_operational_snapshots(backend)
        store.save_operational_data_snapshot(
            OperationalDataSnapshot(
                source_key="group_usage",
                observed_at=datetime.now(timezone.utc),
                collected_at=datetime.now(timezone.utc),
                payload={
                    "11": {
                        "5h": {
                            "group_id": 11,
                            "window": "5h",
                            "total_actual_cost": 3.0,
                            "source": "usage_logs",
                        }
                    },
                    "55": {
                        "5h": {
                            "group_id": 55,
                            "window": "5h",
                            "total_actual_cost": 0.1,
                            "source": "usage_logs",
                        }
                    },
                },
            )
        )
        GroupUsageService(store).refresh()
        now = datetime.now(timezone.utc)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=55,
                group_name="empty-target",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=101,
                email="busy@example.com",
                current_group_id=11,
                current_group_name="rotation-low",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            response = auto_client.post("/rotation/auto/run")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["moved"] == []
    assert payload["failed"][0]["target_group_id"] == "55"
    assert payload["failed"][0]["reason"] == "Target group has no upstream accounts"
    assert backend.replace_calls == []


def test_auto_rotation_fails_when_target_group_accounts_are_unschedulable(client) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 11
    backend.users[0]["group_name"] = "rotation-low"
    backend.users[1]["group_id"] = 11
    backend.users[1]["group_name"] = "rotation-low"
    backend.usage_log_items = [
        usage_log_item(user_id=101, group_id=11, actual_cost=1.0),
        usage_log_item(group_id=11, actual_cost=2.0),
        usage_log_item(group_id=22, actual_cost=0.1),
    ]
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config()
        save_operational_snapshots(backend)
        store.save_operational_data_snapshot(
            OperationalDataSnapshot(
                source_key="group_usage",
                observed_at=datetime.now(timezone.utc),
                collected_at=datetime.now(timezone.utc),
                payload={
                    "11": {
                        "5h": {
                            "group_id": 11,
                            "window": "5h",
                            "total_actual_cost": 3.0,
                            "source": "usage_logs",
                        }
                    },
                    "22": {
                        "5h": {
                            "group_id": 22,
                            "window": "5h",
                            "total_actual_cost": 0.1,
                            "source": "usage_logs",
                        }
                    },
                },
            )
        )
        GroupUsageService(store).refresh()
        now = datetime.now(timezone.utc)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=101,
                email="busy@example.com",
                current_group_id=11,
                current_group_name="rotation-low",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            response = auto_client.post("/rotation/auto/run")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["moved"] == []
    assert payload["failed"][0]["target_group_id"] == "22"
    assert payload["failed"][0]["reason"] == "Target group has no schedulable upstream accounts"
    assert backend.replace_calls == []


def test_auto_rotation_run_records_can_rollback_execution(client, monkeypatch) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 22
    backend.users[0]["group_name"] = "rotation-high"
    backend.user_api_keys[101] = [{"id": 1, "usage_5h": 8.0, "usage_1d": 80.0, "usage_7d": 200.0}]
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config()
        save_operational_snapshots(backend)
        now = datetime.now(timezone.utc)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=101,
                email="busy@example.com",
                current_group_id=22,
                current_group_name="rotation-high",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            executed = auto_client.post("/rotation/auto/run")
            run_id = executed.json()["run_id"]
            rollback = auto_client.post(f"/rotation/auto/runs/{run_id}/rollback")

    assert executed.status_code == 200
    assert rollback.status_code == 200
    payload = rollback.json()
    assert payload["rollback_status"] == "completed"
    assert payload["rollback_results"][0]["status"] == "moved"
    assert backend.replace_calls == [
        {"user_id": 101, "old_group_id": 22, "new_group_id": 11},
        {"user_id": 101, "old_group_id": 11, "new_group_id": 22},
    ]


def test_manual_and_preview_run_records_reject_rollback(client, monkeypatch) -> None:
    backend = FakeRotationSub2API()
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config(usage_window=AutoRotationUsageWindow.window_1d)
        save_operational_snapshots(backend)
        now = datetime.now(timezone.utc)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=101,
                email="rotate@example.com",
                current_group_id=11,
                current_group_name="rotation-low",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            manual = auto_client.post("/rotation/manual", json={"user_id": 101, "target_group_id": 22})
            preview = auto_client.post("/rotation/auto/run", json={"dry_run": True})
            manual_rollback = auto_client.post(
                f"/rotation/auto/runs/{manual.json()['run_id']}/rollback"
            )
            preview_rollback = auto_client.post(
                f"/rotation/auto/runs/{preview.json()['run_id']}/rollback"
            )

    assert manual_rollback.status_code == 400
    assert "Manual run records cannot be rolled back" in manual_rollback.json()["detail"]
    assert preview_rollback.status_code == 400
    assert "Preview run records cannot be rolled back" in preview_rollback.json()["detail"]


def test_auto_rotation_dead_band_skips_when_spread_within_epsilon(
    client, monkeypatch
) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 22
    backend.users[0]["group_name"] = "rotation-high"
    backend.user_api_keys[101] = [{"id": 1, "usage_5h": 5.0, "usage_1d": 50.0, "usage_7d": 100.0}]
    backend.user_api_keys[202] = [{"id": 2, "usage_5h": 4.0, "usage_1d": 40.0, "usage_7d": 80.0}]
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config(imbalance_epsilon=10.0)
        save_operational_snapshots(backend)
        now = datetime.now(timezone.utc)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=101,
                email="busy@example.com",
                current_group_id=22,
                current_group_name="rotation-high",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=202,
                email="newbie@example.com",
                current_group_id=11,
                current_group_name="rotation-low",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            response = auto_client.post("/rotation/auto/run")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dead_band_skipped"] is True
    assert payload["moved"] == []
    assert payload["skipped"] == []
    assert backend.replace_calls == []


def test_auto_rotation_improvement_delta_blocks_marginal_swap(
    client, monkeypatch
) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 22
    backend.users[0]["group_name"] = "rotation-high"
    backend.user_api_keys[101] = [{"id": 1, "usage_5h": 5.0, "usage_1d": 50.0, "usage_7d": 100.0}]
    backend.user_api_keys[202] = [{"id": 2, "usage_5h": 0.0, "usage_1d": 0.0, "usage_7d": 0.0}]
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config(improvement_delta=10.0)
        save_operational_snapshots(backend)
        now = datetime.now(timezone.utc)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=101,
                email="busy@example.com",
                current_group_id=22,
                current_group_name="rotation-high",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=202,
                email="newbie@example.com",
                current_group_id=22,
                current_group_name="rotation-high",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            response = auto_client.post("/rotation/auto/run")

    assert response.status_code == 200
    payload = response.json()
    assert payload["moved"] == []
    assert backend.replace_calls == []
    assert any(result["status"] == "skipped" for result in payload["skipped"])


def test_auto_rotation_dry_run_syncs_current_upstream_assignments_without_mutation(
    client, monkeypatch
) -> None:
    backend = FakeRotationSub2API()
    backend.users[1]["group_id"] = 11
    backend.users[1]["group_name"] = "rotation-low"
    add_available_account_for_group(backend, 22)
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config(usage_window=AutoRotationUsageWindow.window_1d)
        save_operational_snapshots(backend)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=33,
                pool_kind=RotationPoolKind.landing,
                group_name="public-shared",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            response = auto_client.post("/rotation/auto/run", json={"dry_run": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["synced"]["seen"] == 2
    assert payload["synced"]["synced"] == 2
    assert len(payload["planned"]) == 1
    assert payload["planned"][0]["target_group_id"] == "22"
    assert len(payload["skipped"]) == 1
    assert backend.replace_calls == []
    assert main.get_flow_store().get_user_assignment(101) is None
    assert main.get_flow_store().list_rotation_events() == []


def test_auto_rotation_runtime_config_can_be_saved_and_controls_execution(
    client, monkeypatch
) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 22
    backend.users[0]["group_name"] = "rotation-high"
    backend.user_api_keys[101] = [{"id": 1, "usage_5h": 5.0, "usage_1d": 10.0, "usage_7d": 20.0}]
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_operational_snapshots(backend)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=33,
                pool_kind=RotationPoolKind.landing,
                group_name="public-shared",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )

        saved = auto_client.put(
            "/rotation/auto/config",
            json={
                "enabled": False,
                "auto_assign_new_users": False,
                "cooldown_minutes": 7,
                "usage_window": "5h",
                "usage_thresholds": [],
                "schedule_source_group_ids": [33],
            },
        )
        with patch.object(requests.Session, "request", new=backend.request):
            preview = auto_client.post("/rotation/auto/run", json={"dry_run": True})
            blocked = auto_client.post("/rotation/auto/run")

        enabled = auto_client.put(
            "/rotation/auto/config",
            json={
                "enabled": True,
                "auto_assign_new_users": False,
                "cooldown_minutes": 7,
                "usage_window": "5h",
                "usage_thresholds": [],
                "schedule_source_group_ids": [33],
            },
        )
        with patch.object(requests.Session, "request", new=backend.request):
            executed = auto_client.post("/rotation/auto/run")

    assert saved.status_code == 200
    assert saved.json()["config"]["enabled"] is False
    assert saved.json()["config"]["auto_assign_new_users"] is False
    assert saved.json()["config"]["cooldown_minutes"] == 7
    assert saved.json()["config"]["usage_window"] == "5h"
    assert saved.json()["config"]["usage_thresholds"] == []
    assert saved.json()["config"]["schedule_source_group_ids"] == [33]
    assert preview.status_code == 200
    assert preview.json()["dry_run"] is True
    assert blocked.status_code == 400
    assert "disabled" in blocked.json()["detail"]
    assert enabled.status_code == 200
    assert enabled.json()["config"]["enabled"] is True
    assert executed.status_code == 200
    assert len(executed.json()["moved"]) == 1


def test_auto_rotation_auto_assigns_new_users_only_within_schedule_range(
    client, monkeypatch
) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 101,
            "email": "new-in-range@example.com",
            "name": "new-in-range@example.com",
            "status": "active",
            "group_id": 33,
            "group_name": "public-shared",
        },
        {
            "id": 202,
            "email": "outside-range@example.com",
            "name": "outside-range@example.com",
            "status": "active",
            "group_id": 44,
            "group_name": "subscription-dedicated",
        },
    ]
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_operational_snapshots(backend)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=33,
                pool_kind=RotationPoolKind.landing,
                group_name="public-shared",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        config_response = auto_client.put(
            "/rotation/auto/config",
            json={
                "enabled": True,
                "auto_assign_new_users": True,
                "cooldown_minutes": 0,
                "usage_window": "5h",
                "usage_thresholds": [],
                "schedule_source_group_ids": [33],
            },
        )
        with patch.object(requests.Session, "request", new=backend.request):
            preview = auto_client.post("/rotation/auto/run", json={"dry_run": True})
            executed = auto_client.post("/rotation/auto/run")

    assert config_response.status_code == 200
    assert config_response.json()["config"]["auto_assign_new_users"] is True
    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["synced"]["new_user_candidates"] == 1
    assert preview_payload["synced"]["skipped_outside_schedule_range"] == 1
    assert len(preview_payload["planned"]) == 1
    assert preview_payload["planned"][0]["user_id"] == 101
    assert preview_payload["planned"][0]["source_group_id"] == 33
    assert preview_payload["planned"][0]["target_group_id"] == "11"
    assert preview_payload["planned"][0]["usage_window"] == "5h"
    assert preview_payload["planned"][0]["metadata"]["decision_type"] == "new_user_usage_assignment"
    assert backend.replace_calls == [
        {"user_id": 101, "old_group_id": 33, "new_group_id": 11}
    ]
    assert executed.status_code == 200
    executed_payload = executed.json()
    assert executed_payload["synced"]["new_user_candidates"] == 1
    assert len(executed_payload["moved"]) == 1
    assert executed_payload["moved"][0]["user_id"] == 101


def test_auto_rotation_empty_schedule_range_does_not_auto_assign_new_users(
    client, monkeypatch
) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 101,
            "email": "new-without-range@example.com",
            "name": "new-without-range@example.com",
            "status": "active",
            "group_id": 33,
            "group_name": "public-shared",
        }
    ]
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_operational_snapshots(backend)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        config_response = auto_client.put(
            "/rotation/auto/config",
            json={
                "enabled": True,
                "auto_assign_new_users": True,
                "cooldown_minutes": 0,
                "usage_window": "5h",
                "usage_thresholds": [],
                "schedule_source_group_ids": [],
            },
        )
        with patch.object(requests.Session, "request", new=backend.request):
            preview = auto_client.post("/rotation/auto/run", json={"dry_run": True})
            executed = auto_client.post("/rotation/auto/run")

    assert config_response.status_code == 200
    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["synced"]["new_user_candidates"] == 0
    assert preview_payload["synced"]["skipped_outside_schedule_range"] == 1
    assert preview_payload["planned"] == []
    assert executed.status_code == 200
    assert executed.json()["moved"] == []
    assert backend.replace_calls == []


def test_auto_rotation_balances_inside_each_platform_only(client, monkeypatch) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 101,
            "email": "openai-busy@example.com",
            "name": "openai-busy@example.com",
            "status": "active",
            "group_id": 22,
            "group_name": "rotation-high",
        },
        {
            "id": 202,
            "email": "openai-idle@example.com",
            "name": "openai-idle@example.com",
            "status": "active",
            "group_id": 22,
            "group_name": "rotation-high",
        },
        {
            "id": 808,
            "email": "grok-busy@example.com",
            "name": "grok-busy@example.com",
            "status": "active",
            "group_id": 72,
            "group_name": "grok-high",
        },
        {
            "id": 909,
            "email": "grok-idle@example.com",
            "name": "grok-idle@example.com",
            "status": "active",
            "group_id": 72,
            "group_name": "grok-high",
        },
    ]
    backend.user_api_keys[101] = [{"id": "key-101", "group_id": 22}]
    backend.user_api_keys[202] = [{"id": "key-202", "group_id": 22}]
    backend.user_api_keys[808] = [{"id": "key-808", "group_id": 72}]
    backend.user_api_keys[909] = [{"id": "key-909", "group_id": 72}]
    # Each platform carries its own load: openai 1.5/0.2 in group 22, grok 1.0/0.1
    # in group 72, while groups 11 and 71 are idle.
    backend.usage_log_items = [
        usage_log_item(user_id=101, group_id=22, actual_cost=1.5),
        usage_log_item(user_id=202, group_id=22, actual_cost=0.2),
        usage_log_item(user_id=808, group_id=72, actual_cost=1.0),
        usage_log_item(user_id=909, group_id=72, actual_cost=0.1),
    ]
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config()
        save_operational_snapshots(backend)
        for priority, (group_id, group_name, platform) in enumerate(
            [
                (11, "rotation-low", "openai"),
                (22, "rotation-high", "openai"),
                (71, "grok-low", "grok"),
                (72, "grok-high", "grok"),
            ]
        ):
            store.upsert_rotation_pool_group(
                RotationPoolGroup(
                    group_id=group_id,
                    group_name=group_name,
                    platform=platform,
                    status="active",
                    is_exclusive=True,
                    priority=priority,
                )
            )
        with patch.object(requests.Session, "request", new=backend.request):
            response = auto_client.post("/rotation/auto/run")

    assert response.status_code == 200
    payload = response.json()
    # The busiest user of each platform moves to that platform's idle group; the
    # idle users stay put. No decision ever looks at the other platform's load.
    assert backend.replace_calls == [
        {"user_id": 101, "old_group_id": 22, "new_group_id": 11},
        {"user_id": 808, "old_group_id": 72, "new_group_id": 71},
    ]
    platform_of = {11: "openai", 22: "openai", 71: "grok", 72: "grok"}
    assert all(
        platform_of[call["old_group_id"]] == platform_of[call["new_group_id"]]
        for call in backend.replace_calls
    )
    assert {str(item["user_id"]) for item in payload["moved"]} == {"101", "808"}
    assert len(payload["skipped"]) == 2
    store = main.get_flow_store()
    assert str(store.get_user_assignment(101, "openai").current_group_id) == "11"
    assert str(store.get_user_assignment(808, "grok").current_group_id) == "71"
    assert store.get_user_assignment(101, "grok") is None


def test_auto_rotation_syncs_one_assignment_per_platform(client, monkeypatch) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 707,
            "email": "dual@example.com",
            "name": "dual@example.com",
            "status": "active",
            "allowed_groups": [11, 71],
        }
    ]
    backend.user_api_keys[707] = [
        {"id": "key-openai", "group_id": 11},
        {"id": "key-grok", "group_id": 71},
    ]
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config()
        save_operational_snapshots(backend)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=71,
                group_name="grok-low",
                platform="grok",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            response = auto_client.post("/rotation/auto/run")

    assert response.status_code == 200
    payload = response.json()
    # One user, two platform bindings, two synced assignment rows.
    assert payload["synced"]["seen"] == 1
    assert payload["synced"]["synced"] == 2
    assert payload["synced"]["skipped_multiple_groups_on_platform"] == 0
    assert backend.replace_calls == []
    store = main.get_flow_store()
    assignments = {
        assignment.platform: assignment
        for assignment in store.list_user_assignments(707)
    }
    assert set(assignments) == {"openai", "grok"}
    assert str(assignments["openai"].current_group_id) == "11"
    assert str(assignments["grok"].current_group_id) == "71"


def test_key_transfer_rejects_target_group_on_another_platform(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin",
            "status": "active",
            "group_id": 71,
        },
        {
            "id": 2,
            "email": "target@example.com",
            "name": "Target",
            "status": "active",
            "group_id": 11,
        },
    ]
    backend.user_api_keys[1] = [
        {
            "id": "grok-key",
            "user_id": 1,
            "key": "sk-grok-key",
            "name": "rotom:prod:codex:v1:target@example.com",
            "group_id": 71,
            "quota": 10.0,
        },
        {
            "id": "openai-key",
            "user_id": 1,
            "key": "sk-openai-key",
            "name": "rotom:prod:codex:v1:target@example.com",
            "group_id": 11,
            "quota": 10.0,
        },
    ]
    login(client)
    save_operational_snapshots(backend)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post(
            "/orchestration/api-keys/transfer",
            json={"source_user_id": 1, "dry_run": False},
        )

    assert response.status_code == 200
    payload = response.json()
    items = {item["key_id"]: item for item in payload["items"]}
    # The target user has no grok group, so the grok key stays with the admin.
    assert items["grok-key"]["status"] == "skipped"
    assert items["grok-key"]["reason"] == "TARGET_USER_GROUP_NOT_FOUND_ON_PLATFORM_GROK"
    assert items["grok-key"]["target_group_id"] is None
    assert items["openai-key"]["status"] == "moved"
    assert items["openai-key"]["target_group_id"] == 11
    assert backend.api_key_owner_calls == [
        {
            "key_id": "openai-key",
            "user_id": 2,
            "group_id": 11,
            "quota": 0.0,
            "reset_quota": True,
        }
    ]


def test_auto_rotation_skips_ambiguous_and_outside_pool_current_upstream_users(
    client, monkeypatch
) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 101,
            "email": "ambiguous@example.com",
            "name": "ambiguous@example.com",
            "status": "active",
            "allowed_groups": [11, 22],
        },
        {
            "id": 202,
            "email": "outside@example.com",
            "name": "outside@example.com",
            "status": "active",
            "group_id": 33,
            "group_name": "public-shared",
        },
    ]
    clear_caches()

    with started_test_client() as auto_client:
        login(auto_client)
        store = main.get_flow_store()
        save_auto_rotation_config(usage_thresholds=(10.0,))
        save_operational_snapshots(backend)
        now = datetime.now(timezone.utc)
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
                group_name="rotation-low",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=0,
            )
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=22,
                group_name="rotation-high",
                platform="openai",
                status="active",
                is_exclusive=True,
                priority=1,
            )
        )
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=101,
                email="stale@example.com",
                current_group_id=11,
                current_group_name="rotation-low",
                assignment_mode=AssignmentMode.managed_pool,
                created_at=now,
                updated_at=now,
            )
        )
        with patch.object(requests.Session, "request", new=backend.request):
            response = auto_client.post("/rotation/auto/run", json={"dry_run": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["synced"]["seen"] == 2
    assert payload["synced"]["synced"] == 0
    # Two openai groups on one user is a rule violation, counted on its own now
    # that "no group at all" and "an ambiguous platform bucket" are different
    # situations.
    assert payload["synced"]["skipped_without_current_group"] == 0
    assert payload["synced"]["skipped_multiple_groups_on_platform"] == 1
    assert payload["synced"]["skipped_outside_schedule_range"] == 1
    assert payload["synced"]["skipped_outside_pool"] == 0
    assert payload["planned"] == []
    assert payload["moved"] == []
    assert payload["skipped"] == []
    assert backend.replace_calls == []


def test_proxy_health_settings_roundtrip(client) -> None:
    login(client)

    response = client.get("/api/proxy-health/settings")
    assert response.status_code == 200
    settings = response.json()["settings"]
    assert settings["probe_interval_seconds"] == 60
    assert settings["critical_targets"] == ["openai"]

    update = {
        "enabled": True,
        "probe_interval_seconds": 30,
        "quality_check_interval_seconds": 600,
        "failure_threshold": 5,
        "recovery_threshold": 2,
        "auto_move_enabled": False,
        "critical_targets": ["openai", "anthropic"],
        "latency_threshold_ms": 8000,
    }
    response = client.put("/api/proxy-health/settings", json=update)
    assert response.status_code == 200

    response = client.get("/api/proxy-health/settings")
    settings = response.json()["settings"]
    assert settings["probe_interval_seconds"] == 30
    assert settings["failure_threshold"] == 5
    assert settings["auto_move_enabled"] is False
    assert settings["critical_targets"] == ["openai", "anthropic"]
    assert settings["latency_threshold_ms"] == 8000


def test_proxy_health_proxies_lists_upstream_with_health(client) -> None:
    login(client)

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        if path == "/api/v1/admin/proxies" and method == "GET":
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "id": 7,
                                "name": "us-node",
                                "protocol": "socks5",
                                "host": "1.2.3.4",
                                "port": 1080,
                                "status": "active",
                                "latency_ms": 90,
                            }
                        ]
                    },
                },
            )
        return FakeResponse(404, {"detail": f"unexpected {method} {path}"})

    with patch.object(requests.Session, "request", new=fake_request):
        response = client.get("/api/proxy-health/proxies")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["id"] == 7
    assert item["health"] == "unknown"


def test_proxy_health_requires_auth(client) -> None:
    response = client.get("/api/proxy-health/proxies")
    assert response.status_code == 401


def _proxy_admin_fake_request(state: dict):
    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        if path == "/api/v1/admin/proxies" and method == "GET":
            return FakeResponse(200, {"code": 0, "data": {"items": state["proxies"]}})
        if path == "/api/v1/admin/proxies" and method == "POST":
            state["created"] = json
            return FakeResponse(200, {"code": 0, "data": {"id": 9, **(json or {})}})
        if path == "/api/v1/admin/proxies/9" and method == "PUT":
            state["updated"] = json
            return FakeResponse(200, {"code": 0, "data": {"id": 9, **(json or {})}})
        if path == "/api/v1/admin/proxies/9" and method == "DELETE":
            state["deleted"] = True
            return FakeResponse(200, {"code": 0, "data": {}})
        if path == "/api/v1/admin/accounts" and method == "GET":
            return FakeResponse(200, {"code": 0, "data": {"items": []}})
        return FakeResponse(404, {"detail": f"unexpected {method} {path}"})

    return fake_request


def test_proxy_health_proxy_crud_endpoints(client) -> None:
    login(client)
    state: dict = {"proxies": []}

    with patch.object(requests.Session, "request", new=_proxy_admin_fake_request(state)):
        response = client.post(
            "/api/proxy-health/proxies",
            json={"name": "n1", "host": "1.2.3.4", "port": 1080},
        )
        assert response.status_code == 200
        assert response.json()["result"]["id"] == 9
        # Blanked credentials must be forwarded as explicit empty strings, not
        # dropped, so the upstream merge PUT can actually clear them.
        assert state["created"]["username"] == ""
        assert state["created"]["password"] == ""

        response = client.put(
            "/api/proxy-health/proxies/9",
            json={"name": "n1", "host": "1.2.3.4", "port": 1081, "username": ""},
        )
        assert response.status_code == 200
        assert state["updated"]["port"] == 1081
        assert state["updated"]["username"] == ""

        response = client.delete("/api/proxy-health/proxies/9")
        assert response.status_code == 200
        assert state.get("deleted") is True


def test_proxy_health_account_pin_endpoints(client) -> None:
    login(client)
    state: dict = {
        "proxies": [
            {"id": 9, "name": "us-node", "host": "1.2.3.4", "port": 1080, "status": "active"}
        ],
        "accounts": [{"id": 3, "name": "acct-3", "proxy_id": None}],
    }

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        if path == "/api/v1/admin/proxies" and method == "GET":
            return FakeResponse(200, {"code": 0, "data": {"items": state["proxies"]}})
        if path == "/api/v1/admin/accounts" and method == "GET":
            return FakeResponse(200, {"code": 0, "data": {"items": state["accounts"]}})
        if path == "/api/v1/admin/accounts/3" and method == "PUT":
            state["accounts"][0]["proxy_id"] = (json or {}).get("proxy_id")
            return FakeResponse(200, {"code": 0, "data": {}})
        return FakeResponse(404, {"detail": f"unexpected {method} {path}"})

    with patch.object(requests.Session, "request", new=fake_request):
        listed = client.get("/api/proxy-health/account-assignments")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["pinned_proxy_id"] is None

        pinned = client.put(
            "/api/proxy-health/account-assignments/3/pin", json={"proxy_id": "9"}
        )
        assert pinned.status_code == 200
        # Pinning moves the account onto that proxy right away.
        assert state["accounts"][0]["proxy_id"] == 9

        listed = client.get("/api/proxy-health/account-assignments")
        item = listed.json()["items"][0]
        assert item["pinned_proxy_id"] == "9"
        assert item["proxy_id"] == "9"

        released = client.delete("/api/proxy-health/account-assignments/3/pin")
        assert released.status_code == 200

        listed = client.get("/api/proxy-health/account-assignments")
        # Unpinning releases the binding but leaves the account where it is.
        assert listed.json()["items"][0]["pinned_proxy_id"] is None
        assert listed.json()["items"][0]["proxy_id"] == "9"


def test_proxy_health_pin_rejects_unknown_proxy(client) -> None:
    login(client)

    def fake_request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        if path == "/api/v1/admin/proxies" and method == "GET":
            return FakeResponse(200, {"code": 0, "data": {"items": []}})
        return FakeResponse(404, {"detail": f"unexpected {method} {path}"})

    with patch.object(requests.Session, "request", new=fake_request):
        response = client.put(
            "/api/proxy-health/account-assignments/3/pin", json={"proxy_id": "404"}
        )

    assert response.status_code >= 400


def test_proxy_health_manual_rebalance_and_runs_endpoints(client) -> None:
    login(client)
    state: dict = {"proxies": []}

    with patch.object(requests.Session, "request", new=_proxy_admin_fake_request(state)):
        response = client.post("/api/proxy-health/rebalance", json={"dry_run": False})
    assert response.status_code == 200
    run = response.json()["run"]
    assert run["status"] == "noop"
    assert run["trigger"] == "manual"

    response = client.get("/api/proxy-health/runs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 1
    assert payload["items"][0]["run_id"] == run["run_id"]

    response = client.get(f"/api/proxy-health/runs/{run['run_id']}")
    assert response.status_code == 200
    assert response.json()["run"]["run_id"] == run["run_id"]

    response = client.get("/api/proxy-health/runs/does-not-exist")
    assert response.status_code == 404


def test_proxy_health_scheduler_status_endpoint(client) -> None:
    login(client)
    response = client.get("/api/proxy-health/scheduler")
    assert response.status_code == 200
    payload = response.json()
    # conftest pre-disables the proxy-health runtime settings for tests.
    assert payload["enabled"] is False
    assert "cadence_seconds" in payload and "tick_count" in payload
