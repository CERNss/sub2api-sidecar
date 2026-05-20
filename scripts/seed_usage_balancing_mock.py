from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import psycopg

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings  # noqa: E402
from app.models.credit import (  # noqa: E402
    CreditAuditOperation,
    CreditAuditRecord,
    CreditRechargePolicy,
    CreditRechargeRunRecord,
    CreditRunStatus,
    CreditScheduleKind,
    CreditRechargeSchedule,
    CreditTargetScope,
    CreditTargetScopeKind,
)
from app.models.group_usage import GroupUsageSegmentRecord  # noqa: E402
from app.models.operational_data import (  # noqa: E402
    CreditControlRuntimeSettings,
    OperationalDataRuntimeSettings,
    OperationalDataSnapshot,
    ProvisioningRuntimeSettings,
)
from app.models.rotation import (  # noqa: E402
    AutoRotationRuntimeConfig,
    AutoRotationUsageWindow,
    OrchestrationRunKind,
    OrchestrationRunRecord,
    RotationPoolGroup,
    RotationPoolKind,
    RotationTrigger,
    UserGroupAssignment,
)
from app.models.usage_segmentation import (  # noqa: E402
    SEGMENT_LABELS,
    UsageSegment,
    UserUsageSegmentRecord,
)
from app.services.operational_data import (  # noqa: E402
    SOURCE_GROUP_USAGE,
    SOURCE_GROUPS,
    SOURCE_USER_API_KEYS,
    SOURCE_USER_USAGE,
    SOURCE_USERS,
)
from app.stores.postgres import PostgresFlowStore  # noqa: E402

MOCK_PREFIX = "mock-usage-balancing-"
DEFAULT_MOCK_DATABASE = "sub2api_sidecar"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def utc_minutes_ago(minutes: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


def create_database_if_missing(database_url: str) -> None:
    info = psycopg.conninfo.conninfo_to_dict(database_url)
    target_database = info.get("dbname")
    if not target_database:
        raise RuntimeError("database name is missing from connection string")
    admin_info = dict(info)
    admin_info["dbname"] = "postgres"
    admin_url = psycopg.conninfo.make_conninfo("", **admin_info)
    with psycopg.connect(admin_url, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (target_database,),
        ).fetchone()
        if not exists:
            connection.execute(f'CREATE DATABASE "{target_database}"')


def database_url_for_name(database_name: str) -> str:
    settings = get_settings()
    info = psycopg.conninfo.conninfo_to_dict(settings.database_url)
    info["dbname"] = database_name
    return psycopg.conninfo.make_conninfo("", **info)


def require_local_database(database_url: str, *, force: bool) -> None:
    info = psycopg.conninfo.conninfo_to_dict(database_url)
    host = info.get("host", "")
    database = info.get("dbname", "")
    if force:
        return
    if host not in LOCAL_HOSTS:
        raise RuntimeError(
            f"Refusing to seed non-local database host {host!r}. Pass --force for an intentional override."
        )
    if not database.startswith("sub2api_sidecar"):
        raise RuntimeError(
            f"Refusing to seed unexpected database {database!r}. Pass --force for an intentional override."
        )


def delete_existing_mock_rows(store: PostgresFlowStore) -> None:
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM user_usage_segments WHERE user_id_key LIKE %s",
            (f"{MOCK_PREFIX}%",),
        )
        connection.execute(
            "DELETE FROM group_usage_segments WHERE group_id_key LIKE %s",
            (f"{MOCK_PREFIX}%",),
        )
        connection.execute(
            "DELETE FROM user_group_assignments WHERE user_id_key LIKE %s OR email LIKE %s",
            (f"{MOCK_PREFIX}%", f"{MOCK_PREFIX}%"),
        )
        connection.execute(
            "DELETE FROM rotation_pool_groups WHERE group_id_key LIKE %s",
            (f"{MOCK_PREFIX}%",),
        )
        connection.execute(
            "DELETE FROM orchestration_runs WHERE run_id LIKE %s",
            (f"{MOCK_PREFIX}%",),
        )
        connection.execute(
            "DELETE FROM credit_recharge_runs WHERE run_id LIKE %s OR policy_id LIKE %s",
            (f"{MOCK_PREFIX}%", f"{MOCK_PREFIX}%"),
        )
        connection.execute(
            "DELETE FROM credit_recharge_policies WHERE policy_id LIKE %s",
            (f"{MOCK_PREFIX}%",),
        )
        connection.execute(
            "DELETE FROM credit_audit_records WHERE audit_id LIKE %s OR policy_id LIKE %s OR run_id LIKE %s",
            (f"{MOCK_PREFIX}%", f"{MOCK_PREFIX}%", f"{MOCK_PREFIX}%"),
        )
        connection.commit()


