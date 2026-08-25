from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv

from app.errors import ConfigurationError

load_dotenv()

CONFIG_PATH_ENV = "CONFIG_PATH"
DEFAULT_CONFIG_PATH = "config.yaml"
OPERATIONAL_RUNTIME_INTERVAL_SECONDS = 60
API_KEY_GROUP_SELECTION_FIRST = "first"
API_KEY_GROUP_SELECTION_RANDOM = "random"
API_KEY_GROUP_SELECTION_MODES = (
    API_KEY_GROUP_SELECTION_FIRST,
    API_KEY_GROUP_SELECTION_RANDOM,
)

REMOVED_CONFIG_PATHS: tuple[tuple[str, ...], ...] = (
    ("storage",),
    ("storage", "sqlite_db_path"),
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
    "DATABASE_URL",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "SQLITE_DB_PATH",
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
    "codex-auto-review",
    "gpt-5.3-codex-spark",
    "gpt-5.5",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-image-2",
)


@dataclass(frozen=True)
class Sub2APIProvisioningDefaults:
    # Fallback platform: used only when a provisioning request (or key creation)
    # names no platform of its own — an explicit `platform` on the request always
    # wins. Deliberately a free-form string, not an enum: the platform set is
    # whatever the upstream groups declare (today openai and grok), and every
    # platform decision downstream reads `group.platform` rather than this value.
    group_platform: str = "openai"
    # Unused since upstream's POST /admin/accounts turned out to have no `provider`
    # field at all — sidecar was writing a key nothing reads. Kept as an accepted
    # (ignored) setting so existing configs and env vars do not fail to load.
    account_provider: str = "openai"
    # Fallback platform stamped on the account payload, same explicit-wins rule as
    # group_platform above.
    account_platform: str = "openai"
    account_type: str = "oauth"
    account_apikey_type: str = "api_key"
    # Scoped to `account_platform` — see DEFAULT_PLATFORM_ONLY_FIELDS. Other
    # platforms send no websockets extras unless they set this themselves.
    account_ws_mode: str = "context_pool"
    account_concurrency: int = 6
    account_temporary_unschedulable: bool = True
    account_temporary_unschedulable_rules: tuple[TemporaryUnschedulableRule, ...] = (
        DEFAULT_TEMPORARY_UNSCHEDULABLE_RULES
    )
    # Scoped to `account_platform` — see DEFAULT_PLATFORM_ONLY_FIELDS. Other
    # platforms send no model_mapping unless they list their own models.
    account_model_whitelist: tuple[str, ...] = DEFAULT_ACCOUNT_MODEL_WHITELIST
    # Written to `credentials.base_url` on account creation when non-empty. Empty
    # for openai, whose account host upstream fills in itself.
    account_base_url: str = ""
    # Free-form keys merged into the account's `extra`. Platform switches that are
    # neither derived from the OAuth exchange nor modelled as their own setting
    # (grok's `grok_client_tool_cache_enabled`, for one) live here.
    account_extra: Mapping[str, Any] = field(default_factory=dict)
    account_priority: int = 1
    account_rate_multiplier: int = 1
    account_auto_pause_on_expired: bool = True
    # Per-platform overrides of the settings above: platform name -> {field: value}.
    # The flat fields are the base every platform starts from (except the two in
    # DEFAULT_PLATFORM_ONLY_FIELDS, which only reach `account_platform`); an entry
    # here only replaces the keys it names, so a config that never mentions
    # per_platform keeps behaving exactly as it did before this field existed.
    # Defaults to the built-in entries (see BUILTIN_PLATFORM_PROVISIONING_OVERRIDES)
    # so a stock install — and any code constructing this template directly —
    # already matches upstream.
    per_platform: Mapping[str, Mapping[str, Any]] = field(
        default_factory=lambda: _copy_platform_overrides(
            BUILTIN_PLATFORM_PROVISIONING_OVERRIDES
        )
    )

    def for_platform(self, platform: str | None) -> "Sub2APIProvisioningDefaults":
        """This template as it applies to one platform: base + that platform's overrides.

        Every account-building path resolves its defaults through here, so adding a
        platform is a config (or built-in table) edit rather than a new branch at
        each call site.

        Most base settings are genuinely cross-platform, but the two in
        DEFAULT_PLATFORM_ONLY_FIELDS describe openai-shaped accounts and are scoped
        to `account_platform` alone: a platform that does not name them explicitly
        gets them emptied rather than inheriting. Silently stamping one platform's
        model list (or the openai responses-transport keys) onto another platform's
        account would misconfigure it in a way nothing downstream flags.
        """
        key = str(platform or "").strip()
        overrides = dict(self.per_platform.get(key) or {}) if key else {}
        resolved: dict[str, Any] = {}

        if key and key != self.account_platform:
            for name, unset_value in DEFAULT_PLATFORM_ONLY_FIELDS.items():
                if name not in overrides:
                    resolved[name] = unset_value

        resolved.update(overrides)
        if "account_extra" in resolved:
            # The one merged key: base extras are common ground, the platform entry
            # adds to them and wins per key. Every other override replaces outright.
            resolved["account_extra"] = {
                **dict(self.account_extra),
                **dict(resolved["account_extra"]),
            }
        if not resolved:
            return self
        return replace(self, **resolved)


