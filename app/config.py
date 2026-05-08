from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.errors import ConfigurationError

load_dotenv()

CONFIG_PATH_ENV = "CONFIG_PATH"
DEFAULT_CONFIG_PATH = "config.yaml"


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


class ProvisioningAssignmentMode(str, Enum):
    dedicated = "dedicated"
    managed_pool = "managed_pool"


class AutoRotationUsageWindow(str, Enum):
    window_5h = "5h"
    window_1d = "1d"
    window_7d = "7d"
    window_30d = "30d"


@dataclass(frozen=True)
class AutoRotationSettings:
    enabled: bool = False
    interval_seconds: int = 0
    cooldown_minutes: int = 0
    usage_window: AutoRotationUsageWindow = AutoRotationUsageWindow.window_1d
    usage_thresholds: tuple[float, ...] = ()
    imbalance_epsilon: float = 0.0
    improvement_delta: float = 0.0


@dataclass(frozen=True)
class Settings:
    sub2api_base_url: str
    sub2api_admin_api_key: str
    app_base_url: str
    openai_oauth_redirect_uri: str
    sub2api_provisioning_defaults: Sub2APIProvisioningDefaults
    assignment_mode: ProvisioningAssignmentMode = ProvisioningAssignmentMode.dedicated
    auto_rotation: AutoRotationSettings = AutoRotationSettings()
    app_auth_username: str = "admin"
    app_auth_password: str | None = None
    app_access_key_ttl_hours: int = 12
    sqlite_db_path: str = "data/sub2api-sidecar.db"
    default_user_password: str = "ChangeMe123!"
    group_name_prefix: str = "openai-oauth-"
    request_timeout_seconds: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        config = _load_config()
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
            ("default_user_password", "DEFAULT_USER_PASSWORD"),
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
        values["group_name_prefix"] = _string_setting(
            config,
            "GROUP_NAME_PREFIX",
            ("provisioning", "group_name_prefix"),
            default="openai-oauth-",
        )
        values["app_auth_username"] = _string_setting(
            config,
            "APP_AUTH_USERNAME",
            ("app", "auth_username"),
            default="admin",
        )
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
            account_temporary_unschedulable=_bool_setting(
                config,
                "SUB2API_ACCOUNT_TEMPORARY_UNSCHEDULABLE",
                ("sub2api", "provisioning_defaults", "account_temporary_unschedulable"),
                default=True,
            ),
            account_temporary_unschedulable_rules=_rules_setting(config),
        )
        values["assignment_mode"] = _assignment_mode_setting(config)
        values["auto_rotation"] = AutoRotationSettings(
            enabled=_bool_setting(
                config,
                "AUTO_ROTATION_ENABLED",
                ("auto_rotation", "enabled"),
                default=False,
            ),
            interval_seconds=_int_setting(
                config,
                "AUTO_ROTATION_INTERVAL_SECONDS",
                ("auto_rotation", "interval_seconds"),
                default=0,
            ),
            cooldown_minutes=_int_setting(
                config,
                "AUTO_ROTATION_COOLDOWN_MINUTES",
                ("auto_rotation", "cooldown_minutes"),
                default=0,
            ),
            usage_window=_usage_window_setting(config),
            usage_thresholds=_thresholds_setting(config),
            imbalance_epsilon=_float_setting(
                config,
                "AUTO_ROTATION_IMBALANCE_EPSILON",
                ("auto_rotation", "imbalance_epsilon"),
                default=0.0,
            ),
            improvement_delta=_float_setting(
                config,
                "AUTO_ROTATION_IMPROVEMENT_DELTA",
                ("auto_rotation", "improvement_delta"),
                default=0.0,
            ),
        )

        if values["auto_rotation"].interval_seconds < 0:
            raise ConfigurationError("AUTO_ROTATION_INTERVAL_SECONDS must be >= 0")
        if values["auto_rotation"].cooldown_minutes < 0:
            raise ConfigurationError("AUTO_ROTATION_COOLDOWN_MINUTES must be >= 0")
        if values["auto_rotation"].imbalance_epsilon < 0:
            raise ConfigurationError("AUTO_ROTATION_IMBALANCE_EPSILON must be >= 0")
        if values["auto_rotation"].improvement_delta < 0:
            raise ConfigurationError("AUTO_ROTATION_IMPROVEMENT_DELTA must be >= 0")

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