def save_snapshot(
    store: PostgresFlowStore,
    *,
    source_key: str,
    payload: object,
    observed_at: datetime,
) -> None:
    store.save_operational_data_snapshot(
        OperationalDataSnapshot(
            source_key=source_key,
            observed_at=observed_at,
            collected_at=observed_at,
            payload=payload,
        )
    )


def user_id(segment: str, index: int) -> str:
    return f"{MOCK_PREFIX}user-{segment}-{index:02d}"


def group_id(name: str) -> str:
    return f"{MOCK_PREFIX}group-{name}"


def make_user(
    *,
    segment: UsageSegment,
    index: int,
    group_key: str,
    balance: float,
    usage_5h: float | None,
    usage_1d: float | None,
    usage_7d: float | None,
    usage_30d: float | None,
    status: str = "active",
) -> dict[str, object]:
    user_key = user_id(segment.value, index)
    return {
        "id": user_key,
        "email": f"{user_key}@example.com",
        "name": f"{segment.value}-{index}",
        "username": f"{segment.value}_{index}",
        "status": status,
        "group_id": group_key,
        "group_name": group_key.removeprefix(f"{MOCK_PREFIX}group-"),
        "current_group_id": group_key,
        "current_group_name": group_key.removeprefix(f"{MOCK_PREFIX}group-"),
        "group_ids": [group_key],
        "balance": balance,
        "balance_display": f"{balance:.2f} credits",
        "balance_unit": "credits",
        "usage": {
            "5h": usage_5h,
            "1d": usage_1d,
            "7d": usage_7d,
            "30d": usage_30d,
        },
        "segment": segment.value,
    }


def daily_average(window: str, value: float | None) -> float | None:
    if value is None:
        return None
    days = {"5h": 5 / 24, "1d": 1, "7d": 7, "30d": 30}[window]
    return value / days


def user_usage_payload(users: Iterable[dict[str, object]]) -> dict[str, dict[str, dict[str, float]]]:
    payload: dict[str, dict[str, dict[str, float]]] = {}
    for user in users:
        usage = user["usage"]
        assert isinstance(usage, dict)
        per_window: dict[str, dict[str, float]] = {}
        for window, value in usage.items():
            if isinstance(value, (int, float)):
                per_window[str(window)] = {
                    "total_cost": float(value),
                    "total_actual_cost": float(value),
                    "total_requests": int(max(1, float(value) * 12)),
                    "total_tokens": int(max(100, float(value) * 1200)),
                }
        payload[str(user["id"])] = per_window
    return payload


def user_api_keys_payload(users: Iterable[dict[str, object]]) -> dict[str, dict[str, object]]:
    payload: dict[str, dict[str, object]] = {}
    for user in users:
        usage = user["usage"]
        assert isinstance(usage, dict)
        key_id = f"key-{user['id']}"
        item = {
            "id": key_id,
            "name": "primary",
            "group_id": user["current_group_id"],
            "group_name": user["current_group_name"],
        }
        for window, value in usage.items():
            if isinstance(value, (int, float)):
                item[f"usage_{window}"] = float(value)
        payload[str(user["id"])] = {"items": [item], "total": 1}
    return payload


