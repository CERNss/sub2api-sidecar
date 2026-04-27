from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings


CONFIG_ENV_NAMES = (
    "CONFIG_PATH",
    "SUB2API_BASE_URL",
    "SUB2API_ADMIN_API_KEY",
    "APP_BASE_URL",
    "OPENAI_OAUTH_REDIRECT_URI",
    "APP_AUTH_USERNAME",
    "APP_AUTH_PASSWORD",
    "APP_ACCESS_KEY_TTL_HOURS",
    "SQLITE_DB_PATH",
    "DEFAULT_USER_PASSWORD",
    "GROUP_NAME_PREFIX",
    "PROVISIONING_ASSIGNMENT_MODE",
    "AUTO_ROTATION_ENABLED",
    "AUTO_ROTATION_INTERVAL_SECONDS",
    "AUTO_ROTATION_COOLDOWN_MINUTES",
    "AUTO_ROTATION_USAGE_WINDOW",
    "AUTO_ROTATION_USAGE_THRESHOLDS_JSON",
    "SUB2API_GROUP_PLATFORM",
    "SUB2API_ACCOUNT_PROVIDER",
    "SUB2API_ACCOUNT_PLATFORM",
    "SUB2API_ACCOUNT_TYPE",
    "SUB2API_ACCOUNT_WS_MODE",
    "SUB2API_ACCOUNT_TEMPORARY_UNSCHEDULABLE",
    "SUB2API_ACCOUNT_TEMPORARY_UNSCHEDULABLE_RULES_JSON",
)


def _clear_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in CONFIG_ENV_NAMES:
        monkeypatch.delenv(env_name, raising=False)


def test_settings_loads_non_secret_config_from_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
app:
  base_url: http://yaml-sidecar.local
  auth_username: ops
  access_key_ttl_hours: 6
storage:
  sqlite_db_path: ./data/yaml.db
openai:
  oauth_redirect_uri: http://localhost:1555/callback
sub2api:
  base_url: http://yaml-sub2api.local
  request_timeout_seconds: 12
  provisioning_defaults:
    group_platform: yaml-group
    account_provider: yaml-provider
    account_platform: yaml-platform
    account_type: oauth
    account_ws_mode: yaml_pool
    account_temporary_unschedulable: false
    account_temporary_unschedulable_rules:
      - error_code: "418"
        duration_minutes: 5
        keywords:
          - teapot
          - brew
        description: 茶壶保护 - 暂停 5 分钟
provisioning:
  group_name_prefix: yaml-
  assignment_mode: managed_pool
auto_rotation:
  enabled: true
  interval_seconds: 300
  cooldown_minutes: 5
  usage_window: 5h
  usage_thresholds:
    - 10
    - 20.5
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("DEFAULT_USER_PASSWORD", "ChangeMe123!")

    settings = Settings.from_env()

    assert settings.sub2api_base_url == "http://yaml-sub2api.local"
    assert settings.app_base_url == "http://yaml-sidecar.local"
    assert settings.openai_oauth_redirect_uri == "http://localhost:1555/callback"
    assert settings.app_auth_username == "ops"
    assert settings.app_access_key_ttl_hours == 6
    assert settings.sqlite_db_path == "./data/yaml.db"
    assert settings.group_name_prefix == "yaml-"
    assert settings.request_timeout_seconds == 12
    assert settings.assignment_mode.value == "managed_pool"
    assert settings.auto_rotation.enabled is True
    assert settings.auto_rotation.interval_seconds == 300
    assert settings.auto_rotation.cooldown_minutes == 5
    assert settings.auto_rotation.usage_window.value == "5h"
    assert settings.auto_rotation.usage_thresholds == (10.0, 20.5)

    defaults = settings.sub2api_provisioning_defaults
    assert defaults.group_platform == "yaml-group"
    assert defaults.account_provider == "yaml-provider"
    assert defaults.account_platform == "yaml-platform"
    assert defaults.account_ws_mode == "yaml_pool"
    assert defaults.account_temporary_unschedulable is False
    assert defaults.account_temporary_unschedulable_rules[0].error_code == "418"


