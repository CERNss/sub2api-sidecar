from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse
from unittest.mock import patch

import pytest
import requests
from fastapi.testclient import TestClient

import app.main as main
from app.auth import ACCESS_KEY_COOKIE_NAME
from app.clients.sub2api import Sub2APIClient
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
        ]
        self.user_api_keys: dict[int, list[dict[str, object]]] = {}
        self.users_page_size: int | None = None
        self.api_keys_page_size: int | None = None
        self.replace_calls: list[dict[str, object]] = []
        self.set_user_group_calls: list[dict[str, object]] = []
        self.api_key_group_calls: list[dict[str, object]] = []
        self.api_key_owner_calls: list[dict[str, object]] = []
        self.balance_calls: list[dict[str, object]] = []
        self.create_group_calls = 0
        self.create_account_calls = 0
        self.generate_auth_url_calls = 0
        self.exchange_code_calls = 0
        self.update_account_calls: list[dict[str, object]] = []
        self.bind_account_calls: list[dict[str, object]] = []
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
                items = self.users[start : start + page_size]
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
            return FakeResponse(200, {"code": 0, "message": "success", "data": self.users})
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
        if method == "PUT" and path == "/api/v1/admin/users/101":
            self.set_user_group_calls.append(
                {
                    "user_id": 101,
                    "group_id": json["group_id"],
                    "allowed_groups": json["allowed_groups"],
                }
            )
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
            assert json["provider"] == "openai"
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
        if method == "PUT" and path.startswith("/api/v1/admin/accounts/"):
            account_id = path.rsplit("/", 1)[-1]
            self.update_account_calls.append(
                {"account_id": account_id, "path": path, "json": dict(json or {})}
            )
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {"account_id": account_id, "name": json.get("name")},
                },
            )
        if method == "POST" and path in {
            "/api/v1/admin/groups/11/accounts",
            "/api/v1/admin/groups/22/accounts",
            "/api/v1/admin/groups/77/accounts",
            "/api/v1/admin/groups/999/accounts",
        }:
            self.bind_account_calls.append({"path": path, "json": dict(json or {})})
            return FakeResponse(200, {"code": 0, "message": "success", "data": {"ok": True}})
        if method == "POST" and path == "/api/v1/admin/users/101/replace-group":
            self.replace_calls.append(
                {
                    "user_id": 101,
                    "old_group_id": json["old_group_id"],
                    "new_group_id": json["new_group_id"],
                }
            )
            return FakeResponse(
                200,
                {"code": 0, "message": "success", "data": {"migrated_keys": 2}},
            )
        if method == "POST" and path == "/api/v1/admin/users/202/replace-group":
            self.replace_calls.append(
                {
                    "user_id": 202,
                    "old_group_id": json["old_group_id"],
                    "new_group_id": json["new_group_id"],
                }
            )
            return FakeResponse(
                200,
                {"code": 0, "message": "success", "data": {"migrated_keys": 1}},
            )
        if method == "POST" and path == "/api/v1/admin/users/303/replace-group":
            return FakeResponse(500, {"message": "boom"})
        if method == "PUT" and path.startswith("/api/v1/admin/api-keys/"):
            key_id = path.split("/")[5]
            if "user_id" in json:
                self.api_key_owner_calls.append(
                    {
                        "key_id": key_id,
                        "user_id": json["user_id"],
                        "group_id": json["group_id"],
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
                key_record["user_id"] = json["user_id"]
                key_record["group_id"] = json["group_id"]
                key_record["quota"] = json["quota"]
                target_user_id = int(json["user_id"])
                self.user_api_keys.setdefault(target_user_id, []).append(key_record)
                return FakeResponse(
                    200,
                    {
                        "code": 0,
                        "message": "success",
                        "data": {"api_key": key_record},
                    },
                )
            self.api_key_group_calls.append({"key_id": key_id, "group_id": json["group_id"]})
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

    def _api_keys_response(self, user_id: int, params=None) -> FakeResponse:
        items = self.user_api_keys.get(
            user_id,
            [
                {
                    "id": f"key-{user_id}",
                    "name": "primary",
                    "group_id": 11,
                    "group_name": "rotation-low",
                    "usage_5h": 1.0,
                    "usage_1d": 2.0,
                    "usage_7d": 3.0,
                }
            ],
        )
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
    main.get_provisioning_service.cache_clear()
    main.get_notification_service.cache_clear()
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
        users.append(
            {
                **user,
                "current_group_id": current_group_id,
                "current_group_name": current_group_name,
                "group_ids": [current_group_id] if current_group_id not in (None, "") else [],
            }
        )
    user_usage: dict[str, dict[str, dict[str, float]]] = {}
    for user in users:
        user_id = int(user["id"])
        costs = {
            101: {"5h": 1.5, "1d": 2.5, "7d": 6.0, "30d": 20.0},
            202: {"5h": 0.2, "1d": 0.5, "7d": 1.2, "30d": 4.0},
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
    if method == "PUT" and path == "/api/v1/admin/users/u-1/groups":
        return FakeResponse(200, {"success": True})
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
        assert json["provider"] == "openai"
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
    if method == "POST" and path == "/api/v1/admin/groups/g-1/accounts":
        return FakeResponse(200, {"success": True, "account_id": json["account_id"]})
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
    assert [call["path"] for call in calls] == [
        "/api/v1/admin/users",
        "/api/v1/admin/users",
        "/api/v1/admin/users/1/api-keys",
        "/api/v1/admin/users/2/api-keys",
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
    assert payload["require_oauth_only"] is False
    assert payload["messages_dispatch_model_config"] == {
        "opus_mapped_model": "gpt-5.4",
        "sonnet_mapped_model": "gpt-5.3-codex",
        "haiku_mapped_model": "gpt-5.4-mini",
        "exact_model_mappings": {},
    }


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
        oauth = client.generate_openai_auth_url(email="user@example.com", state="state-1")
        client.exchange_openai_code(
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
        result = client.configure_existing_openai_oauth_account(
            account=account,
            name="existing@example.com",
            group_id=77,
        )

    payload = calls[0]["json"]
    assert result["id"] == "acct-existing"
    assert calls[0]["method"] == "PUT"
    assert calls[0]["path"] == "/api/v1/admin/accounts/acct-existing"
    assert payload["name"] == "existing@example.com"
    assert payload["provider"] == "openai"
    assert payload["platform"] == "openai"
    assert payload["type"] == "oauth"
    assert payload["group_ids"] == [77]
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
    monkeypatch.delenv("SUB2API_BASE_URL", raising=False)
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
    monkeypatch.delenv("SUB2API_BASE_URL", raising=False)
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
    assert calls == [
        {
            "method": "GET",
            "host": "secondary-sub2api.local",
            "path": "/api/v1/admin/users",
            "api_key": "secondary-key",
            "timeout": 18,
        }
    ]

    missing_response = client.get("/orchestration/users?upstream_id=missing")
    assert missing_response.status_code == 422
    assert "Unknown Sub2API upstream_id: missing" in missing_response.json()["detail"]
    assert len(calls) == 1

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
    monkeypatch.delenv("SUB2API_BASE_URL", raising=False)
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


def test_provision_start_uses_email_as_dedicated_group_name(client) -> None:
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
    assert create_group_payloads[0]["name"] == email


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

    with TestClient(main.app) as stateless_client:
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

        with TestClient(main.app) as restarted_client:
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
    assert set(selected) == {11, 22, 33, 44}
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
    assert refresh.json()["group_count"] == 4
    assert refresh.json()["window_counts"]["5h"] >= 2
    assert groups.status_code == 200
    payload = groups.json()
    assert payload["total"] == 4
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
    assert response.json()["migrated_keys"] == 2
    assert backend.set_user_group_calls == []
    assert backend.replace_calls == [
        {"user_id": 101, "old_group_id": 11, "new_group_id": 22}
    ]
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
    assert backend.set_user_group_calls == []


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
    assert backend.set_user_group_calls == []


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
    assert backend.set_user_group_calls == []
    assert backend.api_key_group_calls == [{"key_id": "key-101", "group_id": 22}]
    runs = main.get_flow_store().list_orchestration_runs()
    assert runs[0].tag == "manual_api_key"
    assert runs[0].moved[0]["metadata"]["key_id"] == "key-101"


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
            "name": "rotom:codex:v1:idle@example.com",
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
            "name": "rotom:codex:v1:xuzhilin@jihuanshe.com",
            "group_id": 11,
            "quota": 10.0,
        },
        {
            "id": "luozhaobin",
            "user_id": 1,
            "key": "sk-luozhaobin",
            "name": "rotom:codex:v1:luozhaobin@jihuanshe.com",
            "group_id": 11,
            "quota": 10.0,
        },
        {
            "id": "unselected",
            "user_id": 1,
            "key": "sk-unselected",
            "name": "rotom:codex:v1:unselected@jihuanshe.com",
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
            "id": "missing-user",
            "user_id": 1,
            "key": "sk-missing-user",
            "name": "rotom:codex:v1:missing@example.com",
            "group_id": 11,
            "quota": 10.0,
        },
        {
            "id": "no-group",
            "user_id": 1,
            "key": "sk-no-group",
            "name": "rotom:codex:v1:nogroup@example.com",
            "group_id": 11,
            "quota": 10.0,
        },
        {
            "id": "duplicate-email",
            "user_id": 1,
            "key": "sk-duplicate-email",
            "name": "rotom:codex:v1:duplicate@example.com",
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
    assert payload["skipped_count"] == 4
    reasons = {item["key_id"]: item["reason"] for item in payload["items"]}
    assert reasons["bad-name"] == "API key name does not match the service:object:version:email pattern"
    assert reasons["missing-user"] == "USER_NOT_FOUND"
    assert reasons["no-group"] == "TARGET_USER_GROUP_NOT_FOUND"
    assert reasons["duplicate-email"] == "USER_EMAIL_NOT_UNIQUE"
    assert backend.api_key_owner_calls == []


def test_key_transfer_accepts_any_service_object_version_email_prefix(client) -> None:
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
            "name": "rotom:codex:v1:xuzhilin@jihuanshe.com",
            "group_id": 11,
            "quota": 10.0,
        },
        {
            "id": "luozhaobin",
            "user_id": 1,
            "key": "sk-luozhaobin",
            "name": "svc:object:v2:luozhaobin@jihuanshe.com",
            "group_id": 11,
            "quota": 10.0,
        },
        {
            "id": "invalid-email",
            "user_id": 1,
            "key": "sk-invalid-email",
            "name": "rotom:codex:v1:not-email",
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
    assert payload["key_name_pattern"] == "service:object:version:email"
    assert payload["planned_count"] == 2
    assert payload["skipped_count"] == 1
    statuses = {item["key_id"]: item["status"] for item in payload["items"]}
    assert statuses["xuzhilin"] == "planned"
    assert statuses["luozhaobin"] == "planned"
    assert statuses["invalid-email"] == "skipped"


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
            "name": "rotom:codex:v1:missing@example.com",
            "group_id": 11,
            "quota": 10.0,
        }
    ]
    backend.user_api_keys[2] = [
        {
            "id": "source-key",
            "user_id": 2,
            "key": "sk-source",
            "name": "rotom:codex:v1:idle@example.com",
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


def test_provisioning_ignores_managed_pool_setting_and_uses_email_group(client) -> None:
    backend = FakeRotationSub2API()

    with TestClient(main.app) as managed_client:
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
    assert start_response.json()["group_id"] == 999
    assert backend.create_group_calls == 1
    assert complete_response.status_code == 200
    completed_flow = main.get_flow_store().get_by_flow_id(start_response.json()["flow_id"])
    assert completed_flow is not None
    assert completed_flow.user_id is None
    assert completed_flow.group_id == 999
    assert completed_flow.assignment_mode == AssignmentMode.dedicated


def test_provision_start_reuses_existing_email_named_group(client) -> None:
    backend = FakeRotationSub2API()
    backend.groups.append(
        {
            "id": 77,
            "name": "repeat@example.com",
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


def test_provision_start_configures_existing_oauth_account_without_authorization(client) -> None:
    backend = FakeRotationSub2API()
    backend.groups.append(
        {
            "id": 77,
            "name": "repeat@example.com",
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
    assert update_payload["provider"] == "openai"
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
    assert backend.bind_account_calls == []
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
            "name": "repeat@example.com",
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
    assert len(backend.update_account_calls) == 1
    assert backend.create_account_calls == 0
    assert backend.bind_account_calls == [
        {
            "path": "/api/v1/admin/groups/77/accounts",
            "json": {"account_id": "acct-repeat", "account_ids": ["acct-repeat"]},
        }
    ]


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
        deadline = time.monotonic() + 1
        while calls != ["collect", "rotate"] and time.monotonic() < deadline:
            time.sleep(0.01)

    assert calls[:2] == ["collect", "rotate"]


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
    assert moved.json()["migrated_keys"] == 2
    assert skipped.status_code == 200
    assert skipped.json()["status"] == "skipped"
    assert "matches the current assignment" in skipped.json()["reason"]
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert "replace-group failed" in failed.json()["reason"]
    assert backend.set_user_group_calls == []
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


def test_auto_rotation_balances_usage_across_rotation_pool(
    client, monkeypatch
) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 22
    backend.users[0]["group_name"] = "rotation-high"
    backend.user_api_keys[101] = [{"id": 1, "usage_5h": 8.0, "usage_1d": 80.0, "usage_7d": 200.0}]
    backend.user_api_keys[202] = [{"id": 2, "usage_5h": 1.0, "usage_1d": 10.0, "usage_7d": 20.0}]
    clear_caches()

    with TestClient(main.app) as auto_client:
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


def test_auto_rotation_falls_back_to_user_usage_without_segment_record(client) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 22
    backend.users[0]["group_name"] = "rotation-high"
    clear_caches()

    with TestClient(main.app) as auto_client:
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
    assert payload["moved"][0]["usage_snapshot"]["usage_source"] == "user_usage"


def test_auto_rotation_prefers_collected_user_usage_over_api_key_usage(client) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 22
    backend.users[0]["group_name"] = "rotation-high"
    backend.user_api_keys[101] = [{"id": 1, "usage_5h": 0.0, "usage_1d": 0.0, "usage_7d": 0.0}]
    backend.user_api_keys[202] = [{"id": 2, "usage_5h": 0.0, "usage_1d": 0.0, "usage_7d": 0.0}]
    clear_caches()

    with TestClient(main.app) as auto_client:
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
    assert payload["moved"][0]["usage_snapshot"]["usage_source"] == "user_usage"
    assert backend.replace_calls == [
        {"user_id": 101, "old_group_id": 22, "new_group_id": 11}
    ]


def test_auto_rotation_uses_persisted_group_usage_for_balancing(client) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 11
    backend.users[0]["group_name"] = "rotation-low"
    backend.users[1]["group_id"] = 11
    backend.users[1]["group_name"] = "rotation-low"
    clear_caches()

    with TestClient(main.app) as auto_client:
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


def test_auto_rotation_run_records_can_rollback_execution(client, monkeypatch) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 22
    backend.users[0]["group_name"] = "rotation-high"
    backend.user_api_keys[101] = [{"id": 1, "usage_5h": 8.0, "usage_1d": 80.0, "usage_7d": 200.0}]
    clear_caches()

    with TestClient(main.app) as auto_client:
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

    with TestClient(main.app) as auto_client:
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

    with TestClient(main.app) as auto_client:
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

    with TestClient(main.app) as auto_client:
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
    clear_caches()

    with TestClient(main.app) as auto_client:
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

    with TestClient(main.app) as auto_client:
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

    with TestClient(main.app) as auto_client:
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

    with TestClient(main.app) as auto_client:
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

    with TestClient(main.app) as auto_client:
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
    assert payload["synced"]["skipped_without_current_group"] == 1
    assert payload["synced"]["skipped_outside_schedule_range"] == 1
    assert payload["synced"]["skipped_outside_pool"] == 0
    assert payload["planned"] == []
    assert payload["moved"] == []
    assert payload["skipped"] == []
    assert backend.replace_calls == []
