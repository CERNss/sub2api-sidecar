from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.models.flow import (  # noqa: E402
    AssignmentMode,
    FlowStatus,
    ProvisionEvent,
    ProvisionEventStatus,
    ProvisionEventType,
    ProvisionFlow,
)
from app.models.rotation import UserGroupAssignment  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.stores.base import FlowStore  # noqa: E402
from app.stores.postgres import PostgresFlowStore  # noqa: E402

DEMO_PREFIX = "demo-dashboard-"


def utc_minutes_ago(minutes: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


def delete_existing_demo_rows(store: PostgresFlowStore) -> None:
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM provision_events WHERE flow_id LIKE %s",
            (f"{DEMO_PREFIX}%",),
        )
        connection.execute(
            "DELETE FROM provision_flows WHERE flow_id LIKE %s",
            (f"{DEMO_PREFIX}%",),
        )
        connection.execute(
            "DELETE FROM user_group_assignments WHERE email LIKE %s",
            ("demo-dashboard-%",),
        )
        connection.commit()


def save_event(
    store: FlowStore,
    *,
    flow_id: str,
    event_type: ProvisionEventType,
    status: ProvisionEventStatus,
    message: str,
    created_at: datetime,
    details: dict[str, object] | None = None,
) -> None:
    store.save_provision_event(
        ProvisionEvent(
            flow_id=flow_id,
            event_type=event_type,
            status=status,
            message=message,
            details=details,
            created_at=created_at,
            updated_at=created_at,
        )
    )


def seed_completed_dedicated(store: FlowStore) -> None:
    flow_id = f"{DEMO_PREFIX}completed-dedicated"
    created_at = utc_minutes_ago(95)
    updated_at = utc_minutes_ago(84)
    flow = ProvisionFlow(
        flow_id=flow_id,
        email="demo-dashboard-completed@example.com",
        user_id=2001,
        group_id=22,
        state="demo-state-completed-dedicated",
        status=FlowStatus.completed,
        assignment_mode=AssignmentMode.dedicated,
        assignment_reason="dedicated provisioning group",
        account_name="demo-dashboard-completed@example.com",
        oauth_url="http://localhost:3000/callback?code=mock-code&state=demo-state-completed-dedicated",
        oauth_account_id="acct-demo-completed",
        oauth_exchange_payload={
            "access_token": "demo-access-token",
            "refresh_token": "demo-refresh-token",
            "expires_in": 3600,
            "provider_user_id": "openai-user-demo-completed",
            "scope": "openid profile email",
        },
        created_at=created_at,
        updated_at=updated_at,
    )
    store.save(flow)
    event_rows = [
        (0, ProvisionEventType.start_requested, ProvisionEventStatus.info, "Provisioning flow requested", {"email": flow.email}),
        (1, ProvisionEventType.user_created, ProvisionEventStatus.succeeded, "Sub2API user created", {"user_id": flow.user_id}),
        (2, ProvisionEventType.group_resolved, ProvisionEventStatus.succeeded, "Dedicated target group resolved", {"group_id": flow.group_id}),
        (3, ProvisionEventType.user_bound, ProvisionEventStatus.succeeded, "User bound to target group", {"group_id": flow.group_id}),
        (4, ProvisionEventType.oauth_url_generated, ProvisionEventStatus.succeeded, "OpenAI OAuth handoff URL generated", {"redirect_uri": "http://localhost:3000/callback"}),
        (8, ProvisionEventType.callback_parsed, ProvisionEventStatus.succeeded, "OAuth callback parsed", {"state": flow.state}),
        (9, ProvisionEventType.oauth_exchanged, ProvisionEventStatus.succeeded, "OAuth code exchanged", {"received_token_payload": True}),
        (10, ProvisionEventType.account_created, ProvisionEventStatus.succeeded, "OpenAI OAuth account created", {"account_id": flow.oauth_account_id}),
        (11, ProvisionEventType.account_bound, ProvisionEventStatus.succeeded, "OpenAI OAuth account bound to group", {"group_id": flow.group_id}),
        (12, ProvisionEventType.completed, ProvisionEventStatus.succeeded, "Provisioning flow completed", {"oauth_account_id": flow.oauth_account_id}),
    ]
    for offset, event_type, status, message, details in event_rows:
        save_event(
            store,
            flow_id=flow_id,
            event_type=event_type,
            status=status,
            message=message,
            details=details,
            created_at=created_at + timedelta(minutes=offset),
        )
    store.upsert_user_assignment(
        UserGroupAssignment(
            user_id=flow.user_id,
            email=flow.email,
            current_group_id=flow.group_id,
            current_group_name="mock-rotation-high",
            assignment_mode=flow.assignment_mode,
            last_decision_reason=flow.assignment_reason,
            has_api_keys=True,
            created_at=created_at,
            updated_at=updated_at,
        )
    )


def seed_pending_managed_pool(store: FlowStore) -> None:
    flow_id = f"{DEMO_PREFIX}pending-managed-pool"
    created_at = utc_minutes_ago(42)
    flow = ProvisionFlow(
        flow_id=flow_id,
        email="demo-dashboard-pending@example.com",
        user_id=2002,
        group_id=11,
        state="demo-state-pending-managed-pool",
        status=FlowStatus.pending_oauth,
        assignment_mode=AssignmentMode.managed_pool,
        assignment_reason="managed-pool default target",
        account_name="demo-dashboard-pending@example.com",
        oauth_url="http://localhost:3000/callback?code=mock-code&state=demo-state-pending-managed-pool",
        created_at=created_at,
        updated_at=created_at + timedelta(minutes=4),
    )
    store.save(flow)
    for offset, event_type, status, message, details in [
        (0, ProvisionEventType.start_requested, ProvisionEventStatus.info, "Provisioning flow requested", {"email": flow.email}),
        (1, ProvisionEventType.user_created, ProvisionEventStatus.succeeded, "Sub2API user created", {"user_id": flow.user_id}),
        (2, ProvisionEventType.group_resolved, ProvisionEventStatus.succeeded, "Managed-pool target group resolved", {"group_id": flow.group_id}),
        (3, ProvisionEventType.oauth_url_generated, ProvisionEventStatus.succeeded, "OpenAI OAuth handoff URL generated", {"redirect_uri": "http://localhost:3000/callback"}),
        (4, ProvisionEventType.pending_oauth, ProvisionEventStatus.info, "Provisioning flow is pending OAuth callback", {"state": flow.state}),
    ]:
        save_event(
            store,
            flow_id=flow_id,
            event_type=event_type,
            status=status,
            message=message,
            details=details,
            created_at=created_at + timedelta(minutes=offset),
        )