# Base settings that describe an openai-shaped account rather than an account in
# general, mapped to the value that means "do not send this". They apply to
# `account_platform` (the configured default platform) and to no one else, so any
# other platform must opt in through `per_platform.<platform>`:
#   * account_model_whitelist -> `credentials.model_mapping`. A model list is only
#     meaningful for the platform that serves those models; upstream falls back to
#     its own runtime mapping when the key is absent.
#   * account_ws_mode -> the `openai_oauth_responses_websockets_v2_*` extras, whose
#     key names are openai's transport, not a general account feature.
DEFAULT_PLATFORM_ONLY_FIELDS: Mapping[str, Any] = {
    "account_model_whitelist": (),
    "account_ws_mode": "",
}


# Platform overrides sidecar ships with, applied unless the config names the same
# key for the same platform. They exist so a stock install already creates accounts
# that match what upstream itself writes for that platform.
BUILTIN_PLATFORM_PROVISIONING_OVERRIDES: Mapping[str, Mapping[str, Any]] = {
    "grok": {
        # Identity mapping over the models grok accounts serve, mirroring the
        # hand-configured grok accounts upstream.
        "account_model_whitelist": (
            "composer-2.5",
            "grok-4.5",
            "grok-4.6",
        ),
        # The host upstream's own BuildAccountCredentials pins for grok OAuth
        # accounts. (Not to be confused with the API-key host, api.x.ai — a
        # different endpoint for a different account type.)
        "account_base_url": "https://cli-chat-proxy.grok.com/v1",
        "account_extra": {"grok_client_tool_cache_enabled": True},
        # `openai_oauth_responses_websockets_v2_*` describes the openai responses
        # transport; live grok accounts carry no such keys, and an empty ws mode is
        # what suppresses them.
        "account_ws_mode": "",
        # Concurrency, priority, rate multiplier and the temporary-unschedulable
        # rules are deliberately not overridden: grok accounts want the same values
        # the base template already carries.
    },
}