def test_settings_env_overrides_config_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
app:
  base_url: http://yaml-sidecar.local
  access_key_ttl_hours: 6
openai:
  oauth_redirect_uri: http://localhost:1555/callback
sub2api:
  base_url: http://yaml-sub2api.local
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("SUB2API_BASE_URL", "http://env-sub2api.local")
    monkeypatch.setenv("APP_ACCESS_KEY_TTL_HOURS", "18")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("DEFAULT_USER_PASSWORD", "ChangeMe123!")

    settings = Settings.from_env()

    assert settings.sub2api_base_url == "http://env-sub2api.local"
    assert settings.app_access_key_ttl_hours == 18


def test_settings_parse_sub2api_provisioning_overrides(monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("DEFAULT_USER_PASSWORD", "ChangeMe123!")
    monkeypatch.setenv("SUB2API_GROUP_PLATFORM", "openai")
    monkeypatch.setenv("SUB2API_ACCOUNT_PROVIDER", "openai")
    monkeypatch.setenv("SUB2API_ACCOUNT_PLATFORM", "openai")
    monkeypatch.setenv("SUB2API_ACCOUNT_TYPE", "oauth")
    monkeypatch.setenv("SUB2API_ACCOUNT_WS_MODE", "context_pool")
    monkeypatch.setenv("SUB2API_ACCOUNT_TEMPORARY_UNSCHEDULABLE", "false")
    monkeypatch.setenv(
        "SUB2API_ACCOUNT_TEMPORARY_UNSCHEDULABLE_RULES_JSON",
        (
            '[{"error_code":"418","duration_minutes":5,'
            '"keywords":["teapot","brew"],"description":"茶壶保护 - 暂停 5 分钟"}]'
        ),
    )

    settings = Settings.from_env()

    assert settings.sub2api_provisioning_defaults.group_platform == "openai"
    assert settings.sub2api_provisioning_defaults.account_provider == "openai"
    assert settings.sub2api_provisioning_defaults.account_platform == "openai"
    assert settings.sub2api_provisioning_defaults.account_type == "oauth"
    assert settings.sub2api_provisioning_defaults.account_ws_mode == "context_pool"
    assert settings.sub2api_provisioning_defaults.account_temporary_unschedulable is False

    rules = settings.sub2api_provisioning_defaults.account_temporary_unschedulable_rules
    assert len(rules) == 1
    assert rules[0].error_code == "418"
    assert rules[0].duration_minutes == 5
    assert rules[0].keywords == ("teapot", "brew")
    assert rules[0].description == "茶壶保护 - 暂停 5 分钟"


def test_settings_parse_managed_pool_and_auto_rotation(monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("DEFAULT_USER_PASSWORD", "ChangeMe123!")
    monkeypatch.setenv("PROVISIONING_ASSIGNMENT_MODE", "managed_pool")
    monkeypatch.setenv("AUTO_ROTATION_ENABLED", "true")
    monkeypatch.setenv("AUTO_ROTATION_INTERVAL_SECONDS", "900")
    monkeypatch.setenv("AUTO_ROTATION_COOLDOWN_MINUTES", "15")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_WINDOW", "7d")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_THRESHOLDS_JSON", "[10, 25.5]")

    settings = Settings.from_env()

    assert settings.assignment_mode.value == "managed_pool"
    assert settings.auto_rotation.enabled is True
    assert settings.auto_rotation.interval_seconds == 900
    assert settings.auto_rotation.cooldown_minutes == 15
    assert settings.auto_rotation.usage_window.value == "7d"
    assert settings.auto_rotation.usage_thresholds == (10.0, 25.5)


def test_settings_reject_invalid_auto_rotation_window(monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("DEFAULT_USER_PASSWORD", "ChangeMe123!")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_WINDOW", "2h")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "AUTO_ROTATION_USAGE_WINDOW" in str(exc_info.value)