def build_users() -> list[dict[str, object]]:
    low = group_id("rotation-low")
    high = group_id("rotation-high")
    balanced = group_id("rotation-balanced")
    landing = group_id("landing-new-users")
    return [
        make_user(segment=UsageSegment.heavy, index=1, group_key=high, balance=18.2, usage_5h=8.5, usage_1d=38.0, usage_7d=220.0, usage_30d=760.0),
        make_user(segment=UsageSegment.heavy, index=2, group_key=high, balance=9.7, usage_5h=7.0, usage_1d=31.0, usage_7d=185.0, usage_30d=610.0),
        make_user(segment=UsageSegment.heavy, index=3, group_key=balanced, balance=5.5, usage_5h=4.8, usage_1d=24.0, usage_7d=155.0, usage_30d=430.0),
        make_user(segment=UsageSegment.active, index=1, group_key=balanced, balance=44.0, usage_5h=1.9, usage_1d=9.5, usage_7d=58.0, usage_30d=195.0),
        make_user(segment=UsageSegment.active, index=2, group_key=low, balance=33.1, usage_5h=1.2, usage_1d=6.5, usage_7d=42.0, usage_30d=146.0),
        make_user(segment=UsageSegment.light, index=1, group_key=low, balance=68.0, usage_5h=0.3, usage_1d=1.4, usage_7d=8.0, usage_30d=28.0),
        make_user(segment=UsageSegment.light, index=2, group_key=low, balance=21.0, usage_5h=0.1, usage_1d=0.8, usage_7d=6.0, usage_30d=18.0),
        make_user(segment=UsageSegment.idle, index=1, group_key=landing, balance=3.0, usage_5h=None, usage_1d=None, usage_7d=None, usage_30d=None, status="inactive"),
        make_user(segment=UsageSegment.spike, index=1, group_key=landing, balance=13.0, usage_5h=3.2, usage_1d=4.0, usage_7d=7.0, usage_30d=12.0),
    ]


def build_groups() -> list[dict[str, object]]:
    return [
        {
            "id": group_id("rotation-low"),
            "name": "rotation-low",
            "type": "standard",
            "group_kind": "standard",
            "platform": "openai",
            "status": "active",
            "is_exclusive": True,
            "is_subscription": False,
            "account_count": 2,
            "active_account_count": 2,
            "rpm_limit": 120,
            "rate_multiplier": 1.2,
            "daily_limit_usd": 50,
        },
        {
            "id": group_id("rotation-high"),
            "name": "rotation-high",
            "type": "standard",
            "group_kind": "standard",
            "platform": "openai",
            "status": "active",
            "is_exclusive": True,
            "is_subscription": False,
            "account_count": 3,
            "active_account_count": 3,
            "rpm_limit": 180,
            "rate_multiplier": 1.5,
            "daily_limit_usd": 80,
        },
        {
            "id": group_id("rotation-balanced"),
            "name": "rotation-balanced",
            "type": "standard",
            "group_kind": "standard",
            "platform": "openai",
            "status": "active",
            "is_exclusive": True,
            "is_subscription": False,
            "account_count": 2,
            "active_account_count": 2,
            "rpm_limit": 150,
            "rate_multiplier": 1.3,
            "daily_limit_usd": 60,
        },
        {
            "id": group_id("landing-new-users"),
            "name": "landing-new-users",
            "type": "standard",
            "group_kind": "standard",
            "platform": "openai",
            "status": "active",
            "is_exclusive": False,
            "is_subscription": False,
            "account_count": 1,
            "active_account_count": 1,
        },
    ]


def group_usage_payload() -> dict[str, dict[str, dict[str, object]]]:
    values = {
        group_id("rotation-low"): {"5h": 2.4, "1d": 10.0, "7d": 64.0, "30d": 245.0},
        group_id("rotation-high"): {"5h": 20.5, "1d": 92.0, "7d": 560.0, "30d": 1800.0},
        group_id("rotation-balanced"): {"5h": 6.7, "1d": 33.5, "7d": 221.0, "30d": 780.0},
        group_id("landing-new-users"): {"5h": 3.2, "1d": 4.0, "7d": 7.0, "30d": 12.0},
    }
    payload: dict[str, dict[str, dict[str, object]]] = {}
    for key, windows in values.items():
        payload[key] = {}
        for window, cost in windows.items():
            payload[key][window] = {
                "group_id": key,
                "window": window,
                "total_requests": int(cost * 10),
                "total_tokens": int(cost * 1500),
                "total_cost": cost,
                "total_actual_cost": cost,
                "total_account_cost": round(cost * 0.97, 4),
                "source": "mock_seed",
            }
    return payload