def seed_failed_callback(store: FlowStore) -> None:
    flow_id = f"{DEMO_PREFIX}failed-callback"
    created_at = utc_minutes_ago(25)
    updated_at = utc_minutes_ago(20)
    flow = ProvisionFlow(
        flow_id=flow_id,
        email="demo-dashboard-failed@example.com",
        user_id=2003,
        group_id=55,
        state="demo-state-failed-callback",
        status=FlowStatus.failed,
        assignment_mode=AssignmentMode.managed_pool,
        assignment_reason="manual callback retry test",
        account_name="demo-dashboard-failed@example.com",
        oauth_url="http://localhost:3000/callback?error=access_denied&state=demo-state-failed-callback",
        error_message="OAuth callback contains error: access_denied",
        created_at=created_at,
        updated_at=updated_at,
    )
    store.save(flow)
    for offset, event_type, status, message, details in [
        (0, ProvisionEventType.start_requested, ProvisionEventStatus.info, "Provisioning flow requested", {"email": flow.email}),
        (1, ProvisionEventType.user_created, ProvisionEventStatus.succeeded, "Sub2API user created", {"user_id": flow.user_id}),
        (2, ProvisionEventType.group_resolved, ProvisionEventStatus.succeeded, "Target group assignment resolved", {"group_id": flow.group_id}),
        (5, ProvisionEventType.failed, ProvisionEventStatus.failed, "Provisioning flow marked failed", {"error": flow.error_message}),
    ]:
        save_event(
            store,
            flow_id=flow_id,
            event_type=event_type,
            status=status,
            message=message,
            details=details,
            created_at=created_at + timedelta(minutes=offset),
        )


def seed_completed_long_detail(store: FlowStore) -> None:
    flow_id = f"{DEMO_PREFIX}completed-long-detail"
    created_at = utc_minutes_ago(12)
    updated_at = utc_minutes_ago(7)
    flow = ProvisionFlow(
        flow_id=flow_id,
        email="demo-dashboard-long.email.alias+layout-overflow-check@example.com",
        user_id=2004,
        group_id=77,
        state="demo-state-long-detail",
        status=FlowStatus.completed,
        assignment_mode=AssignmentMode.managed_pool,
        assignment_reason="managed-pool layout overflow check",
        account_name="demo-dashboard-long.email.alias+layout-overflow-check@example.com",
        oauth_url="http://localhost:3000/callback?code=mock-code&state=demo-state-long-detail",
        oauth_account_id="acct-demo-long-detail",
        oauth_exchange_payload={
            "access_token": "long-demo-access-token",
            "refresh_token": "long-demo-refresh-token",
            "nested": {
                "client_secret": "nested-secret-should-redact",
                "region": "mock-region",
            },
        },
        created_at=created_at,
        updated_at=updated_at,
    )
    store.save(flow)
    for offset, event_type, status, message, details in [
        (0, ProvisionEventType.start_requested, ProvisionEventStatus.info, "Provisioning flow requested", {"email": flow.email}),
        (1, ProvisionEventType.user_created, ProvisionEventStatus.succeeded, "Sub2API user created", {"user_id": flow.user_id}),
        (2, ProvisionEventType.group_resolved, ProvisionEventStatus.succeeded, "Long-name target group resolved", {"group_id": flow.group_id}),
        (3, ProvisionEventType.oauth_exchanged, ProvisionEventStatus.succeeded, "OAuth code exchanged", {"received_token_payload": True}),
        (4, ProvisionEventType.account_created, ProvisionEventStatus.succeeded, "OpenAI OAuth account created", {"account_id": flow.oauth_account_id}),
        (5, ProvisionEventType.completed, ProvisionEventStatus.succeeded, "Provisioning flow completed", {"oauth_account_id": flow.oauth_account_id}),
    ]:
        save_event(
            store,
            flow_id=flow_id,
            event_type=event_type,
            status=status,
            message=message,
            details=details,
            created_at=created_at + timedelta(minutes=offset),
        )
    store.upsert_user_assignment(
        UserGroupAssignment(
            user_id=flow.user_id,
            email=flow.email,
            current_group_id=flow.group_id,
            current_group_name="mock-very-long-dedicated-group-name-for-overflow-layout-check-2026",
            assignment_mode=flow.assignment_mode,
            last_decision_reason=flow.assignment_reason,
            has_api_keys=True,
            created_at=created_at,
            updated_at=updated_at,
        )
    )


def seed() -> None:
    store = PostgresFlowStore(get_settings().database_url)
    delete_existing_demo_rows(store)
    seed_completed_dedicated(store)
    seed_pending_managed_pool(store)
    seed_failed_callback(store)
    seed_completed_long_detail(store)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed local demo dashboard provisioning history.")
    parser.parse_args()
    seed()
    print("Seeded demo dashboard history in PostgreSQL")


if __name__ == "__main__":
    main()