def _float_setting(
    config: Mapping[str, Any],
    env_name: str,
    config_path: tuple[str, ...],
    *,
    default: float,
) -> float:
    env_value = _env_string(env_name)
    if env_value is not None:
        return _parse_float_value(env_value, source=env_name)

    raw_value = _config_value(config, config_path)
    if raw_value is None or raw_value == "":
        return default
    return _parse_float_value(raw_value, source=_config_label(config_path))


def _parse_float_value(raw_value: Any, *, source: str) -> float:
    if isinstance(raw_value, bool):
        raise ConfigurationError(f"{source} must be numeric")
    try:
        return float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{source} must be numeric") from exc


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


def _assignment_mode_setting(config: Mapping[str, Any]) -> ProvisioningAssignmentMode:
    env_name = "PROVISIONING_ASSIGNMENT_MODE"
    config_path = ("provisioning", "assignment_mode")
    raw_value = _env_string(env_name)
    source = env_name
    if raw_value is None:
        raw_value = _config_value(config, config_path)
        source = _config_label(config_path)
    if raw_value is None or raw_value == "":
        raw_value = ProvisioningAssignmentMode.dedicated.value
        source = env_name

    try:
        return ProvisioningAssignmentMode(str(raw_value).strip())
    except ValueError as exc:
        raise ConfigurationError(
            f"{source} must be one of: {', '.join(mode.value for mode in ProvisioningAssignmentMode)}"
        ) from exc


def _usage_window_setting(config: Mapping[str, Any]) -> AutoRotationUsageWindow:
    env_name = "AUTO_ROTATION_USAGE_WINDOW"
    config_path = ("auto_rotation", "usage_window")
    raw_value = _env_string(env_name)
    source = env_name
    if raw_value is None:
        raw_value = _config_value(config, config_path)
        source = _config_label(config_path)
    if raw_value is None or raw_value == "":
        raw_value = AutoRotationUsageWindow.window_1d.value
        source = env_name

    try:
        return AutoRotationUsageWindow(str(raw_value).strip())
    except ValueError as exc:
        raise ConfigurationError(
            f"{source} must be one of: {', '.join(window.value for window in AutoRotationUsageWindow)}"
        ) from exc


def _thresholds_setting(config: Mapping[str, Any]) -> tuple[float, ...]:
    env_name = "AUTO_ROTATION_USAGE_THRESHOLDS_JSON"
    env_value = _env_string(env_name)
    if env_value is not None:
        try:
            payload = json.loads(env_value)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"{env_name} must be valid JSON") from exc
        return _parse_thresholds_payload(payload, source=env_name)

    config_path = ("auto_rotation", "usage_thresholds")
    payload = _config_value(config, config_path)
    if payload is None:
        return ()

    return _parse_thresholds_payload(payload, source=_config_label(config_path))


def _parse_thresholds_payload(payload: Any, *, source: str) -> tuple[float, ...]:
    if not isinstance(payload, list):
        raise ConfigurationError(f"{source} must be an array")

    thresholds: list[float] = []
    last_value: float | None = None
    for index, item in enumerate(payload, start=1):
        if isinstance(item, bool):
            raise ConfigurationError(f"{source}[{index}] must be numeric")
        try:
            value = float(item)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"{source}[{index}] must be numeric") from exc
        if value < 0:
            raise ConfigurationError(f"{source}[{index}] must be >= 0")
        if last_value is not None and value < last_value:
            raise ConfigurationError(f"{source} must be sorted in ascending order")
        thresholds.append(value)
        last_value = value
    return tuple(thresholds)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
