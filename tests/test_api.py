from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest
import requests
from fastapi.testclient import TestClient

import app.main as main
from app.auth import ACCESS_KEY_COOKIE_NAME
from app.clients.sub2api import Sub2APIClient
from app.config import Sub2APIProvisioningDefaults, get_settings
from app.models.flow import AssignmentMode
from app.models.rotation import RotationPoolGroup, RotationPoolKind, UserGroupAssignment

EXPECTED_REDIRECT_URI = "http://localhost:1455/callback"
AUTH_PAYLOAD = {"username": "admin", "password": "test-admin-pass"}
EXPECTED_TEMPORARY_UNSCHEDULABLE_RULES = [
    {
        "error_code": "529",
        "duration_minutes": 60,
        "keywords": ["overloaded", "too many"],
        "description": "服务过载 - 暂停 60 分钟",
    },
    {
        "error_code": "429",
        "duration_minutes": 10,
        "keywords": ["rate limit", "too many requests"],
        "description": "触发限流 - 暂停 10 分钟",
    },
    {
        "error_code": "503",
        "duration_minutes": 30,
        "keywords": ["unavailable", "maintenance"],
        "description": "服务不可用 - 暂停 30 分钟",
    },
]


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
            },
            {
                "id": 202,
                "email": "idle@example.com",
                "name": "idle@example.com",
                "status": "active",
                "group_id": 22,
                "group_name": "rotation-high",
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
        self.replace_calls: list[dict[str, object]] = []
        self.set_user_group_calls: list[dict[str, object]] = []
        self.api_key_group_calls: list[dict[str, object]] = []
        self.create_group_calls = 0

    def request(self, method: str, url: str, json=None, params=None, timeout=None):
        path = urlparse(url).path
        if method == "GET" and path == "/api/v1/admin/groups/all":
            return FakeResponse(200, {"code": 0, "message": "success", "data": self.groups})
        if method == "GET" and path in {"/api/v1/admin/users/all", "/api/v1/admin/users"}:
            return FakeResponse(200, {"code": 0, "message": "success", "data": self.users})
        if method == "GET" and path == "/api/v1/admin/accounts":
            return FakeResponse(200, {"code": 0, "message": "success", "data": self.accounts})
        if method == "POST" and path in {"/api/v1/admin/groups", "/api/admin/groups"}:
            self.create_group_calls += 1
            return FakeResponse(
                200,
                {"code": 0, "message": "success", "data": {"id": 999, "name": json["name"]}},
            )
        if method == "POST" and path in {"/api/v1/admin/users", "/api/admin/users"}:
            return FakeResponse(
                200,
                {"code": 0, "message": "success", "data": {"id": 101, "email": json["email"]}},
            )
        if method == "PUT" and path in {"/api/v1/admin/users/101", "/api/admin/users/101/groups"}:
            self.set_user_group_calls.append(
                {
                    "user_id": 101,
                    "group_id": json["group_id"],
                    "allowed_groups": json["allowed_groups"],
                }
            )
            return FakeResponse(200, {"code": 0, "message": "success", "data": {"ok": True}})
        if method == "POST" and path in {"/api/v1/admin/openai/oauth/url", "/api/admin/openai/oauth/url"}:
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "oauth_url": (
                            "https://auth.example.com/authorize"
                            f"?client_id=sub2api-demo&state={json['state']}"
                        )
                    },
                },
            )
        if method == "POST" and path in {
            "/api/v1/admin/openai/oauth/exchange",
            "/api/admin/openai/oauth/exchange",
        }:
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
        if method == "POST" and path in {"/api/v1/admin/openai/accounts", "/api/admin/openai/accounts"}:
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {"account_id": "oa-1", "name": json["name"], "email": json["email"]},
                },
            )
        if method == "POST" and path in {
            "/api/v1/admin/groups/11/accounts",
            "/api/v1/admin/groups/22/accounts",
            "/api/admin/groups/11/accounts",
            "/api/admin/groups/22/accounts",
        }:
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
            self.api_key_group_calls.append({"key_id": key_id, "group_id": json["group_id"]})
            return FakeResponse(200, {"code": 0, "message": "success", "data": {"ok": True}})
        if method == "GET" and path == "/api/v1/admin/users/101/api-keys":
            return self._api_keys_response(101)
        if method == "GET" and path == "/api/v1/admin/users/202/api-keys":
            return self._api_keys_response(202)
        if method == "GET" and path == "/api/v1/admin/users/303/api-keys":
            return self._api_keys_response(303)
        if method == "GET" and path == "/api/v1/admin/usage/stats":
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "message": "success",
                    "data": {"total_actual_cost": 88.5, "total_requests": 10},
                },
            )
        return FakeResponse(404, {"detail": f"unexpected {method} {path}"})

    def _api_keys_response(self, user_id: int) -> FakeResponse:
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
        return FakeResponse(
            200,
            {
                "code": 0,
                "message": "success",
                "data": {
                    "items": items,
                    "total": len(items),
                    "page": 1,
                    "page_size": 1000,
                    "pages": 1,
                },
            },
        )


