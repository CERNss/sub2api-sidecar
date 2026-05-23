from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings


CONFIG_ENV_NAMES = (
    "CONFIG_PATH",
    "SUB2API_BASE_URL",
    "SUB2API_ADMIN_API_KEY",
    "SUB2API_SECONDARY_ADMIN_API_KEY",
    "APP_BASE_URL",
    "APP_BASE_PATH",
    "OPENAI_OAUTH_REDIRECT_URI",
    "APP_AUTH_USERNAME",
    "APP_AUTH_PASSWORD",
    "APP_ACCESS_KEY_TTL_HOURS",
    "POSTGRES_PASSWORD",
    "DATABASE_URL",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "SQLITE_DB_PATH",
    "PROVISIONING_ASSIGNMENT_MODE",
    "AUTO_ROTATION_ENABLED",
    "AUTO_ROTATION_INTERVAL_SECONDS",
    "AUTO_ROTATION_COOLDOWN_MINUTES",
    "AUTO_ROTATION_USAGE_WINDOW",
    "AUTO_ROTATION_USAGE_THRESHOLDS_JSON",
    "AUTO_ROTATION_IMBALANCE_EPSILON",
    "AUTO_ROTATION_IMPROVEMENT_DELTA",
    "CREDIT_CONTROL_ENABLED",
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
    monkeypatch.setenv("CONFIG_PATH", "__missing_test_config__.yaml")


def _database_config_yaml() -> str:
    return """
database:
  url: postgres
  port: 5432
  username: sidecar
  name: sidecar
""".lstrip()


def _write_minimal_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_database_config_yaml(), encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")


def test_settings_loads_non_secret_config_from_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _database_config_yaml()
        + """
app:
  base_url: http://yaml-sidecar.local
  base_path: /sidecar/
  auth_username: ops
  access_key_ttl_hours: 6
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
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")

    settings = Settings.from_env()

    assert settings.sub2api_base_url == "http://yaml-sub2api.local"
    assert settings.app_base_url == "http://yaml-sidecar.local"
    assert settings.app_base_path == "/sidecar"
    assert settings.openai_oauth_redirect_uri == "http://localhost:1555/callback"
    assert settings.app_auth_username == "ops"
    assert settings.app_access_key_ttl_hours == 6
    assert settings.database_url == "postgresql://sidecar:secret@postgres:5432/sidecar"
    assert settings.request_timeout_seconds == 12

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
        _database_config_yaml()
        + """
app:
  base_url: http://yaml-sidecar.local
  access_key_ttl_hours: 6
openai:
  oauth_redirect_uri: http://localhost:1555/callback
sub2api:
  base_url: http://yaml-sub2api.local
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("SUB2API_BASE_URL", "http://env-sub2api.local")
    monkeypatch.setenv("APP_ACCESS_KEY_TTL_HOURS", "18")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")

    settings = Settings.from_env()

    assert settings.sub2api_base_url == "http://env-sub2api.local"
    assert settings.app_access_key_ttl_hours == 18


def test_settings_loads_multiple_sub2api_upstreams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _database_config_yaml()
        + """
app:
  base_url: http://yaml-sidecar.local
openai:
  oauth_redirect_uri: http://localhost:1555/callback
sub2api:
  request_timeout_seconds: 12
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
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "main-key")
    monkeypatch.setenv("SUB2API_SECONDARY_ADMIN_API_KEY", "secondary-key")

    settings = Settings.from_env()

    assert settings.default_sub2api_upstream_id == "main"
    assert len(settings.sub2api_upstreams) == 2
    assert settings.sub2api_base_url == "http://main-sub2api.local"
    assert settings.sub2api_admin_api_key == "main-key"
    assert settings.get_sub2api_upstream("secondary").base_url == "http://secondary-sub2api.local"
    assert settings.get_sub2api_upstream("secondary").admin_api_key == "secondary-key"
    assert settings.get_sub2api_upstream("secondary").request_timeout_seconds == 18


def test_settings_rejects_duplicate_sub2api_upstream_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _database_config_yaml()
        + """
app:
  base_url: http://yaml-sidecar.local
openai:
  oauth_redirect_uri: http://localhost:1555/callback
sub2api:
  upstreams:
    - id: dup
      base_url: http://main-sub2api.local
      admin_api_key_env: SUB2API_ADMIN_API_KEY
    - id: dup
      base_url: http://secondary-sub2api.local
      admin_api_key_env: SUB2API_SECONDARY_ADMIN_API_KEY
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "main-key")
    monkeypatch.setenv("SUB2API_SECONDARY_ADMIN_API_KEY", "secondary-key")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "Duplicate Sub2API upstream id" in str(exc_info.value)