def _copy_platform_overrides(
    overrides: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {platform: dict(values) for platform, values in overrides.items()}


@dataclass(frozen=True)
class Sub2APIUpstream:
    upstream_id: str
    name: str
    base_url: str
    admin_api_key: str
    admin_api_key_env: str
    request_timeout_seconds: int
    provisioning_defaults: Sub2APIProvisioningDefaults


@dataclass(frozen=True)
class AccountInvalidAlertWhitelist:
    ids: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroupAlertWhitelist:
    ids: tuple[str, ...] = ()
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_base_url: str
    app_base_path: str
    openai_oauth_redirect_uri: str
    sub2api_upstreams: tuple[Sub2APIUpstream, ...]
    default_sub2api_upstream_id: str
    app_auth_username: str = "admin"
    app_auth_password: str | None = None
    app_access_key_ttl_hours: int = 12
    request_timeout_seconds: int = 30
    database_pool_max_size: int = 10
    sub2api_usage_log_max_items: int = 100_000
    sub2api_request_max_retries: int = 2
    sub2api_api_keys_fetch_concurrency: int = 8
    sub2api_page_fetch_concurrency: int = 8
    api_key_group_selection: str = API_KEY_GROUP_SELECTION_FIRST
    account_invalid_alert_whitelist: AccountInvalidAlertWhitelist = (
        AccountInvalidAlertWhitelist()
    )
    group_alert_whitelist: GroupAlertWhitelist = GroupAlertWhitelist()

    @property
    def default_sub2api_upstream(self) -> Sub2APIUpstream:
        return self.get_sub2api_upstream(self.default_sub2api_upstream_id)

    def get_sub2api_upstream(self, upstream_id: str | None = None) -> Sub2APIUpstream:
        selected_id = (upstream_id or self.default_sub2api_upstream_id).strip()
        for upstream in self.sub2api_upstreams:
            if upstream.upstream_id == selected_id:
                return upstream
        raise ConfigurationError(f"Unknown Sub2API upstream_id: {selected_id}")

    @classmethod
    def from_env(cls) -> "Settings":
        config = _load_config()
        _reject_removed_settings(config)
        missing: list[str] = []
        values: dict[str, Any] = {}

        for field_name, env_name, config_path in (
            ("app_base_url", "APP_BASE_URL", ("app", "base_url")),
            (
                "openai_oauth_redirect_uri",
                "OPENAI_OAUTH_REDIRECT_URI",
                ("openai", "oauth_redirect_uri"),
            ),
        ):
            value = _env_string(env_name) if not config_path else _string_setting(config, env_name, config_path)
            if value is None:
                missing.append(env_name if not config_path else f"{_config_label(config_path)} or {env_name}")
            else:
                values[field_name] = value

        database_url = _database_url_setting(config, missing)
        if database_url is not None:
            values["database_url"] = database_url

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
        values["database_pool_max_size"] = _int_setting(
            config,
            "DATABASE_POOL_MAX_SIZE",
            ("database", "pool_max_size"),
            default=10,
        )
        if values["database_pool_max_size"] <= 0:
            raise ConfigurationError("DATABASE_POOL_MAX_SIZE must be greater than zero")
        values["sub2api_usage_log_max_items"] = _int_setting(
            config,
            "SUB2API_USAGE_LOG_MAX_ITEMS",
            ("sub2api", "usage_log_max_items"),
            default=100_000,
        )
        if values["sub2api_usage_log_max_items"] < 0:
            raise ConfigurationError(
                "SUB2API_USAGE_LOG_MAX_ITEMS must be zero (unlimited) or greater"
            )
        values["sub2api_request_max_retries"] = _int_setting(
            config,
            "SUB2API_REQUEST_MAX_RETRIES",
            ("sub2api", "request_max_retries"),
            default=2,
        )
        if values["sub2api_request_max_retries"] < 0:
            raise ConfigurationError(
                "SUB2API_REQUEST_MAX_RETRIES must be zero or greater"
            )
        values["sub2api_api_keys_fetch_concurrency"] = _int_setting(
            config,
            "SUB2API_API_KEYS_FETCH_CONCURRENCY",
            ("sub2api", "api_keys_fetch_concurrency"),
            default=8,
        )
        if values["sub2api_api_keys_fetch_concurrency"] <= 0:
            raise ConfigurationError(
                "SUB2API_API_KEYS_FETCH_CONCURRENCY must be greater than zero"
            )
        values["sub2api_page_fetch_concurrency"] = _int_setting(
            config,
            "SUB2API_PAGE_FETCH_CONCURRENCY",
            ("sub2api", "page_fetch_concurrency"),
            default=8,
        )
        if values["sub2api_page_fetch_concurrency"] <= 0:
            raise ConfigurationError(
                "SUB2API_PAGE_FETCH_CONCURRENCY must be greater than zero"
            )
        values["api_key_group_selection"] = _api_key_group_selection_setting(config)
        values["account_invalid_alert_whitelist"] = _account_invalid_alert_whitelist_setting(
            config
        )
        values["group_alert_whitelist"] = _group_alert_whitelist_setting(config)
        values["app_access_key_ttl_hours"] = _int_setting(
            config,
            "APP_ACCESS_KEY_TTL_HOURS",
            ("app", "access_key_ttl_hours"),
            default=12,
        )

        if values["app_access_key_ttl_hours"] <= 0:
            raise ConfigurationError("APP_ACCESS_KEY_TTL_HOURS must be greater than zero")

        upstreams = _sub2api_upstreams_setting(
            config,
            default_timeout_seconds=values["request_timeout_seconds"],
            missing=missing,
        )
        if upstreams:
            values["sub2api_upstreams"] = upstreams
            values["default_sub2api_upstream_id"] = upstreams[0].upstream_id

        if missing:
            raise ConfigurationError(
                "Missing required configuration: " + ", ".join(sorted(missing))
            )

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


def _sub2api_upstreams_setting(
    config: Mapping[str, Any],
    *,
    default_timeout_seconds: int,
    missing: list[str],
) -> tuple[Sub2APIUpstream, ...] | None:
    upstreams_payload = _config_value(config, ("sub2api", "upstreams"))
    defaults = _provisioning_defaults_setting(config)
    if defaults.account_concurrency <= 0:
        raise ConfigurationError("SUB2API_ACCOUNT_CONCURRENCY must be greater than zero")

    if upstreams_payload is None:
        missing.append("sub2api.upstreams")
        return None

    if not isinstance(upstreams_payload, list) or not upstreams_payload:
        raise ConfigurationError("sub2api.upstreams must be a non-empty array")

    upstreams: list[Sub2APIUpstream] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(upstreams_payload, start=1):
        source = f"sub2api.upstreams[{index}]"
        if not isinstance(item, Mapping):
            raise ConfigurationError(f"{source} must be an object")
        upstream_id = _required_string_item(item, "id", source=source)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", upstream_id):
            raise ConfigurationError(
                f"{source}.id must use 1-64 URL-safe letters, numbers, underscores, or hyphens"
            )
        if upstream_id in seen_ids:
            raise ConfigurationError(f"Duplicate Sub2API upstream id: {upstream_id}")
        seen_ids.add(upstream_id)

        base_url = _required_string_item(item, "base_url", source=source)
        admin_api_key_env = _required_string_item(
            item,
            "admin_api_key_env",
            source=source,
        )
        admin_api_key = _env_string(admin_api_key_env)
        if admin_api_key is None:
            missing.append(admin_api_key_env)

        raw_timeout = item.get("request_timeout_seconds")
        timeout_seconds = (
            default_timeout_seconds
            if raw_timeout in (None, "")
            else _parse_int_value(raw_timeout, source=f"{source}.request_timeout_seconds")
        )
        if timeout_seconds <= 0:
            raise ConfigurationError(f"{source}.request_timeout_seconds must be greater than zero")

        upstreams.append(
            Sub2APIUpstream(
                upstream_id=upstream_id,
                name=str(item.get("name") or upstream_id).strip() or upstream_id,
                base_url=base_url,
                admin_api_key=admin_api_key or "",
                admin_api_key_env=admin_api_key_env,
                request_timeout_seconds=timeout_seconds,
                provisioning_defaults=defaults,
            )
        )

    return tuple(upstreams)


def _required_string_item(payload: Mapping[str, Any], key: str, *, source: str) -> str:
    raw_value = payload.get(key)
    if raw_value is None or raw_value == "":
        raise ConfigurationError(f"{source}.{key} must be a non-empty string")
    if isinstance(raw_value, str):
        value = raw_value.strip()
    elif isinstance(raw_value, (int, float, bool)):
        value = str(raw_value).strip()
    else:
        raise ConfigurationError(f"{source}.{key} must be a string")
    if not value:
        raise ConfigurationError(f"{source}.{key} must be a non-empty string")
    return value


def _provisioning_defaults_setting(config: Mapping[str, Any]) -> Sub2APIProvisioningDefaults:
    return Sub2APIProvisioningDefaults(
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
        account_apikey_type=_string_setting(
            config,
            "SUB2API_ACCOUNT_APIKEY_TYPE",
            ("sub2api", "provisioning_defaults", "account_apikey_type"),
            default="api_key",
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
            default=6,
        ),
        account_temporary_unschedulable=_bool_setting(
            config,
            "SUB2API_ACCOUNT_TEMPORARY_UNSCHEDULABLE",
            ("sub2api", "provisioning_defaults", "account_temporary_unschedulable"),
            default=True,
        ),
        account_temporary_unschedulable_rules=_rules_setting(config),
        account_model_whitelist=_account_model_whitelist_setting(config),
        account_base_url=_string_setting(
            config,
            "SUB2API_ACCOUNT_BASE_URL",
            ("sub2api", "provisioning_defaults", "account_base_url"),
            default="",
        ),
        account_extra=_account_extra_setting(config),
        account_priority=_int_setting(
            config,
            "SUB2API_ACCOUNT_PRIORITY",
            ("sub2api", "provisioning_defaults", "account_priority"),
            default=1,
        ),
        account_rate_multiplier=_int_setting(
            config,
            "SUB2API_ACCOUNT_RATE_MULTIPLIER",
            ("sub2api", "provisioning_defaults", "account_rate_multiplier"),
            default=1,
        ),
        account_auto_pause_on_expired=_bool_setting(
            config,
            "SUB2API_ACCOUNT_AUTO_PAUSE_ON_EXPIRED",
            ("sub2api", "provisioning_defaults", "account_auto_pause_on_expired"),
            default=True,
        ),
        per_platform=_per_platform_provisioning_setting(config),
    )


def _database_url_setting(
    config: Mapping[str, Any], missing: list[str]
) -> str | None:
    url = _string_config_setting(config, ("database", "url"))
    username = _string_config_setting(config, ("database", "username"))
    name = _string_config_setting(config, ("database", "name"))
    password = _env_string("POSTGRES_PASSWORD")
    for label, value in (
        ("database.url", url),
        ("database.username", username),
        ("database.name", name),
        ("POSTGRES_PASSWORD", password),
    ):
        if value is None:
            missing.append(label)
    if url is None or username is None or name is None or password is None:
        return None

    port = _int_config_setting(config, ("database", "port"), default=5432)

    if port <= 0 or port > 65535:
        raise ConfigurationError("database.port must be between 1 and 65535")

    normalized_host = url.strip()
    normalized_username = username.strip()
    normalized_name = name.strip()
    if not normalized_host:
        raise ConfigurationError("database.url must not be empty")
    if not normalized_username:
        raise ConfigurationError("database.username must not be empty")
    if not normalized_name:
        raise ConfigurationError("database.name must not be empty")
    if (
        "://" in normalized_host
        or "/" in normalized_host
        or "?" in normalized_host
        or "#" in normalized_host
        or any(char.isspace() for char in normalized_host)
    ):
        raise ConfigurationError("database.url must be a host name or IP address")

    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"

    return (
        f"postgresql://{quote(normalized_username, safe='')}:{quote(password, safe='')}"
        f"@{normalized_host}:{port}/{quote(normalized_name, safe='')}"
    )


def _string_config_setting(
    config: Mapping[str, Any],
    config_path: tuple[str, ...],
    *,
    default: str | None = None,
) -> str | None:
    raw_value = _config_value(config, config_path)
    if raw_value is None or raw_value == "":
        return default
    if isinstance(raw_value, str):
        return raw_value
    if isinstance(raw_value, (int, float, bool)):
        return str(raw_value)

    raise ConfigurationError(f"{_config_label(config_path)} must be a string")


def _int_config_setting(
    config: Mapping[str, Any],
    config_path: tuple[str, ...],
    *,
    default: int,
) -> int:
    raw_value = _config_value(config, config_path)
    if raw_value is None or raw_value == "":
        return default
    return _parse_int_value(raw_value, source=_config_label(config_path))


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


def _api_key_group_selection_setting(config: Mapping[str, Any]) -> str:
    value = _string_setting(
        config,
        "SUB2API_API_KEY_GROUP_SELECTION",
        ("sub2api", "api_key_group_selection"),
        default=API_KEY_GROUP_SELECTION_FIRST,
    )
    normalized = (value or API_KEY_GROUP_SELECTION_FIRST).strip().lower()
    if normalized not in API_KEY_GROUP_SELECTION_MODES:
        raise ConfigurationError(
            "SUB2API_API_KEY_GROUP_SELECTION must be one of: "
            + ", ".join(API_KEY_GROUP_SELECTION_MODES)
        )
    return normalized


def _account_invalid_alert_whitelist_setting(
    config: Mapping[str, Any],
) -> AccountInvalidAlertWhitelist:
    return AccountInvalidAlertWhitelist(
        ids=_string_list_setting(
            config,
            "NOTIFICATION_ACCOUNT_INVALID_WHITELIST_IDS",
            ("notifications", "account_invalid_whitelist", "ids"),
        ),
        names=_string_list_setting(
            config,
            "NOTIFICATION_ACCOUNT_INVALID_WHITELIST_NAMES",
            ("notifications", "account_invalid_whitelist", "names"),
        ),
        emails=_string_list_setting(
            config,
            "NOTIFICATION_ACCOUNT_INVALID_WHITELIST_EMAILS",
            ("notifications", "account_invalid_whitelist", "emails"),
        ),
    )


def _group_alert_whitelist_setting(
    config: Mapping[str, Any],
) -> GroupAlertWhitelist:
    return GroupAlertWhitelist(
        ids=_string_list_setting(
            config,
            "NOTIFICATION_GROUP_WHITELIST_IDS",
            ("notifications", "group_whitelist", "ids"),
        ),
        names=_string_list_setting(
            config,
            "NOTIFICATION_GROUP_WHITELIST_NAMES",
            ("notifications", "group_whitelist", "names"),
        ),
    )


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


def _account_extra_setting(config: Mapping[str, Any]) -> Mapping[str, Any]:
    env_name = "SUB2API_ACCOUNT_EXTRA_JSON"
    env_value = _env_string(env_name)
    if env_value is not None:
        try:
            payload = json.loads(env_value)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"{env_name} must be valid JSON") from exc
        return _parse_account_extra_payload(payload, source=env_name)

    config_path = ("sub2api", "provisioning_defaults", "account_extra")
    payload = _config_value(config, config_path)
    if payload is None:
        return {}
    return _parse_account_extra_payload(payload, source=_config_label(config_path))


