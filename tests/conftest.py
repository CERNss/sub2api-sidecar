from __future__ import annotations

import sys
import os
from pathlib import Path
from urllib.parse import quote

import psycopg
import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.main as main
from app.config import get_settings
from app.models.operational_data import (
    CreditControlRuntimeSettings,
    OperationalDataRuntimeSettings,
    ProvisioningRuntimeSettings,
)
from app.stores.postgres import PostgresFlowStore


DEFAULT_TEST_POSTGRES_URL = "127.0.0.1"
DEFAULT_TEST_POSTGRES_PORT = 55432
DEFAULT_TEST_POSTGRES_USERNAME = "sub2api_sidecar"
DEFAULT_TEST_POSTGRES_NAME = "sub2api_sidecar_test"
DEFAULT_TEST_POSTGRES_PASSWORD = "sub2api_sidecar_test"


def clear_app_caches() -> None:
    get_settings.cache_clear()
    main.get_auth_manager.cache_clear()
    main.get_flow_store.cache_clear()
    main.get_sub2api_client.cache_clear()
    main.get_rotation_service.cache_clear()
    main.get_provisioning_service.cache_clear()
    main.get_notification_service.cache_clear()
    main.get_credit_control_service.cache_clear()
    main.get_usage_segmentation_service.cache_clear()


def _write_test_config(config_path: Path) -> str:
    url = os.getenv("TEST_POSTGRES_URL", DEFAULT_TEST_POSTGRES_URL)
    port = int(os.getenv("TEST_POSTGRES_PORT", str(DEFAULT_TEST_POSTGRES_PORT)))
    username = os.getenv("TEST_POSTGRES_USERNAME", DEFAULT_TEST_POSTGRES_USERNAME)
    database_name = os.getenv("TEST_POSTGRES_NAME", DEFAULT_TEST_POSTGRES_NAME)
    password = os.getenv("TEST_POSTGRES_PASSWORD", DEFAULT_TEST_POSTGRES_PASSWORD)
    config_path.write_text(
        f"""
database:
  url: {url}
  port: {port}
  username: {username}
  name: {database_name}
""".lstrip(),
        encoding="utf-8",
    )
    return password


def _database_url_from_config(config_path: Path, password: str) -> str:
    import yaml

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    database = payload["database"]
    return (
        f"postgresql://{quote(str(database['username']), safe='')}:{quote(password, safe='')}"
        f"@{database['url']}:{int(database.get('port', 5432))}/"
        f"{quote(str(database['name']), safe='')}"
    )


def _reset_postgres_database(database_url: str) -> None:
    try:
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute("DROP SCHEMA public CASCADE")
            connection.execute("CREATE SCHEMA public")
    except psycopg.OperationalError as exc:
        raise RuntimeError(
            "PostgreSQL tests require a running database. Start it with "
            "`docker compose up -d postgres` or set TEST_POSTGRES_* values."
        ) from exc


@pytest.fixture
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    config_path = tmp_path / "config.yaml"
    password = _write_test_config(config_path)
    app_database_url = _database_url_from_config(config_path, password)
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("POSTGRES_PASSWORD", password)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://testserver")
    monkeypatch.delenv("APP_BASE_PATH", raising=False)
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("APP_AUTH_USERNAME", "admin")
    monkeypatch.setenv("APP_AUTH_PASSWORD", "test-admin-pass")
    monkeypatch.setenv("APP_ACCESS_KEY_TTL_HOURS", "12")
    monkeypatch.delenv("PROVISIONING_ASSIGNMENT_MODE", raising=False)
    monkeypatch.setenv("SUB2API_GROUP_PLATFORM", "openai")
    monkeypatch.setenv("SUB2API_ACCOUNT_PROVIDER", "openai")
    monkeypatch.setenv("SUB2API_ACCOUNT_PLATFORM", "openai")
    monkeypatch.setenv("SUB2API_ACCOUNT_TYPE", "oauth")
    monkeypatch.setenv("SUB2API_ACCOUNT_WS_MODE", "context_pool")
    monkeypatch.setenv("SUB2API_ACCOUNT_TEMPORARY_UNSCHEDULABLE", "true")
    _reset_postgres_database(app_database_url)
    store = PostgresFlowStore(app_database_url)
    store.save_operational_data_runtime_settings(
        OperationalDataRuntimeSettings(enabled=False)
    )
    store.save_credit_control_runtime_settings(
        CreditControlRuntimeSettings(enabled=False)
    )
    store.save_provisioning_runtime_settings(
        ProvisioningRuntimeSettings()
    )
    clear_app_caches()
    yield {"database_url": app_database_url, "database_name": os.getenv("TEST_POSTGRES_NAME", DEFAULT_TEST_POSTGRES_NAME)}
    clear_app_caches()


@pytest.fixture
def client(app_env: dict[str, str]) -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client