def test_settings_rejects_direct_database_url_env(monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://sidecar:secret@postgres:5432/sidecar")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "DATABASE_URL" in str(exc_info.value)


def test_settings_requires_structured_database_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
app:
  base_url: http://127.0.0.1:8000
openai:
  oauth_redirect_uri: http://localhost:1455/callback
sub2api:
  base_url: http://mock-sub2api.local
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    message = str(exc_info.value)
    assert "database.url" in message
    assert "database.username" in message
    assert "database.name" in message


def test_settings_rejects_removed_operational_data_interval(monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("OPERATIONAL_DATA_COLLECT_INTERVAL_SECONDS", "60")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "OPERATIONAL_DATA_COLLECT_INTERVAL_SECONDS" in str(exc_info.value)


def test_settings_rejects_removed_yaml_runtime_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
app:
  base_url: http://127.0.0.1:8000
openai:
  oauth_redirect_uri: http://localhost:1455/callback
sub2api:
  base_url: http://mock-sub2api.local
auto_rotation:
  enabled: true
  interval_seconds: 60
credit_control:
  enabled: true
  recharge_tick_seconds: 60
operational_data:
  enabled: true
  expiration: 240
  collect_interval_seconds: 60
provisioning:
  assignment_mode: managed_pool
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    message = str(exc_info.value)
    assert "auto_rotation" in message
    assert "credit_control" in message
    assert "operational_data" in message
    assert "auto_rotation.enabled" in message
    assert "auto_rotation.interval_seconds" in message
    assert "credit_control.enabled" in message
    assert "credit_control.recharge_tick_seconds" in message
    assert "operational_data.enabled" in message
    assert "operational_data.expiration" in message
    assert "operational_data.collect_interval_seconds" in message
    assert "provisioning" in message
    assert "provisioning.assignment_mode" in message


def test_settings_rejects_removed_runtime_env(monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("OPERATIONAL_DATA_EXPIRATION", "240")
    monkeypatch.setenv("CREDIT_CONTROL_ENABLED", "false")
    monkeypatch.setenv("AUTO_ROTATION_ENABLED", "true")
    monkeypatch.setenv("PROVISIONING_ASSIGNMENT_MODE", "managed_pool")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    message = str(exc_info.value)
    assert "AUTO_ROTATION_ENABLED" in message
    assert "CREDIT_CONTROL_ENABLED" in message
    assert "OPERATIONAL_DATA_EXPIRATION" in message
    assert "PROVISIONING_ASSIGNMENT_MODE" in message


def test_settings_normalizes_env_base_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("APP_BASE_PATH", "sidecar/")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")

    settings = Settings.from_env()

    assert settings.app_base_path == "/sidecar"


def test_settings_parse_sub2api_provisioning_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
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


def test_settings_rejects_removed_provisioning_assignment_mode_env(monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("PROVISIONING_ASSIGNMENT_MODE", "managed_pool")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "PROVISIONING_ASSIGNMENT_MODE" in str(exc_info.value)


def test_settings_rejects_removed_auto_rotation_env(monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_WINDOW", "7d")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "AUTO_ROTATION_USAGE_WINDOW" in str(exc_info.value)
