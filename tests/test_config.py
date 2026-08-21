from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings


CONFIG_ENV_NAMES = (
    "CONFIG_PATH",
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
    "DATABASE_POOL_MAX_SIZE",
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
    "SUB2API_ACCOUNT_BASE_URL",
    "SUB2API_ACCOUNT_EXTRA_JSON",
    "SUB2API_ACCOUNT_PRIORITY",
    "SUB2API_ACCOUNT_RATE_MULTIPLIER",
    "SUB2API_ACCOUNT_AUTO_PAUSE_ON_EXPIRED",
    "SUB2API_PROVISIONING_PER_PLATFORM_JSON",
    "SUB2API_API_KEY_GROUP_SELECTION",
    "SUB2API_USAGE_LOG_MAX_ITEMS",
    "SUB2API_REQUEST_MAX_RETRIES",
    "SUB2API_API_KEYS_FETCH_CONCURRENCY",
    "NOTIFICATION_ACCOUNT_INVALID_WHITELIST_IDS",
    "NOTIFICATION_ACCOUNT_INVALID_WHITELIST_NAMES",
    "NOTIFICATION_ACCOUNT_INVALID_WHITELIST_EMAILS",
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
    config_path.write_text(
        _database_config_yaml()
        + """
sub2api:
  upstreams:
    - id: main
      base_url: http://mock-sub2api.local
      admin_api_key_env: SUB2API_ADMIN_API_KEY
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")


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
notifications:
  account_invalid_whitelist:
    ids:
      - acct-yaml
    names:
      - Manual Off
    emails:
      - manual@example.com
sub2api:
  request_timeout_seconds: 12
  usage_log_max_items: 50000
  api_key_group_selection: random
  upstreams:
    - id: main
      name: YAML Sub2API
      base_url: http://yaml-sub2api.local
      admin_api_key_env: SUB2API_ADMIN_API_KEY
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

    assert settings.default_sub2api_upstream.base_url == "http://yaml-sub2api.local"
    assert settings.app_base_url == "http://yaml-sidecar.local"
    assert settings.app_base_path == "/sidecar"
    assert settings.openai_oauth_redirect_uri == "http://localhost:1555/callback"
    assert settings.app_auth_username == "ops"
    assert settings.app_access_key_ttl_hours == 6
    assert settings.database_url == "postgresql://sidecar:secret@postgres:5432/sidecar"
    assert settings.request_timeout_seconds == 12
    assert settings.sub2api_usage_log_max_items == 50000
    assert settings.api_key_group_selection == "random"
    assert settings.account_invalid_alert_whitelist.ids == ("acct-yaml",)
    assert settings.account_invalid_alert_whitelist.names == ("Manual Off",)
    assert settings.account_invalid_alert_whitelist.emails == ("manual@example.com",)

    defaults = settings.default_sub2api_upstream.provisioning_defaults
    assert defaults.group_platform == "yaml-group"
    assert defaults.account_provider == "yaml-provider"
    assert defaults.account_platform == "yaml-platform"
    assert defaults.account_ws_mode == "yaml_pool"
    assert defaults.account_concurrency == 7
    assert defaults.account_model_whitelist == ("yaml-model-a", "yaml-model-b")
    assert defaults.account_temporary_unschedulable is False
    assert defaults.account_temporary_unschedulable_rules[0].error_code == "418"


def test_settings_env_overrides_account_invalid_whitelist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("NOTIFICATION_ACCOUNT_INVALID_WHITELIST_IDS", "acct-env, acct-two")
    monkeypatch.setenv("NOTIFICATION_ACCOUNT_INVALID_WHITELIST_NAMES", "Manual Off")
    monkeypatch.setenv(
        "NOTIFICATION_ACCOUNT_INVALID_WHITELIST_EMAILS",
        "manual@example.com, disabled@example.com",
    )

    settings = Settings.from_env()

    assert settings.account_invalid_alert_whitelist.ids == ("acct-env", "acct-two")
    assert settings.account_invalid_alert_whitelist.names == ("Manual Off",)
    assert settings.account_invalid_alert_whitelist.emails == (
        "manual@example.com",
        "disabled@example.com",
    )


def test_settings_env_overrides_group_whitelist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("NOTIFICATION_GROUP_WHITELIST_IDS", "g-env, g-two")
    monkeypatch.setenv("NOTIFICATION_GROUP_WHITELIST_NAMES", "Landing Pool")

    settings = Settings.from_env()

    assert settings.group_alert_whitelist.ids == ("g-env", "g-two")
    assert settings.group_alert_whitelist.names == ("Landing Pool",)


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
  upstreams:
    - id: main
      base_url: http://yaml-sub2api.local
      admin_api_key_env: SUB2API_ADMIN_API_KEY
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("APP_ACCESS_KEY_TTL_HOURS", "18")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")

    settings = Settings.from_env()

    assert settings.default_sub2api_upstream.base_url == "http://yaml-sub2api.local"
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
    assert settings.default_sub2api_upstream.base_url == "http://main-sub2api.local"
    assert settings.default_sub2api_upstream.admin_api_key == "main-key"
    assert settings.get_sub2api_upstream("secondary").base_url == "http://secondary-sub2api.local"
    assert settings.get_sub2api_upstream("secondary").admin_api_key == "secondary-key"
    assert settings.get_sub2api_upstream("secondary").request_timeout_seconds == 18


def test_settings_rejects_invalid_api_key_group_selection(
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
  api_key_group_selection: round_robin
  upstreams:
    - id: main
      base_url: http://main-sub2api.local
      admin_api_key_env: SUB2API_ADMIN_API_KEY
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "main-key")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "SUB2API_API_KEY_GROUP_SELECTION" in str(exc_info.value)


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
  upstreams:
    - id: main
      base_url: http://mock-sub2api.local
      admin_api_key_env: SUB2API_ADMIN_API_KEY
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
  upstreams:
    - id: main
      base_url: http://mock-sub2api.local
      admin_api_key_env: SUB2API_ADMIN_API_KEY
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

    defaults = settings.default_sub2api_upstream.provisioning_defaults
    assert defaults.group_platform == "openai"
    assert defaults.account_provider == "openai"
    assert defaults.account_platform == "openai"
    assert defaults.account_type == "oauth"
    assert defaults.account_ws_mode == "context_pool"
    assert defaults.account_concurrency == 8
    assert defaults.account_model_whitelist == (
        "gpt-test-a",
        "gpt-test-b",
    )
    assert defaults.account_temporary_unschedulable is False

    rules = defaults.account_temporary_unschedulable_rules
    assert len(rules) == 1
    assert rules[0].error_code == "418"
    assert rules[0].duration_minutes == 5
    assert rules[0].keywords == ("teapot", "brew")
    assert rules[0].description == "茶壶保护 - 暂停 5 分钟"


def test_settings_rejects_removed_provisioning_assignment_mode_env(monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("PROVISIONING_ASSIGNMENT_MODE", "managed_pool")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "PROVISIONING_ASSIGNMENT_MODE" in str(exc_info.value)


def test_settings_rejects_removed_auto_rotation_env(monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_WINDOW", "7d")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "AUTO_ROTATION_USAGE_WINDOW" in str(exc_info.value)


def test_settings_usage_log_max_items_defaults_to_100k(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")

    settings = Settings.from_env()

    assert settings.sub2api_usage_log_max_items == 100_000


def test_settings_usage_log_max_items_env_override_allows_unlimited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("SUB2API_USAGE_LOG_MAX_ITEMS", "0")

    settings = Settings.from_env()

    assert settings.sub2api_usage_log_max_items == 0


def test_settings_rejects_negative_usage_log_max_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("SUB2API_USAGE_LOG_MAX_ITEMS", "-1")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "SUB2API_USAGE_LOG_MAX_ITEMS" in str(exc_info.value)


def test_settings_page_fetch_concurrency_defaults_to_8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")

    settings = Settings.from_env()

    assert settings.sub2api_page_fetch_concurrency == 8


def test_settings_page_fetch_concurrency_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("SUB2API_PAGE_FETCH_CONCURRENCY", "16")

    settings = Settings.from_env()

    assert settings.sub2api_page_fetch_concurrency == 16


def test_settings_rejects_non_positive_page_fetch_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("SUB2API_PAGE_FETCH_CONCURRENCY", "0")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "SUB2API_PAGE_FETCH_CONCURRENCY" in str(exc_info.value)


def test_settings_database_pool_max_size_defaults_to_10(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")

    settings = Settings.from_env()

    assert settings.database_pool_max_size == 10


def test_settings_database_pool_max_size_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("DATABASE_POOL_MAX_SIZE", "25")

    settings = Settings.from_env()

    assert settings.database_pool_max_size == 25


def test_settings_rejects_non_positive_database_pool_max_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("DATABASE_POOL_MAX_SIZE", "0")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "DATABASE_POOL_MAX_SIZE" in str(exc_info.value)


def test_settings_sub2api_request_pool_knobs_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")

    settings = Settings.from_env()

    assert settings.sub2api_request_max_retries == 2
    assert settings.sub2api_api_keys_fetch_concurrency == 8


def test_settings_sub2api_request_pool_knobs_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("SUB2API_REQUEST_MAX_RETRIES", "0")
    monkeypatch.setenv("SUB2API_API_KEYS_FETCH_CONCURRENCY", "16")

    settings = Settings.from_env()

    assert settings.sub2api_request_max_retries == 0
    assert settings.sub2api_api_keys_fetch_concurrency == 16


def test_settings_rejects_negative_request_max_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("SUB2API_REQUEST_MAX_RETRIES", "-1")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "SUB2API_REQUEST_MAX_RETRIES" in str(exc_info.value)


def test_settings_rejects_non_positive_api_keys_fetch_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("SUB2API_API_KEYS_FETCH_CONCURRENCY", "0")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "SUB2API_API_KEYS_FETCH_CONCURRENCY" in str(exc_info.value)


def _write_provisioning_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provisioning_yaml: str
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _database_config_yaml()
        + """
sub2api:
  upstreams:
    - id: main
      base_url: http://mock-sub2api.local
      admin_api_key_env: SUB2API_ADMIN_API_KEY
  provisioning_defaults:
"""
        + provisioning_yaml,
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")


def _provisioning_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, yaml: str):
    _clear_config_env(monkeypatch)
    _write_provisioning_config(tmp_path, monkeypatch, yaml)
    return Settings.from_env().default_sub2api_upstream.provisioning_defaults


def test_provisioning_defaults_without_per_platform_keep_base_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config that never mentions per_platform behaves as it did before."""
    defaults = _provisioning_defaults(
        tmp_path,
        monkeypatch,
        """    account_concurrency: 7
    account_model_whitelist:
      - base-model
""",
    )

    assert defaults.account_concurrency == 7
    assert defaults.account_model_whitelist == ("base-model",)
    assert defaults.account_ws_mode == "context_pool"
    # The default platform gets the base template unchanged, and so does a caller
    # that resolved no platform at all.
    assert defaults.account_platform == "openai"
    assert defaults.for_platform("openai") is defaults
    assert defaults.for_platform(None) is defaults
    assert defaults.for_platform("") is defaults


def test_base_model_whitelist_and_ws_mode_do_not_leak_to_other_platforms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both describe an openai-shaped account, so they stop at account_platform."""
    defaults = _provisioning_defaults(
        tmp_path,
        monkeypatch,
        """    account_concurrency: 7
    account_model_whitelist:
      - base-model
""",
    )
    anthropic = defaults.for_platform("anthropic")

    # No model list and no websockets extras: an unlisted platform would otherwise
    # be handed openai's models and openai's transport keys, silently.
    assert anthropic.account_model_whitelist == ()
    assert anthropic.account_ws_mode == ""
    # Everything genuinely cross-platform still applies.
    assert anthropic.account_concurrency == 7
    assert anthropic.account_temporary_unschedulable is True
    assert anthropic.account_temporary_unschedulable_rules == (
        defaults.account_temporary_unschedulable_rules
    )
    assert anthropic.account_type == defaults.account_type


def test_another_platform_opts_in_to_a_model_whitelist_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults = _provisioning_defaults(
        tmp_path,
        monkeypatch,
        """    account_model_whitelist:
      - base-model
    per_platform:
      anthropic:
        account_model_whitelist:
          - claude-4
      gemini:
        account_ws_mode: context_pool
""",
    )

    anthropic = defaults.for_platform("anthropic")
    assert anthropic.account_model_whitelist == ("claude-4",)
    # Opting into one of the two does not drag the other along.
    assert anthropic.account_ws_mode == ""

    gemini = defaults.for_platform("gemini")
    assert gemini.account_ws_mode == "context_pool"
    assert gemini.account_model_whitelist == ()


def test_base_model_whitelist_follows_a_non_openai_default_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scope is `account_platform`, not the literal string "openai"."""
    defaults = _provisioning_defaults(
        tmp_path,
        monkeypatch,
        """    account_platform: anthropic
    account_model_whitelist:
      - claude-4
""",
    )

    assert defaults.for_platform("anthropic").account_model_whitelist == ("claude-4",)
    assert defaults.for_platform("openai").account_model_whitelist == ()


def test_provisioning_defaults_ship_a_builtin_grok_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stock install: grok already matches what upstream writes for grok accounts."""
    defaults = _provisioning_defaults(
        tmp_path,
        monkeypatch,
        """    account_concurrency: 8
""",
    )
    grok = defaults.for_platform("grok")

    assert grok.account_model_whitelist == (
        "composer-2.5",
        "grok-4.5",
        "grok-4.6",
    )
    assert grok.account_base_url == "https://cli-chat-proxy.grok.com/v1"
    assert grok.account_extra == {"grok_client_tool_cache_enabled": True}
    # No websockets extras for grok, and everything the entry does not name — the
    # backoff rules, concurrency — still comes from the base template.
    assert grok.account_ws_mode == ""
    assert grok.account_concurrency == 8
    assert grok.account_temporary_unschedulable is True
    assert grok.account_temporary_unschedulable_rules == (
        defaults.account_temporary_unschedulable_rules
    )


def test_per_platform_overrides_only_the_keys_it_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults = _provisioning_defaults(
        tmp_path,
        monkeypatch,
        """    account_concurrency: 5
    per_platform:
      grok:
        account_concurrency: 12
      anthropic:
        account_model_whitelist:
          - claude-4
        account_base_url: https://api.anthropic.com/v1
        account_extra:
          anthropic_beta_enabled: true
""",
    )

    grok = defaults.for_platform("grok")
    assert grok.account_concurrency == 12
    # Tuning one knob must not drop the rest of the shipped grok template.
    assert grok.account_base_url == "https://cli-chat-proxy.grok.com/v1"
    assert grok.account_model_whitelist[0] == "composer-2.5"

    anthropic = defaults.for_platform("anthropic")
    assert anthropic.account_model_whitelist == ("claude-4",)
    assert anthropic.account_base_url == "https://api.anthropic.com/v1"
    assert anthropic.account_extra == {"anthropic_beta_enabled": True}
    assert anthropic.account_concurrency == 5

    # Base is untouched by either entry.
    assert defaults.account_concurrency == 5
    assert defaults.account_base_url == ""


def test_per_platform_account_extra_merges_over_the_base_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """extra is the one merged key: base extras are common ground, platform wins."""
    defaults = _provisioning_defaults(
        tmp_path,
        monkeypatch,
        """    account_extra:
      shared_flag: true
      overridden: base
    per_platform:
      grok:
        account_extra:
          overridden: grok
""",
    )

    assert defaults.account_extra == {"shared_flag": True, "overridden": "base"}
    assert defaults.for_platform("grok").account_extra == {
        "shared_flag": True,
        "overridden": "grok",
    }


def test_per_platform_can_switch_off_a_model_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults = _provisioning_defaults(
        tmp_path,
        monkeypatch,
        """    per_platform:
      grok:
        account_model_whitelist: []
""",
    )

    assert defaults.for_platform("grok").account_model_whitelist == ()


def test_per_platform_rejects_unknown_override_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_provisioning_config(
        tmp_path,
        monkeypatch,
        """    per_platform:
      grok:
        account_notes: nope
""",
    )

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    message = str(exc_info.value)
    assert "account_notes" in message
    assert "account_model_whitelist" in message


def test_per_platform_can_be_supplied_as_json_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_config_env(monkeypatch)
    _write_minimal_config(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv(
        "SUB2API_PROVISIONING_PER_PLATFORM_JSON",
        '{"grok": {"account_ws_mode": "context_pool"}}',
    )

    defaults = Settings.from_env().default_sub2api_upstream.provisioning_defaults
    grok = defaults.for_platform("grok")

    assert grok.account_ws_mode == "context_pool"
    assert grok.account_base_url == "https://cli-chat-proxy.grok.com/v1"


def test_provisioning_defaults_read_the_new_template_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults = _provisioning_defaults(
        tmp_path,
        monkeypatch,
        """    account_base_url: https://base.example.com/v1
    account_priority: 3
    account_rate_multiplier: 2
    account_auto_pause_on_expired: false
""",
    )

    assert defaults.account_base_url == "https://base.example.com/v1"
    assert defaults.account_priority == 3
    assert defaults.account_rate_multiplier == 2
    assert defaults.account_auto_pause_on_expired is False
