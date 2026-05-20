from __future__ import annotations

from datetime import datetime, timezone

from app.models.operational_data import OperationalDataSnapshot
from app.services.group_usage import GroupUsageService
from app.services.operational_data import SOURCE_GROUP_USAGE, SOURCE_GROUPS, SOURCE_USERS
from app.stores.postgres import PostgresFlowStore


def test_group_usage_refresh_calculates_and_persists_records(app_env: dict[str, str]) -> None:
    store = PostgresFlowStore(app_env["database_url"])
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    store.save_operational_data_snapshot(
        OperationalDataSnapshot(
            source_key=SOURCE_GROUPS,
            observed_at=now,
            collected_at=now,
            payload=[
                {
                    "id": 11,
                    "name": "rotation-low",
                    "group_kind": "standard",
                    "platform": "openai",
                    "status": "active",
                    "is_exclusive": True,
                },
                {
                    "id": 22,
                    "name": "rotation-high",
                    "group_kind": "standard",
                    "platform": "openai",
                    "status": "active",
                    "is_exclusive": True,
                },
            ],
        )
    )
    store.save_operational_data_snapshot(
        OperationalDataSnapshot(
            source_key=SOURCE_USERS,
            observed_at=now,
            collected_at=now,
            payload=[
                {"id": 101, "email": "a@example.com", "current_group_id": 11},
                {"id": 202, "email": "b@example.com", "current_group_id": 22},
                {"id": 303, "email": "c@example.com", "current_group_id": 22},
            ],
        )
    )
    store.save_operational_data_snapshot(
        OperationalDataSnapshot(
            source_key=SOURCE_GROUP_USAGE,
            observed_at=now,
            collected_at=now,
            payload={
                "11": {
                    "5h": {
                        "total_actual_cost": 1.0,
                        "total_requests": 2,
                        "total_tokens": 100,
                        "source": "usage_logs",
                    },
                    "30d": {
                        "total_actual_cost": 60.0,
                        "total_requests": 30,
                        "total_tokens": 3000,
                        "total_account_cost": 59.0,
                        "source": "dashboard_groups",
                    },
                },
                "22": {
                    "5h": {
                        "total_actual_cost": 0.0,
                        "total_requests": 0,
                        "total_tokens": 0,
                        "source": "usage_logs",
                    },
                    "30d": {
                        "total_actual_cost": 15.0,
                        "total_requests": 15,
                        "total_tokens": 1500,
                        "source": "dashboard_groups",
                    },
                },
            },
        )
    )

    result = GroupUsageService(store).refresh(now=now)

    assert result.group_count == 2
    assert result.window_counts["5h"] == 2
    assert result.window_counts["30d"] == 2
    low = store.get_group_usage_segment(11)
    high = store.get_group_usage_segment(22)
    assert low is not None
    assert low.member_count == 1
    assert low.usage_by_window["5h"] == 1.0
    assert low.daily_average_by_window["30d"] == 2.0
    assert low.short_term_ratio == 2.4
    assert low.request_count_by_window["30d"] == 30
    assert low.account_cost_by_window["30d"] == 59.0
    assert low.source_by_window["5h"] == "usage_logs"
    assert high is not None
    assert high.member_count == 2
    assert high.usage_by_window["30d"] == 15.0
