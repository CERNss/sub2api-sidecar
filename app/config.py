from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.errors import ConfigurationError

load_dotenv()

CONFIG_PATH_ENV = "CONFIG_PATH"
DEFAULT_CONFIG_PATH = "config.yaml"
OPERATIONAL_RUNTIME_INTERVAL_SECONDS = 60

REMOVED_CONFIG_PATHS: tuple[tuple[str, ...], ...] = (
    ("auto_rotation",),
    ("credit_control",),
    ("operational_data",),
    ("provisioning",),
    ("auto_rotation", "enabled"),
    ("auto_rotation", "interval_seconds"),
    ("auto_rotation", "cooldown_minutes"),
    ("auto_rotation", "usage_window"),
    ("auto_rotation", "usage_thresholds"),
    ("auto_rotation", "imbalance_epsilon"),
    ("auto_rotation", "improvement_delta"),
    ("credit_control", "enabled"),
    ("credit_control", "recharge_tick_seconds"),
    ("operational_data", "enabled"),
    ("operational_data", "expiration"),
    ("operational_data", "collect_interval_seconds"),
    ("provisioning", "assignment_mode"),
)

REMOVED_ENV_NAMES: tuple[str, ...] = (
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
    "PROVISIONING_ASSIGNMENT_MODE",
)


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


DEFAULT_ACCOUNT_MODEL_WHITELIST: tuple[str, ...] = (
    "gpt-5.3-codex",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.5",
)


@dataclass(frozen=True)
class Sub2APIProvisioningDefaults:
    group_platform: str = "openai"
    account_provider: str = "openai"
    account_platform: str = "openai"
    account_type: str = "oauth"
    account_ws_mode: str = "context_pool"
    account_concurrency: int = 5
    account_temporary_unschedulable: bool = True
    account_temporary_unschedulable_rules: tuple[TemporaryUnschedulableRule, ...] = (
        DEFAULT_TEMPORARY_UNSCHEDULABLE_RULES
    )
    account_model_whitelist: tuple[str, ...] = DEFAULT_ACCOUNT_MODEL_WHITELIST


@dataclass(frozen=True)
class Settings:
    sub2api_base_url: str
    sub2api_admin_api_key: str
    app_base_url: str
    app_base_path: str
    openai_oauth_redirect_uri: str
    sub2api_provisioning_defaults: Sub2APIProvisioningDefaults
    app_auth_username: str = "admin"
    app_auth_password: str | None = None
    app_access_key_ttl_hours: int = 12
    sqlite_db_path: str = "data/sub2api-sidecar.db"
    request_timeout_seconds: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        config = _load_config()
        _reject_removed_settings(config)
        missing: list[str] = []
        values: dict[str, Any] = {}

        for field_name, env_name, config_path in (
            ("sub2api_base_url", "SUB2API_BASE_URL", ("sub2api", "base_url")),
            ("app_base_url", "APP_BASE_URL", ("app", "base_url")),
            (
                "openai_oauth_redirect_uri",
                "OPENAI_OAUTH_REDIRECT_URI",
                ("openai", "oauth_redirect_uri"),
            ),
        ):
            value = _string_setting(config, env_name, config_path)
            if value is None:
                missing.append(f"{_config_label(config_path)} or {env_name}")
            else:
                values[field_name] = value

        for field_name, env_name in (
            ("sub2api_admin_api_key", "SUB2API_ADMIN_API_KEY"),
        ):
            value = _env_string(env_name)
            if value is None:
                missing.append(env_name)
            else:
                values[field_name] = value

        if missing:
            raise ConfigurationError(
                "Missing required configuration: " + ", ".join(sorted(missing))
            )

        values["sqlite_db_path"] = _string_setting(
            config,
            "SQLITE_DB_PATH",
            ("storage", "sqlite_db_path"),
            default="data/sub2api-sidecar.db",
        )
        values["app_auth_username"] = _string_setting(
            config,
            "APP_AUTH_USERNAME",
            ("app", "auth_username"),
            default="admin",
        )
        values["app_base_path"] = _base_path_setting(config)
        values["app_auth_password"] = _env_string("APP_AUTH_PASSWORD")
        values["request_timeout_seconds"] = _int_setting(
            config,
            "REQUEST_TIMEOUT_SECONDS",
            ("sub2api", "request_timeout_seconds"),
            default=30,
        )
        values["app_access_key_ttl_hours"] = _int_setting(
            config,
            "APP_ACCESS_KEY_TTL_HOURS",
            ("app", "access_key_ttl_hours"),
            default=12,
        )

        if values["app_access_key_ttl_hours"] <= 0:
            raise ConfigurationError("APP_ACCESS_KEY_TTL_HOURS must be greater than zero")

        values["sub2api_provisioning_defaults"] = Sub2APIProvisioningDefaults(
            group_platform=_string_setting(
                config,
                "SUB2API_GROUP_PLATFORM",
                ("sub2api", "provisioning_defaults", "group_platform"),
                default="openai",
            ),
            account_provider=_string_setting(
                config,
                "SUB2API_ACCOUNT_PROVIDER",
                ("sub2api", "provisioning_defaults", "account_provider"),
                default="openai",
            ),
            account_platform=_string_setting(
                config,
                "SUB2API_ACCOUNT_PLATFORM",
                ("sub2api", "provisioning_defaults", "account_platform"),
                default="openai",
            ),
            account_type=_string_setting(
                config,
                "SUB2API_ACCOUNT_TYPE",
                ("sub2api", "provisioning_defaults", "account_type"),
                default="oauth",
            ),
            account_ws_mode=_string_setting(
                config,
                "SUB2API_ACCOUNT_WS_MODE",
                ("sub2api", "provisioning_defaults", "account_ws_mode"),
                default="context_pool",
            ),
            account_concurrency=_int_setting(
                config,
                "SUB2API_ACCOUNT_CONCURRENCY",
                ("sub2api", "provisioning_defaults", "account_concurrency"),
                default=5,
            ),
            account_temporary_unschedulable=_bool_setting(
                config,
                "SUB2API_ACCOUNT_TEMPORARY_UNSCHEDULABLE",
                ("sub2api", "provisioning_defaults", "account_temporary_unschedulable"),
                default=True,
            ),
            account_temporary_unschedulable_rules=_rules_setting(config),
            account_model_whitelist=_account_model_whitelist_setting(config),
        )
        if values["sub2api_provisioning_defaults"].account_concurrency <= 0:
            raise ConfigurationError("SUB2API_ACCOUNT_CONCURRENCY must be greater than zero")

        return cls(**values)


