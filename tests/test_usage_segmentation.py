from __future__ import annotations

from datetime import datetime, timezone

from app.models.operational_data import OperationalDataSnapshot
from app.models.usage_segmentation import UsageSegment
from app.services.operational_data import SOURCE_USER_API_KEYS, SOURCE_USER_USAGE, SOURCE_USERS
from app.services.usage_segmentation import UsageSegmentationService
from app.stores.postgres import PostgresFlowStore


def test_usage_segmentation_refresh_classifies_and_persists_records(app_env: dict[str, str]) -> None:
    store = PostgresFlowStore(app_env["database_url"])
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    store.save_operational_data_snapshot(
        OperationalDataSnapshot(
            source_key=SOURCE_USERS,
            observed_at=now,
            collected_at=now,
            payload=[
                {"id": 101, "email": "heavy@example.com", "balance": 20.0},
                {"id": 202, "email": "spike@example.com", "balance": 10.0},
                {"id": 303, "email": "idle@example.com", "balance": 1.0},
            ],
        )
    )
    store.save_operational_data_snapshot(
        OperationalDataSnapshot(
            source_key=SOURCE_USER_USAGE,
            observed_at=now,
            collected_at=now,
            payload={
                "101": {
                    "5h": {"total_cost": 1.0},
                    "7d": {"total_cost": 60.0},
                    "30d": {"total_cost": 180.0},
                },
                "202": {
                    "5h": {"total_cost": 2.0},
                    "30d": {"total_cost": 3.0},
                },
                "303": {},
            },
        )
    )
    store.save_operational_data_snapshot(
        OperationalDataSnapshot(
            source_key=SOURCE_USER_API_KEYS,
            observed_at=now,
            collected_at=now,
            payload={
                "101": {"items": [{"id": "key-101"}], "total": 1},
                "202": {"items": [{"id": "key-202"}], "total": 1},
                "303": {"items": [], "total": 0},
            },
        )
    )

    result = UsageSegmentationService(store).refresh(now=now)

    assert result.user_count == 3
    assert result.segment_counts["heavy"] == 1
    assert result.segment_counts["spike"] == 1
    assert result.segment_counts["idle"] == 1
    heavy = store.get_user_usage_segment(101)
    spike = store.get_user_usage_segment(202)
    idle = store.get_user_usage_segment(303)
    assert heavy is not None
    assert heavy.segment == UsageSegment.heavy
    assert heavy.daily_average_by_window["30d"] == 6.0
    assert spike is not None
    assert spike.segment == UsageSegment.spike
    assert spike.short_term_ratio is not None
    assert spike.short_term_ratio > 3.0
    assert idle is not None
    assert idle.segment == UsageSegment.idle
