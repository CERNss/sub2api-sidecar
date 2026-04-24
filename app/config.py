from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

from app.errors import ConfigurationError

load_dotenv()


@dataclass(frozen=True)
class TemporaryUnschedulableRule:
    error_code: str
    duration_minutes: int
    keywords: tuple[str, ...]
    description: str


DEFAULT_TEMPORARY_UNSCHEDULABLE_RULES: tuple[TemporaryUnschedulableRule, ...] = (
    TemporaryUnschedulableRule(
        error_code="529",
        duration_minutes=60,
        keywords=("overloaded", "too many"),
        description="服务过载 - 暂停 60 分钟",
    ),
    TemporaryUnschedulableRule(
        error_code="429",
        duration_minutes=10,
        keywords=("rate limit", "too many requests"),
        description="触发限流 - 暂停 10 分钟",
    ),
    TemporaryUnschedulableRule(
        error_code="503",
        duration_minutes=30,
        keywords=("unavailable", "maintenance"),
        description="服务不可用 - 暂停 30 分钟",
    ),
)


@dataclass(frozen=True)
class Sub2APIProvisioningDefaults:
    group_platform: str = "openai"
    account_provider: str = "openai"
    account_platform: str = "openai"
    account_type: str = "oauth"
    account_ws_mode: str = "context_pool"
    account_temporary_unschedulable: bool = True
    account_temporary_unschedulable_rules: tuple[TemporaryUnschedulableRule, ...] = (
        DEFAULT_TEMPORARY_UNSCHEDULABLE_RULES
    )


@dataclass(frozen=True)
class Settings:
    sub2api_base_url: str
    sub2api_admin_api_key: str
    app_base_url: str
    openai_oauth_redirect_uri: str
    sub2api_provisioning_defaults: Sub2APIProvisioningDefaults
    app_auth_username: str = "admin"
    app_auth_password: str | None = None
    app_access_key_ttl_hours: int = 12
    sqlite_db_path: str = "data/sub2api-sidecar.db"
    default_user_password: str = "ChangeMe123!"
    group_name_prefix: str = "openai-oauth-"
    request_timeout_seconds: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        missing = []
        required_envs = {
            "SUB2API_BASE_URL": "sub2api_base_url",
            "SUB2API_ADMIN_API_KEY": "sub2api_admin_api_key",
            "APP_BASE_URL": "app_base_url",
            "OPENAI_OAUTH_REDIRECT_URI": "openai_oauth_redirect_uri",
            "DEFAULT_USER_PASSWORD": "default_user_password",
        }
        values: dict[str, str | int] = {}
        for env_name, field_name in required_envs.items():
            value = os.getenv(env_name)
            if not value:
                missing.append(env_name)
            else:
                values[field_name] = value

        if missing:
            raise ConfigurationError(
                "Missing required environment variables: " + ", ".join(sorted(missing))
            )

        values["sqlite_db_path"] = os.getenv("SQLITE_DB_PATH", "data/sub2api-sidecar.db")
        values["group_name_prefix"] = os.getenv("GROUP_NAME_PREFIX", "openai-oauth-")
        values["app_auth_username"] = os.getenv("APP_AUTH_USERNAME", "admin")
        values["app_auth_password"] = os.getenv("APP_AUTH_PASSWORD") or None

        timeout_raw = os.getenv("REQUEST_TIMEOUT_SECONDS", "30")
        try:
            values["request_timeout_seconds"] = int(timeout_raw)
        except ValueError as exc:
            raise ConfigurationError("REQUEST_TIMEOUT_SECONDS must be an integer") from exc

        access_key_ttl_raw = os.getenv("APP_ACCESS_KEY_TTL_HOURS", "12")
        try:
            values["app_access_key_ttl_hours"] = int(access_key_ttl_raw)
        except ValueError as exc:
            raise ConfigurationError("APP_ACCESS_KEY_TTL_HOURS must be an integer") from exc

        if values["app_access_key_ttl_hours"] <= 0:
            raise ConfigurationError("APP_ACCESS_KEY_TTL_HOURS must be greater than zero")

        values["sub2api_provisioning_defaults"] = Sub2APIProvisioningDefaults(
            group_platform=os.getenv("SUB2API_GROUP_PLATFORM", "openai"),
            account_provider=os.getenv("SUB2API_ACCOUNT_PROVIDER", "openai"),
            account_platform=os.getenv("SUB2API_ACCOUNT_PLATFORM", "openai"),
            account_type=os.getenv("SUB2API_ACCOUNT_TYPE", "oauth"),
            account_ws_mode=os.getenv("SUB2API_ACCOUNT_WS_MODE", "context_pool"),
            account_temporary_unschedulable=_parse_bool_env(
                "SUB2API_ACCOUNT_TEMPORARY_UNSCHEDULABLE",
                default=True,
            ),
            account_temporary_unschedulable_rules=_parse_rules_env(
                "SUB2API_ACCOUNT_TEMPORARY_UNSCHEDULABLE_RULES_JSON"
            ),
        )

        return cls(**values)


def _parse_bool_env(env_name: str, *, default: bool) -> bool:
    raw_value = os.getenv(env_name)
    if raw_value is None or raw_value == "":
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{env_name} must be a boolean value")


def _parse_rules_env(env_name: str) -> tuple[TemporaryUnschedulableRule, ...]:
    raw_value = os.getenv(env_name)
    if not raw_value:
        return DEFAULT_TEMPORARY_UNSCHEDULABLE_RULES

    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"{env_name} must be valid JSON") from exc

    if not isinstance(payload, list) or not payload:
        raise ConfigurationError(f"{env_name} must be a non-empty JSON array")

    rules: list[TemporaryUnschedulableRule] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ConfigurationError(f"{env_name}[{index}] must be an object")
        rules.append(_parse_rule(item, env_name=env_name, index=index))
    return tuple(rules)


def _parse_rule(
    payload: dict[str, object], *, env_name: str, index: int
) -> TemporaryUnschedulableRule:
    try:
        error_code = str(payload["error_code"]).strip()
        duration_minutes = int(payload["duration_minutes"])
        description = str(payload["description"]).strip()
    except KeyError as exc:
        raise ConfigurationError(
            f"{env_name}[{index}] is missing required field: {exc.args[0]}"
        ) from exc
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"{env_name}[{index}] contains an invalid numeric field"
        ) from exc

    keywords_raw = payload.get("keywords", [])
    if isinstance(keywords_raw, str):
        keywords = tuple(
            keyword.strip() for keyword in keywords_raw.split(",") if keyword.strip()
        )
    elif isinstance(keywords_raw, list):
        keywords = tuple(
            str(keyword).strip() for keyword in keywords_raw if str(keyword).strip()
        )
    else:
        raise ConfigurationError(f"{env_name}[{index}].keywords must be a string or array")

    if not error_code or duration_minutes <= 0 or not description or not keywords:
        raise ConfigurationError(
            f"{env_name}[{index}] must provide error_code, positive duration_minutes, "
            "description, and at least one keyword"
        )

    return TemporaryUnschedulableRule(
        error_code=error_code,
        duration_minutes=duration_minutes,
        keywords=keywords,
        description=description,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