def build_user_segment_record(user: dict[str, object], now: datetime) -> UserUsageSegmentRecord:
    usage = user["usage"]
    assert isinstance(usage, dict)
    segment = UsageSegment(str(user["segment"]))
    usage_by_window = {
        window: float(value) if isinstance(value, (int, float)) else None
        for window, value in usage.items()
    }
    daily_by_window = {
        window: daily_average(window, value)
        for window, value in usage_by_window.items()
    }
    baseline = usage_by_window.get("30d")
    baseline_daily = daily_by_window.get("30d")
    short_daily = daily_by_window.get("5h")
    medium_daily = daily_by_window.get("7d")
    return UserUsageSegmentRecord(
        user_id=user["id"],
        email=str(user["email"]),
        username=str(user["username"]),
        name=str(user["name"]),
        status=str(user["status"]),
        group_id=user["current_group_id"],
        group_name=str(user["current_group_name"]),
        group_ids=list(user["group_ids"]),  # type: ignore[arg-type]
        balance=float(user["balance"]),
        balance_display=str(user["balance_display"]),
        balance_unit=str(user["balance_unit"]),
        has_api_keys=segment != UsageSegment.idle,
        api_key_count=0 if segment == UsageSegment.idle else 1,
        usage_by_window=usage_by_window,
        daily_average_by_window=daily_by_window,
        baseline_window="30d" if baseline is not None else None,
        baseline_daily_average=baseline_daily,
        short_term_ratio=(
            short_daily / baseline_daily
            if short_daily is not None and baseline_daily not in (None, 0)
            else None
        ),
        medium_term_ratio=(
            medium_daily / baseline_daily
            if medium_daily is not None and baseline_daily not in (None, 0)
            else None
        ),
        runway_days=(
            float(user["balance"]) / baseline_daily
            if baseline_daily not in (None, 0)
            else None
        ),
        known_usage_window_count=sum(value is not None for value in usage_by_window.values()),
        positive_usage_window_count=sum((value or 0) > 0 for value in usage_by_window.values()),
        segment=segment,
        segment_label=SEGMENT_LABELS[segment],
        reasons=[f"mock_seed:{segment.value}", "for_frontend_and_balancing_verification"],
        metadata={"mock": True, "seed": "usage_balancing"},
        observed_at=now,
        refreshed_at=now,
        created_at=now,
        updated_at=now,
    )