def clear_caches() -> None:
    get_settings.cache_clear()
    main.get_auth_manager.cache_clear()
    main.get_flow_store.cache_clear()
    main.get_sub2api_client.cache_clear()
    main.get_rotation_service.cache_clear()
    main.get_provisioning_service.cache_clear()


def fake_sub2api_request(self, method: str, url: str, json=None, params=None, timeout=None):
    path = urlparse(url).path
    if method == "POST" and path == "/api/admin/groups":
        assert json["platform"] == "openai"
        return FakeResponse(200, {"id": "g-1", "name": json["name"]})
    if method == "POST" and path == "/api/admin/users":
        return FakeResponse(200, {"id": "u-1", "email": json["email"]})
    if method == "PUT" and path == "/api/admin/users/u-1/groups":
        return FakeResponse(200, {"success": True})
    if method == "POST" and path == "/api/admin/openai/oauth/url":
        assert json["redirect_uri"] == EXPECTED_REDIRECT_URI
        return FakeResponse(
            200,
            {
                "oauth_url": (
                    "https://auth.example.com/authorize"
                    f"?client_id=sub2api-demo&state={json['state']}"
                )
            },
        )
    if method == "POST" and path == "/api/admin/openai/oauth/exchange":
        assert json["redirect_uri"] == EXPECTED_REDIRECT_URI
        return FakeResponse(
            200,
            {
                "access_token": "token-123",
                "refresh_token": "refresh-123",
                "provider_user_id": "provider-1",
            },
        )
    if method == "POST" and path == "/api/admin/openai/accounts":
        assert json["provider"] == "openai"
        assert json["platform"] == "openai"
        assert json["type"] == "oauth"
        assert json["email"] == json["name"]
        assert json["group_id"] == "g-1"
        assert json["group_ids"] == ["g-1"]
        assert json["wsmode"] == "context_pool"
        assert json["temporary_unschedulable"] is True
        assert json["temporary_unschedulable_rules"] == EXPECTED_TEMPORARY_UNSCHEDULABLE_RULES
        return FakeResponse(
            200,
            {
                "account_id": "oa-1",
                "name": json["name"],
                "email": json["email"],
            },
        )
    if method == "POST" and path == "/api/admin/groups/g-1/accounts":
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


@pytest.mark.parametrize("path", ["/orchestration/manual", "/provision", "/notifications"])
def test_operator_pages_redirect_to_login_when_unauthenticated(client, path: str) -> None:
    response = client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next={path}"


