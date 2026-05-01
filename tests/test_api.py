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
from app.models.rotation import RotationPoolGroup, UserGroupAssignment

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
                "name": "rotate@example.com",
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
            },
            {
                "id": 22,
                "name": "rotation-high",
                "type": "standard",
                "platform": "openai",
                "status": "active",
                "is_exclusive": True,
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
    assert payload["user_id"] == "u-1"
    assert payload["group_id"] == "g-1"
    assert payload["oauth_redirect_uri"] == EXPECTED_REDIRECT_URI

    stored_flow = main.get_flow_store().get_by_flow_id(payload["flow_id"])
    assert stored_flow is not None
    assert stored_flow.email == "user@example.com"
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
        keys_response = client.get("/orchestration/users/101/api-keys")

    assert users_response.status_code == 200
    assert users_response.json()["total"] == 1
    assert users_response.json()["items"][0]["user_id"] == 101
    assert users_response.json()["items"][0]["current_group_id"] == 11
    assert groups_response.status_code == 200
    groups = {item["group_id"]: item for item in groups_response.json()["items"]}
    assert groups[11]["rotation_supported"] is True
    assert groups[44]["rotation_supported"] is False
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


def test_existing_user_group_orchestration_rejects_allowed_group_as_source(client) -> None:
    backend = FakeRotationSub2API()
    backend.users = [
        {
            "id": 404,
            "email": "allowed-only@example.com",
            "name": "allowed-only@example.com",
            "status": "active",
            "allowed_groups": [11],
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
                "reason": "allowed group is not direct current group",
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
    assert response.json()["migrated_keys"] == 1
    assert backend.replace_calls == []
    assert backend.set_user_group_calls == []
    assert backend.api_key_group_calls == [{"key_id": "key-101", "group_id": 22}]


def test_managed_pool_provisioning_uses_selected_pool_group(client, monkeypatch) -> None:
    backend = FakeRotationSub2API()
    monkeypatch.setenv("PROVISIONING_ASSIGNMENT_MODE", "managed_pool")
    clear_caches()

    with TestClient(main.app) as managed_client:
        login(managed_client)
        main.get_flow_store().upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=11,
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
    assert start_response.json()["group_id"] == 11
    assert backend.create_group_calls == 0
    assert complete_response.status_code == 200
    assignment = main.get_flow_store().get_user_assignment(101)
    assert assignment is not None
    assert assignment.current_group_id == 11
    assert assignment.assignment_mode == AssignmentMode.managed_pool


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


def test_auto_rotation_reorders_new_users_last_and_moves_by_usage_window(
    client, monkeypatch
) -> None:
    backend = FakeRotationSub2API()
    backend.user_api_keys[101] = [{"id": 1, "usage_5h": 5.0, "usage_1d": 10.0, "usage_7d": 20.0}]
    backend.user_api_keys[202] = []
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
    assert payload["window"] == "5h"
    assert len(payload["moved"]) == 2
    assert backend.replace_calls[0]["user_id"] == 101
    assert backend.replace_calls[1]["user_id"] == 202
    assert payload["moved"][1]["usage_snapshot"]["has_api_keys"] is False