def build_group_usage_records(users: list[dict[str, object]], now: datetime) -> list[GroupUsageSegmentRecord]:
    members: dict[str, int] = {}
    for user in users:
        key = str(user["current_group_id"])
        members[key] = members.get(key, 0) + 1
    records: list[GroupUsageSegmentRecord] = []
    for group in build_groups():
        key = str(group["id"])
        windows = group_usage_payload()[key]
        usage_by_window = {
            window: float(payload["total_actual_cost"])
            for window, payload in windows.items()
        }
        records.append(
            GroupUsageSegmentRecord(
                group_id=key,
                group_name=str(group["name"]),
                group_kind=str(group["group_kind"]),
                platform=str(group["platform"]),
                status=str(group["status"]),
                is_exclusive=bool(group["is_exclusive"]),
                is_subscription=bool(group["is_subscription"]),
                member_count=members.get(key, 0),
                usage_by_window=usage_by_window,
                daily_average_by_window={
                    window: daily_average(window, value)
                    for window, value in usage_by_window.items()
                },
                request_count_by_window={
                    window: int(payload["total_requests"])
                    for window, payload in windows.items()
                },
                token_count_by_window={
                    window: int(payload["total_tokens"])
                    for window, payload in windows.items()
                },
                account_cost_by_window={
                    window: float(payload["total_account_cost"])
                    for window, payload in windows.items()
                },
                source_by_window={
                    window: str(payload["source"])
                    for window, payload in windows.items()
                },
                baseline_window="30d",
                baseline_daily_average=daily_average("30d", usage_by_window["30d"]),
                short_term_ratio=daily_average("5h", usage_by_window["5h"]) / daily_average("30d", usage_by_window["30d"]),
                medium_term_ratio=daily_average("7d", usage_by_window["7d"]) / daily_average("30d", usage_by_window["30d"]),
                known_usage_window_count=len(usage_by_window),
                positive_usage_window_count=sum(value > 0 for value in usage_by_window.values()),
                observed_at=now,
                refreshed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    return records


def seed_rotation(store: PostgresFlowStore, users: list[dict[str, object]], now: datetime) -> None:
    for priority, group in enumerate(build_groups()):
        pool_kind = (
            RotationPoolKind.landing
            if str(group["id"]) == group_id("landing-new-users")
            else RotationPoolKind.rotation
        )
        store.upsert_rotation_pool_group(
            RotationPoolGroup(
                group_id=str(group["id"]),
                pool_kind=pool_kind,
                group_name=str(group["name"]),
                group_kind=str(group["group_kind"]),
                platform=str(group["platform"]),
                status=str(group["status"]),
                is_exclusive=bool(group["is_exclusive"]) or pool_kind == RotationPoolKind.rotation,
                is_subscription=False,
                priority=priority,
                created_at=now,
                updated_at=now,
            )
        )
    for user in users:
        if str(user["current_group_id"]) == group_id("landing-new-users"):
            continue
        store.upsert_user_assignment(
            UserGroupAssignment(
                user_id=user["id"],
                email=str(user["email"]),
                current_group_id=user["current_group_id"],
                current_group_name=str(user["current_group_name"]),
                last_decision_reason="mock seed current assignment",
                has_api_keys=True,
                created_at=now,
                updated_at=now,
            )
        )
    store.save_auto_rotation_config(
        AutoRotationRuntimeConfig(
            enabled=False,
            auto_assign_new_users=True,
            cooldown_minutes=0,
            usage_window=AutoRotationUsageWindow.window_5h,
            usage_thresholds=(),
            imbalance_epsilon=0.0,
            improvement_delta=0.0,
            schedule_source_group_ids=(group_id("landing-new-users"),),
            created_at=now,
            updated_at=now,
        )
    )
    store.save_orchestration_run(
        OrchestrationRunRecord(
            run_id=f"{MOCK_PREFIX}preview-run",
            run_kind=OrchestrationRunKind.automatic,
            tag="automatic_preview",
            trigger_type=RotationTrigger.automatic_api,
            dry_run=True,
            status="planned",
            window=AutoRotationUsageWindow.window_5h,
            synced={"seen": len(users), "synced": 7, "new_user_candidates": 2},
            config={"usage_window": "5h", "auto_assign_new_users": True},
            planned=[
                {
                    "user_id": user_id("heavy", 1),
                    "email": f"{user_id('heavy', 1)}@example.com",
                    "source_group_id": group_id("rotation-high"),
                    "target_group_id": group_id("rotation-low"),
                    "trigger_type": RotationTrigger.automatic_api.value,
                    "status": "planned",
                    "reason": "mock preview balances high usage group",
                    "usage_window": "5h",
                    "usage_value": 8.5,
                    "metadata": {
                        "decision_type": "usage_balancing",
                        "source_group_load_before": 20.5,
                        "target_group_load_before": 2.4,
                        "source_group_load_source": "group_usage:mock_seed",
                        "target_group_load_source": "group_usage:mock_seed",
                    },
                }
            ],
            created_at=utc_minutes_ago(8),
            updated_at=utc_minutes_ago(8),
        )
    )


def seed_credit(store: PostgresFlowStore, now: datetime) -> None:
    policy_id = f"{MOCK_PREFIX}credit-policy"
    run_id = f"{MOCK_PREFIX}credit-run"
    store.save_credit_control_runtime_settings(
        CreditControlRuntimeSettings(enabled=False, created_at=now, updated_at=now)
    )
    store.save_operational_data_runtime_settings(
        OperationalDataRuntimeSettings(enabled=False, collect_interval_seconds=60, created_at=now, updated_at=now)
    )
    store.save_provisioning_runtime_settings(
        ProvisioningRuntimeSettings(created_at=now, updated_at=now)
    )
    target_scope = CreditTargetScope(
        kind=CreditTargetScopeKind.balance_threshold,
        balance_below=15.0,
    )
    store.save_credit_policy(
        CreditRechargePolicy(
            policy_id=policy_id,
            name="Mock low-balance top-up",
            enabled=True,
            amount=25.0,
            target_scope=target_scope,
            schedule=CreditRechargeSchedule(
                kind=CreditScheduleKind.daily,
                start_at=now + timedelta(hours=1),
                timezone="Asia/Shanghai",
            ),
            reason_template="mock seed auto top-up for {{email}}",
            next_run_at=now + timedelta(hours=1),
            created_at=now,
            updated_at=now,
        )
    )
    store.save_credit_run(
        CreditRechargeRunRecord(
            run_id=run_id,
            policy_id=policy_id,
            policy_name="Mock low-balance top-up",
            occurrence_key="mock-2026-05-20",
            operation_type=CreditAuditOperation.automatic_recharge,
            status=CreditRunStatus.succeeded,
            dry_run=True,
            amount=25.0,
            target_scope=target_scope,
            reason="mock seed historical run",
            actor="mock-seed",
            started_at=utc_minutes_ago(20),
            finished_at=utc_minutes_ago(19),
            target_count=2,
            success_count=2,
            created_at=utc_minutes_ago(20),
            updated_at=utc_minutes_ago(19),
        )
    )
    store.save_credit_audit(
        CreditAuditRecord(
            audit_id=f"{MOCK_PREFIX}audit-heavy",
            operation_type=CreditAuditOperation.automatic_recharge,
            status="succeeded",
            user_id=user_id("heavy", 2),
            policy_id=policy_id,
            run_id=run_id,
            actor="mock-seed",
            summary="mock recharge for heavy user",
            details={"amount": 25.0, "balance_before": 9.7, "balance_after": 34.7},
            created_at=utc_minutes_ago(19),
        )
    )


def seed(database_url: str, *, create_database: bool) -> dict[str, int]:
    require_local_database(database_url, force=False)
    if create_database:
        create_database_if_missing(database_url)
    store = PostgresFlowStore(database_url)
    now = datetime.now(timezone.utc)
    users = build_users()
    delete_existing_mock_rows(store)
    save_snapshot(store, source_key=SOURCE_GROUPS, payload=build_groups(), observed_at=now)
    save_snapshot(store, source_key=SOURCE_USERS, payload=users, observed_at=now)
    save_snapshot(store, source_key=SOURCE_USER_USAGE, payload=user_usage_payload(users), observed_at=now)
    save_snapshot(store, source_key=SOURCE_USER_API_KEYS, payload=user_api_keys_payload(users), observed_at=now)
    save_snapshot(store, source_key=SOURCE_GROUP_USAGE, payload=group_usage_payload(), observed_at=now)
    user_records = [build_user_segment_record(user, now) for user in users]
    group_records = build_group_usage_records(users, now)
    store.upsert_user_usage_segments(user_records)
    store.upsert_group_usage_segments(group_records)
    seed_rotation(store, users, now)
    seed_credit(store, now)
    return {
        "users": len(users),
        "user_segments": len(user_records),
        "groups": len(build_groups()),
        "group_usage": len(group_records),
        "rotation_pool_groups": 3,
        "landing_pool_groups": 1,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed local mock data for credit-control and usage-balancing frontend verification."
    )
    parser.add_argument(
        "--database",
        default=DEFAULT_MOCK_DATABASE,
        help="Database name to seed, using connection settings from config.yaml. Defaults to sub2api_sidecar.",
    )
    parser.add_argument(
        "--create-database",
        action="store_true",
        help="Create the target local database if it does not exist.",
    )
    args = parser.parse_args()
    database_url = database_url_for_name(args.database)
    summary = seed(database_url, create_database=args.create_database)
    print(
        "Seeded usage-balancing mock data into "
        f"{quote(args.database, safe='')} | "
        + ", ".join(f"{key}={value}" for key, value in summary.items())
    )


if __name__ == "__main__":
    main()