@pytest.mark.parametrize("path", ["/health", "/ping"])
def test_probe_endpoints_return_ok_without_auth(client, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ui_config_exposes_login_context_and_current_user(client) -> None:
    response = client.get("/ui/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["app_title"] == "Sub2API OpenAI OAuth 编排服务"
    assert payload["auth_username"] == "admin"
    assert payload["oauth_redirect_uri"] == EXPECTED_REDIRECT_URI
    assert payload["current_user"] is None

    login(client)
    response = client.get("/ui/config")

    assert response.status_code == 200
    assert response.json()["current_user"] == "admin"


def test_login_returns_access_key_and_sets_cookie(client) -> None:
    response = client.post("/auth/login", json=AUTH_PAYLOAD)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["username"] == "admin"
    assert payload["access_key"]
    assert response.cookies.get(ACCESS_KEY_COOKIE_NAME) == payload["access_key"]


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


def test_provision_start_persists_flow_in_sqlite_with_cookie_auth(client) -> None:
    login(client)

    with patch.object(requests.Session, "request", new=fake_sub2api_request):
        response = client.post("/provision/start", json={"email": "user@example.com"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["account_name"] == "user@example.com"
    assert payload.get("user_id") is None
    assert payload["group_id"] == "g-1"
    assert payload["oauth_redirect_uri"] == EXPECTED_REDIRECT_URI

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
        removed_landing = client.delete("/rotation/pool/groups/11?pool_kind=landing")
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


def test_rotation_pool_rejects_public_group(client) -> None:
    backend = FakeRotationSub2API()
    login(client)

    with patch.object(requests.Session, "request", new=backend.request):
        response = client.post("/rotation/pool/groups", json={"group_id": 33})

    assert response.status_code == 400
    assert "exclusive groups" in response.json()["detail"]


def test_rotation_pool_rejects_subscription_group(client) -> None:
    backend = FakeRotationSub2API()
    login(client)

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


def test_existing_user_group_orchestration_uses_replace_group_not_allowed_groups(client) -> None:
    backend = FakeRotationSub2API()
    login(client)

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


def test_managed_pool_provisioning_uses_selected_pool_group(client, monkeypatch) -> None:
    backend = FakeRotationSub2API()
    monkeypatch.setenv("PROVISIONING_ASSIGNMENT_MODE", "managed_pool")
    clear_caches()

    with TestClient(main.app) as managed_client:
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
    assert start_response.json()["group_id"] == "11"
    assert backend.create_group_calls == 0
    assert complete_response.status_code == 200
    completed_flow = main.get_flow_store().get_by_flow_id(start_response.json()["flow_id"])
    assert completed_flow is not None
    assert completed_flow.user_id is None
    assert completed_flow.group_id == "11"
    assert completed_flow.assignment_mode == AssignmentMode.managed_pool


def test_manual_rotation_success_skip_and_failure(client) -> None:
    backend = FakeRotationSub2API()
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
    monkeypatch.setenv("AUTO_ROTATION_ENABLED", "true")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_WINDOW", "5h")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_THRESHOLDS_JSON", "[]")
    clear_caches()

    with TestClient(main.app) as auto_client:
        login(auto_client)
        store = main.get_flow_store()
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
    assert backend.replace_calls[0]["new_group_id"] == "11"
    assert payload["moved"][0]["usage_window"] == "5h"
    assert payload["moved"][0]["usage_value"] == 8.0
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


def test_auto_rotation_run_records_can_rollback_execution(client, monkeypatch) -> None:
    backend = FakeRotationSub2API()
    backend.users[0]["group_id"] = 22
    backend.users[0]["group_name"] = "rotation-high"
    backend.user_api_keys[101] = [{"id": 1, "usage_5h": 8.0, "usage_1d": 80.0, "usage_7d": 200.0}]
    monkeypatch.setenv("AUTO_ROTATION_ENABLED", "true")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_WINDOW", "5h")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_THRESHOLDS_JSON", "[]")
    clear_caches()

    with TestClient(main.app) as auto_client:
        login(auto_client)
        store = main.get_flow_store()
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
        {"user_id": 101, "old_group_id": 22, "new_group_id": "11"},
        {"user_id": 101, "old_group_id": "11", "new_group_id": 22},
    ]


def test_manual_and_preview_run_records_reject_rollback(client, monkeypatch) -> None:
    backend = FakeRotationSub2API()
    monkeypatch.setenv("AUTO_ROTATION_ENABLED", "true")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_THRESHOLDS_JSON", "[]")
    clear_caches()

    with TestClient(main.app) as auto_client:
        login(auto_client)
        store = main.get_flow_store()
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
    monkeypatch.setenv("AUTO_ROTATION_ENABLED", "true")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_WINDOW", "5h")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_THRESHOLDS_JSON", "[]")
    monkeypatch.setenv("AUTO_ROTATION_IMBALANCE_EPSILON", "10.0")
    clear_caches()

    with TestClient(main.app) as auto_client:
        login(auto_client)
        store = main.get_flow_store()
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
    monkeypatch.setenv("AUTO_ROTATION_ENABLED", "true")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_WINDOW", "5h")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_THRESHOLDS_JSON", "[]")
    monkeypatch.setenv("AUTO_ROTATION_IMPROVEMENT_DELTA", "10.0")
    clear_caches()

    with TestClient(main.app) as auto_client:
        login(auto_client)
        store = main.get_flow_store()
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
    monkeypatch.setenv("AUTO_ROTATION_ENABLED", "true")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_THRESHOLDS_JSON", "[]")
    clear_caches()

    with TestClient(main.app) as auto_client:
        login(auto_client)
        store = main.get_flow_store()
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
    monkeypatch.setenv("AUTO_ROTATION_ENABLED", "false")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_WINDOW", "1d")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_THRESHOLDS_JSON", "[]")
    clear_caches()

    with TestClient(main.app) as auto_client:
        login(auto_client)
        store = main.get_flow_store()
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
    monkeypatch.setenv("AUTO_ROTATION_ENABLED", "false")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_WINDOW", "1d")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_THRESHOLDS_JSON", "[]")
    clear_caches()

    with TestClient(main.app) as auto_client:
        login(auto_client)
        store = main.get_flow_store()
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
        {"user_id": 101, "old_group_id": 33, "new_group_id": "11"}
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
    monkeypatch.setenv("AUTO_ROTATION_ENABLED", "false")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_WINDOW", "1d")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_THRESHOLDS_JSON", "[]")
    clear_caches()

    with TestClient(main.app) as auto_client:
        login(auto_client)
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
    monkeypatch.setenv("AUTO_ROTATION_ENABLED", "true")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_WINDOW", "5h")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_THRESHOLDS_JSON", "[10]")
    clear_caches()

    with TestClient(main.app) as auto_client:
        login(auto_client)
        store = main.get_flow_store()
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
