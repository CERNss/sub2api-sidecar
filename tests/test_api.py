from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest
import requests
from fastapi.testclient import TestClient

import app.main as main
from app.auth import ACCESS_KEY_COOKIE_NAME
from app.config import get_settings

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


def clear_caches() -> None:
    get_settings.cache_clear()
    main.get_auth_manager.cache_clear()
    main.get_flow_store.cache_clear()
    main.get_sub2api_client.cache_clear()
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


def login(client: TestClient) -> dict[str, object]:
    response = client.post("/auth/login", json=AUTH_PAYLOAD)
    assert response.status_code == 200
    return response.json()


def test_root_redirects_to_login_when_unauthenticated(client) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_contains_password_guidance(client) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert "密码会在每次服务启动时重新生成" in response.text
    assert "从服务启动日志里复制" in response.text
    assert "localhost URL 粘贴回编排页" in response.text


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