def _load_config() -> Mapping[str, Any]:
    config_path = os.getenv(CONFIG_PATH_ENV) or DEFAULT_CONFIG_PATH
    return _load_config_file(config_path)


def _load_config_file(config_path: str) -> Mapping[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {}
    if path.is_dir():
        raise ConfigurationError(
            f"{CONFIG_PATH_ENV} must point to a YAML file, got directory: {path}"
        )

    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise ConfigurationError(
            f"Install PyYAML to read {path}, or configure everything through environment variables"
        ) from exc

    with path.open("r", encoding="utf-8") as config_file:
        try:
            payload = yaml.safe_load(config_file) or {}
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"{path} must be valid YAML") from exc

    if not isinstance(payload, Mapping):
        raise ConfigurationError(f"{path} must contain a YAML mapping")

    return payload


def _env_string(env_name: str) -> str | None:
    raw_value = os.getenv(env_name)
    if raw_value is None or raw_value == "":
        return None
    return raw_value


def _config_value(config: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = config
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _config_label(path: tuple[str, ...]) -> str:
    return ".".join(path)


def _reject_removed_settings(config: Mapping[str, Any]) -> None:
    configured_paths = [
        _config_label(path)
        for path in REMOVED_CONFIG_PATHS
        if _config_value(config, path) is not None
    ]
    configured_env = [
        env_name for env_name in REMOVED_ENV_NAMES if _env_string(env_name) is not None
    ]
    if not configured_paths and not configured_env:
        return
    removed = ", ".join(sorted([*configured_paths, *configured_env]))
    raise ConfigurationError(
        "Removed configuration fields are not supported: "
        f"{removed}. Configure runtime switches and operational data expiration "
        "through the authenticated web UI/API."
    )


def _string_setting(
    config: Mapping[str, Any],
    env_name: str,
    config_path: tuple[str, ...],
    *,
    default: str | None = None,
) -> str | None:
    env_value = _env_string(env_name)
    if env_value is not None:
        return env_value

    raw_value = _config_value(config, config_path)
    if raw_value is None or raw_value == "":
        return default
    if isinstance(raw_value, str):
        return raw_value
    if isinstance(raw_value, (int, float, bool)):
        return str(raw_value)

    raise ConfigurationError(f"{_config_label(config_path)} must be a string")


def _int_setting(
    config: Mapping[str, Any],
    env_name: str,
    config_path: tuple[str, ...],
    *,
    default: int,
) -> int:
    env_value = _env_string(env_name)
    if env_value is not None:
        return _parse_int_value(env_value, source=env_name)

    raw_value = _config_value(config, config_path)
    if raw_value is None or raw_value == "":
        return default
    return _parse_int_value(raw_value, source=_config_label(config_path))


def _parse_int_value(raw_value: Any, *, source: str) -> int:
    if isinstance(raw_value, bool):
        raise ConfigurationError(f"{source} must be an integer")
    try:
        return int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{source} must be an integer") from exc


def _bool_setting(
    config: Mapping[str, Any],
    env_name: str,
    config_path: tuple[str, ...],
    *,
    default: bool,
) -> bool:
    env_value = _env_string(env_name)
    if env_value is not None:
        return _parse_bool_value(env_value, source=env_name)

    raw_value = _config_value(config, config_path)
    if raw_value is None or raw_value == "":
        return default
    return _parse_bool_value(raw_value, source=_config_label(config_path))


def _parse_bool_value(raw_value: Any, *, source: str) -> bool:
    if isinstance(raw_value, bool):
        return raw_value

    normalized = str(raw_value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{source} must be a boolean value")


def _base_path_setting(config: Mapping[str, Any]) -> str:
    raw_value = _string_setting(
        config,
        "APP_BASE_PATH",
        ("app", "base_path"),
        default="",
    )
    return _normalize_base_path(raw_value or "")


def _normalize_base_path(raw_value: str) -> str:
    value = raw_value.strip()
    if value in {"", "/"}:
        return ""
    if "://" in value or "?" in value or "#" in value or any(char.isspace() for char in value):
        raise ConfigurationError("APP_BASE_PATH must be a URL path prefix such as /sidecar")
    if not value.startswith("/"):
        value = f"/{value}"
    return value.rstrip("/")


def _rules_setting(config: Mapping[str, Any]) -> tuple[TemporaryUnschedulableRule, ...]:
    env_name = "SUB2API_ACCOUNT_TEMPORARY_UNSCHEDULABLE_RULES_JSON"
    env_value = _env_string(env_name)
    if env_value is not None:
        try:
            payload = json.loads(env_value)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"{env_name} must be valid JSON") from exc
        return _parse_rules_payload(payload, source=env_name)

    config_path = (
        "sub2api",
        "provisioning_defaults",
        "account_temporary_unschedulable_rules",
    )
    payload = _config_value(config, config_path)
    if payload is None:
        return DEFAULT_TEMPORARY_UNSCHEDULABLE_RULES

    return _parse_rules_payload(payload, source=_config_label(config_path))


def _parse_rules_payload(
    payload: Any, *, source: str
) -> tuple[TemporaryUnschedulableRule, ...]:
    if not isinstance(payload, list) or not payload:
        raise ConfigurationError(f"{source} must be a non-empty array")

    rules: list[TemporaryUnschedulableRule] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ConfigurationError(f"{source}[{index}] must be an object")
        rules.append(_parse_rule(item, source=source, index=index))
    return tuple(rules)


def _parse_rule(
    payload: dict[str, object], *, source: str, index: int
) -> TemporaryUnschedulableRule:
    try:
        error_code = str(payload["error_code"]).strip()
        duration_minutes = int(payload["duration_minutes"])
        description = str(payload["description"]).strip()
    except KeyError as exc:
        raise ConfigurationError(
            f"{source}[{index}] is missing required field: {exc.args[0]}"
        ) from exc
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"{source}[{index}] contains an invalid numeric field"
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
        raise ConfigurationError(f"{source}[{index}].keywords must be a string or array")

    if not error_code or duration_minutes <= 0 or not description or not keywords:
        raise ConfigurationError(
            f"{source}[{index}] must provide error_code, positive duration_minutes, "
            "description, and at least one keyword"
        )

    return TemporaryUnschedulableRule(
        error_code=error_code,
        duration_minutes=duration_minutes,
        keywords=keywords,
        description=description,
    )


def _account_model_whitelist_setting(config: Mapping[str, Any]) -> tuple[str, ...]:
    env_name = "SUB2API_ACCOUNT_MODEL_WHITELIST_JSON"
    env_value = _env_string(env_name)
    if env_value is not None:
        try:
            payload = json.loads(env_value)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"{env_name} must be valid JSON") from exc
        return _parse_string_list_payload(payload, source=env_name)

    csv_env_name = "SUB2API_ACCOUNT_MODEL_WHITELIST"
    csv_env_value = _env_string(csv_env_name)
    if csv_env_value is not None:
        return _parse_string_list_payload(csv_env_value, source=csv_env_name)

    config_path = ("sub2api", "provisioning_defaults", "account_model_whitelist")
    payload = _config_value(config, config_path)
    if payload is None:
        return DEFAULT_ACCOUNT_MODEL_WHITELIST

    return _parse_string_list_payload(payload, source=_config_label(config_path))


def _parse_string_list_payload(payload: Any, *, source: str) -> tuple[str, ...]:
    if isinstance(payload, str):
        values = [value.strip() for value in payload.split(",")]
    elif isinstance(payload, list):
        values = []
        for index, value in enumerate(payload, start=1):
            if not isinstance(value, str):
                raise ConfigurationError(f"{source}[{index}] must be a string")
            values.append(value.strip())
    else:
        raise ConfigurationError(f"{source} must be a string or array")

    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        cleaned.append(value)
        seen.add(value)

    if not cleaned:
        raise ConfigurationError(f"{source} must contain at least one value")

    return tuple(cleaned)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
