from __future__ import annotations

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