def _parse_account_extra_payload(payload: Any, *, source: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ConfigurationError(f"{source} must be a mapping of extra keys to values")
    extra: dict[str, Any] = {}
    for key, value in payload.items():
        name = str(key).strip()
        if not name:
            raise ConfigurationError(f"{source} contains an empty key name")
        extra[name] = value
    return extra


# Which template fields a per-platform entry may override, and how each one's raw
# value is read. Adding a knob to the platform dimension means adding it here — the
# account builders never look at a platform name themselves.
PER_PLATFORM_OVERRIDE_PARSERS: Mapping[str, Any] = {
    "account_type": lambda value, source: _parse_platform_string(value, source=source),
    "account_apikey_type": lambda value, source: _parse_platform_string(
        value, source=source
    ),
    # Deliberately allowed to be empty: an empty ws mode is how a platform opts out
    # of the openai websockets extras entirely.
    "account_ws_mode": lambda value, source: _parse_platform_string(
        value, source=source, allow_empty=True
    ),
    "account_base_url": lambda value, source: _parse_platform_string(
        value, source=source, allow_empty=True
    ),
    "account_concurrency": lambda value, source: _parse_int_value(value, source=source),
    "account_priority": lambda value, source: _parse_int_value(value, source=source),
    "account_rate_multiplier": lambda value, source: _parse_int_value(
        value, source=source
    ),
    "account_auto_pause_on_expired": lambda value, source: _parse_bool_value(
        value, source=source
    ),
    "account_temporary_unschedulable": lambda value, source: _parse_bool_value(
        value, source=source
    ),
    "account_temporary_unschedulable_rules": lambda value, source: _parse_rules_payload(
        value, source=source
    ),
    # Empty list allowed: that is how a platform says "send no model_mapping".
    "account_model_whitelist": lambda value, source: _parse_string_list_payload(
        value, source=source, allow_empty=True
    ),
    "account_extra": lambda value, source: _parse_account_extra_payload(
        value, source=source
    ),
}


def _parse_platform_string(
    raw_value: Any, *, source: str, allow_empty: bool = False
) -> str:
    if raw_value is None:
        value = ""
    elif isinstance(raw_value, str):
        value = raw_value.strip()
    elif isinstance(raw_value, (int, float, bool)):
        value = str(raw_value).strip()
    else:
        raise ConfigurationError(f"{source} must be a string")
    if not value and not allow_empty:
        raise ConfigurationError(f"{source} must be a non-empty string")
    return value


def _per_platform_provisioning_setting(
    config: Mapping[str, Any],
) -> Mapping[str, Mapping[str, Any]]:
    """Read `provisioning_defaults.per_platform`, overlaid on the built-in entries.

    The overlay is per key, not per platform: configuring `grok.account_concurrency`
    leaves grok's built-in model whitelist and base_url in place, so an operator
    tuning one knob does not silently drop the rest of the shipped template.
    """
    env_name = "SUB2API_PROVISIONING_PER_PLATFORM_JSON"
    env_value = _env_string(env_name)
    if env_value is not None:
        try:
            payload = json.loads(env_value)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"{env_name} must be valid JSON") from exc
        source = env_name
    else:
        config_path = ("sub2api", "provisioning_defaults", "per_platform")
        payload = _config_value(config, config_path)
        source = _config_label(config_path)

    overrides = _copy_platform_overrides(BUILTIN_PLATFORM_PROVISIONING_OVERRIDES)
    if payload is None:
        return overrides
    if not isinstance(payload, Mapping):
        raise ConfigurationError(f"{source} must be a mapping of platform to settings")

    for platform, values in payload.items():
        platform_name = str(platform).strip()
        if not platform_name:
            raise ConfigurationError(f"{source} contains an empty platform name")
        platform_source = f"{source}.{platform_name}"
        if not isinstance(values, Mapping):
            raise ConfigurationError(f"{platform_source} must be a mapping")
        resolved = overrides.setdefault(platform_name, {})
        for key, value in values.items():
            field_name = str(key).strip()
            parser = PER_PLATFORM_OVERRIDE_PARSERS.get(field_name)
            if parser is None:
                raise ConfigurationError(
                    f"{platform_source}.{field_name} is not an overridable "
                    "provisioning default; supported keys: "
                    f"{', '.join(sorted(PER_PLATFORM_OVERRIDE_PARSERS))}"
                )
            resolved[field_name] = parser(value, f"{platform_source}.{field_name}")

    return overrides


def _string_list_setting(
    config: Mapping[str, Any],
    env_name: str,
    config_path: tuple[str, ...],
) -> tuple[str, ...]:
    env_value = _env_string(env_name)
    if env_value is not None:
        return _parse_string_list_payload(env_value, source=env_name, allow_empty=True)

    payload = _config_value(config, config_path)
    if payload is None:
        return ()
    return _parse_string_list_payload(
        payload,
        source=_config_label(config_path),
        allow_empty=True,
    )


def _parse_string_list_payload(
    payload: Any,
    *,
    source: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
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

    if not cleaned and not allow_empty:
        raise ConfigurationError(f"{source} must contain at least one value")

    return tuple(cleaned)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
