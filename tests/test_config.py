from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings


CONFIG_ENV_NAMES = (
    "CONFIG_PATH",
    "SUB2API_BASE_URL",
    "SUB2API_ADMIN_API_KEY",
    "APP_BASE_URL",
    "APP_BASE_PATH",
    "OPENAI_OAUTH_REDIRECT_URI",
    "APP_AUTH_USERNAME",
    "APP_AUTH_PASSWORD",
    "APP_ACCESS_KEY_TTL_HOURS",
    "SQLITE_DB_PATH",
    "PROVISIONING_ASSIGNMENT_MODE",
    "AUTO_ROTATION_ENABLED",
    "AUTO_ROTATION_INTERVAL_SECONDS",
    "AUTO_ROTATION_COOLDOWN_MINUTES",
    "AUTO_ROTATION_USAGE_WINDOW",
    "AUTO_ROTATION_USAGE_THRESHOLDS_JSON",
    "CREDIT_CONTROL_RECHARGE_TICK_SECONDS",
    "OPERATIONAL_DATA_ENABLED",
    "OPERATIONAL_DATA_COLLECT_INTERVAL_SECONDS",
    "OPERATIONAL_DATA_EXPIRATION",
    "SUB2API_GROUP_PLATFORM",
    "SUB2API_ACCOUNT_PROVIDER",
    "SUB2API_ACCOUNT_PLATFORM",
    "SUB2API_ACCOUNT_TYPE",
    "SUB2API_ACCOUNT_WS_MODE",
    "SUB2API_ACCOUNT_CONCURRENCY",
    "SUB2API_ACCOUNT_MODEL_WHITELIST",
    "SUB2API_ACCOUNT_MODEL_WHITELIST_JSON",
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
  base_path: /sidecar/
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
    account_concurrency: 7
    account_model_whitelist:
      - yaml-model-a
      - yaml-model-b
    account_temporary_unschedulable: false
    account_temporary_unschedulable_rules:
      - error_code: "418"
        duration_minutes: 5
        keywords:
          - teapot
          - brew
        description: 茶壶保护 - 暂停 5 分钟
provisioning:
  assignment_mode: managed_pool
auto_rotation:
  enabled: true
  interval_seconds: 300
  cooldown_minutes: 5
  usage_window: 5h
  usage_thresholds:
    - 10
    - 20.5
credit_control:
  recharge_tick_seconds: 120
operational_data:
  enabled: true
  collect_interval_seconds: 90
  expiration: 240
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")

    settings = Settings.from_env()

    assert settings.sub2api_base_url == "http://yaml-sub2api.local"
    assert settings.app_base_url == "http://yaml-sidecar.local"
    assert settings.app_base_path == "/sidecar"
    assert settings.openai_oauth_redirect_uri == "http://localhost:1555/callback"
    assert settings.app_auth_username == "ops"
    assert settings.app_access_key_ttl_hours == 6
    assert settings.sqlite_db_path == "./data/yaml.db"
    assert settings.request_timeout_seconds == 12
    assert settings.assignment_mode.value == "managed_pool"
    assert settings.auto_rotation.enabled is True
    assert settings.auto_rotation.interval_seconds == 300
    assert settings.auto_rotation.cooldown_minutes == 5
    assert settings.auto_rotation.usage_window.value == "5h"
    assert settings.auto_rotation.usage_thresholds == (10.0, 20.5)
    assert settings.credit_control.recharge_tick_seconds == 120
    assert settings.operational_data.enabled is True
    assert settings.operational_data.collect_interval_seconds == 90
    assert settings.operational_data.expiration == 240

    defaults = settings.sub2api_provisioning_defaults
    assert defaults.group_platform == "yaml-group"
    assert defaults.account_provider == "yaml-provider"
    assert defaults.account_platform == "yaml-platform"
    assert defaults.account_ws_mode == "yaml_pool"
    assert defaults.account_concurrency == 7
    assert defaults.account_model_whitelist == ("yaml-model-a", "yaml-model-b")
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
    monkeypatch.setenv("OPERATIONAL_DATA_COLLECT_INTERVAL_SECONDS", "45")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")

    settings = Settings.from_env()

    assert settings.sub2api_base_url == "http://env-sub2api.local"
    assert settings.app_access_key_ttl_hours == 18
    assert settings.operational_data.collect_interval_seconds == 45


def test_settings_defaults_operational_data_to_enabled(monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")

    settings = Settings.from_env()

    assert settings.operational_data.enabled is True
    assert settings.operational_data.collect_interval_seconds == 60
    assert settings.operational_data.expiration is None


def test_settings_rejects_negative_operational_data_interval(monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("OPERATIONAL_DATA_COLLECT_INTERVAL_SECONDS", "-1")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "OPERATIONAL_DATA_COLLECT_INTERVAL_SECONDS" in str(exc_info.value)


def test_settings_rejects_enabled_operational_data_with_zero_interval(
    monkeypatch,
) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("OPERATIONAL_DATA_ENABLED", "true")
    monkeypatch.setenv("OPERATIONAL_DATA_COLLECT_INTERVAL_SECONDS", "0")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "OPERATIONAL_DATA_COLLECT_INTERVAL_SECONDS" in str(exc_info.value)


def test_settings_loads_operational_data_expiration_from_env(monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("OPERATIONAL_DATA_EXPIRATION", "240")

    settings = Settings.from_env()

    assert settings.operational_data.expiration == 240


def test_settings_rejects_non_positive_operational_data_expiration(
    monkeypatch,
) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("OPERATIONAL_DATA_EXPIRATION", "0")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "OPERATIONAL_DATA_EXPIRATION" in str(exc_info.value)


def test_settings_normalizes_env_base_path(monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("APP_BASE_PATH", "sidecar/")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")

    settings = Settings.from_env()

    assert settings.app_base_path == "/sidecar"


def test_settings_parse_sub2api_provisioning_overrides(monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("SUB2API_GROUP_PLATFORM", "openai")
    monkeypatch.setenv("SUB2API_ACCOUNT_PROVIDER", "openai")
    monkeypatch.setenv("SUB2API_ACCOUNT_PLATFORM", "openai")
    monkeypatch.setenv("SUB2API_ACCOUNT_TYPE", "oauth")
    monkeypatch.setenv("SUB2API_ACCOUNT_WS_MODE", "context_pool")
    monkeypatch.setenv("SUB2API_ACCOUNT_CONCURRENCY", "8")
    monkeypatch.setenv(
        "SUB2API_ACCOUNT_MODEL_WHITELIST_JSON",
        '["gpt-test-a", "gpt-test-b"]',
    )
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
    assert settings.sub2api_provisioning_defaults.account_concurrency == 8
    assert settings.sub2api_provisioning_defaults.account_model_whitelist == (
        "gpt-test-a",
        "gpt-test-b",
    )
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
    monkeypatch.setenv("AUTO_ROTATION_USAGE_WINDOW", "2h")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "AUTO_ROTATION_USAGE_WINDOW" in str(exc_info.value)
