from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from app.models.flow import ProvisionEvent, ProvisionFlow
from app.models.schemas import (
    ProvisionEventResponse,
    ProvisionFlowDetailResponse,
    ProvisionFlowSummaryResponse,
)

REDACTED_VALUE = "[redacted]"
SENSITIVE_KEY_PARTS = (
    "access_key",
    "access-token",
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "client_secret",
    "id_token",
    "password",
    "refresh-token",
    "refresh_token",
    "secret",
    "token",
)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in SENSITIVE_KEY_PARTS):
                redacted[key] = REDACTED_VALUE
            else:
                redacted[key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def flow_summary_response(flow: ProvisionFlow) -> ProvisionFlowSummaryResponse:
    return ProvisionFlowSummaryResponse(
        flow_id=flow.flow_id,
        email=flow.email,
        user_id=flow.user_id,
        group_id=flow.group_id,
        assignment_mode=flow.assignment_mode.value,
        status=flow.status.value,
        account_name=flow.account_name,
        oauth_account_id=flow.oauth_account_id,
        error_message=flow.error_message,
        created_at=flow.created_at,
        updated_at=flow.updated_at,
    )


def event_response(event: ProvisionEvent) -> ProvisionEventResponse:
    return ProvisionEventResponse(
        event_id=event.event_id,
        flow_id=event.flow_id,
        event_type=event.event_type.value,
        status=event.status.value,
        message=event.message,
        details=redact_sensitive(event.details),
        created_at=event.created_at,
    )


def flow_detail_response(
    flow: ProvisionFlow,
    *,
    oauth_redirect_uri: str,
    events: list[ProvisionEvent],
) -> ProvisionFlowDetailResponse:
    summary = flow_summary_response(flow)
    return ProvisionFlowDetailResponse(
        **summary.model_dump(),
        state=flow.state,
        assignment_reason=flow.assignment_reason,
        oauth_url=flow.oauth_url,
        oauth_redirect_uri=_oauth_redirect_uri_from_url(flow.oauth_url) or oauth_redirect_uri,
        oauth_exchange_payload=redact_sensitive(flow.oauth_exchange_payload),
        events=[event_response(event) for event in events],
    )


def _oauth_redirect_uri_from_url(oauth_url: str | None) -> str | None:
    if not oauth_url:
        return None
    parsed = urlparse(oauth_url)
    params = parse_qs(parsed.query or parsed.fragment)
    values = params.get("redirect_uri") or []
    return values[0] if values else None
