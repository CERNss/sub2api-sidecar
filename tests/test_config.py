from __future__ import annotations

import pytest

from app.config import Settings


def test_settings_parse_sub2api_provisioning_overrides(monkeypatch) -> None:
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
    monkeypatch.setenv("SUB2API_BASE_URL", "http://mock-sub2api.local")
    monkeypatch.setenv("SUB2API_ADMIN_API_KEY", "test-key")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_OAUTH_REDIRECT_URI", "http://localhost:1455/callback")
    monkeypatch.setenv("DEFAULT_USER_PASSWORD", "ChangeMe123!")
    monkeypatch.setenv("AUTO_ROTATION_USAGE_WINDOW", "2h")

    with pytest.raises(Exception) as exc_info:
        Settings.from_env()

    assert "AUTO_ROTATION_USAGE_WINDOW" in str(exc_info.value)
