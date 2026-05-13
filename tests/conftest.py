from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.main as main
from app.config import get_settings


def clear_app_caches() -> None:
    get_settings.cache_clear()
    main.get_auth_manager.cache_clear()
    main.get_flow_store.cache_clear()
    main.get_sub2api_client.cache_clear()
    main.get_rotation_service.cache_clear()
    main.get_provisioning_service.cache_clear()
    main.get_notification_service.cache_clear()


@pytest.fixture
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    db_path = tmp_path / "sub2api-sidecar-test.db"
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "missing-config.yaml"))
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://testserver")
    monkeypatch.delenv("APP_BASE_PATH", raising=False)
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("APP_AUTH_USERNAME", "admin")
    monkeypatch.setenv("APP_AUTH_PASSWORD", "test-admin-pass")
    monkeypatch.setenv("APP_ACCESS_KEY_TTL_HOURS", "12")
    monkeypatch.setenv("SQLITE_DB_PATH", str(db_path))
    monkeypatch.setenv("GROUP_NAME_PREFIX", "openai-oauth-")
    monkeypatch.setenv("PROVISIONING_ASSIGNMENT_MODE", "dedicated")
    monkeypatch.setenv("AUTO_ROTATION_ENABLED", "false")
    monkeypatch.setenv("AUTO_ROTATION_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("AUTO_ROTATION_COOLDOWN_MINUTES", "0")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_WINDOW", "1d")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_THRESHOLDS_JSON", "[]")
    monkeypatch.setenv("SUB2API_GROUP_PLATFORM", "openai")
    monkeypatch.setenv("SUB2API_ACCOUNT_PROVIDER", "openai")
    monkeypatch.setenv("SUB2API_ACCOUNT_PLATFORM", "openai")
    monkeypatch.setenv("SUB2API_ACCOUNT_TYPE", "oauth")
    monkeypatch.setenv("SUB2API_ACCOUNT_WS_MODE", "context_pool")
    monkeypatch.setenv("SUB2API_ACCOUNT_TEMPORARY_UNSCHEDULABLE", "true")
    clear_app_caches()
    yield {"db_path": str(db_path)}
    clear_app_caches()


@pytest.fixture
def client(app_env: dict[str, str]) -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client
