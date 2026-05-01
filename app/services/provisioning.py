from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from app.clients.sub2api import Sub2APIClient
from app.config import ProvisioningAssignmentMode
from app.errors import (
    FlowNotFoundError,
    InvalidOAuthCallbackPayloadError,
    InvalidOAuthStateError,
    RotationPoolEmptyError,
)
from app.models.flow import (
    AssignmentMode,
    FlowStatus,
    ProvisionEvent,
    ProvisionEventStatus,
    ProvisionEventType,
    ProvisionFlow,
)
from app.models.schemas import ProvisionCompleteResponse, ProvisionStartResponse
from app.services.rotation import RotationService
from app.stores.base import FlowStore
from app.stores.sqlite import SQLiteFlowStore

logger = logging.getLogger(__name__)


class ProvisioningService:
    def __init__(
        self,
        flow_store: FlowStore,
        sub2api_client: Sub2APIClient,
        default_user_password: str,
        group_name_prefix: str,
        openai_oauth_redirect_uri: str,
        assignment_mode: ProvisioningAssignmentMode,
        rotation_store: SQLiteFlowStore,
        rotation_service: RotationService,
    ) -> None:
        self.flow_store = flow_store
        self.sub2api_client = sub2api_client
        self.default_user_password = default_user_password
        self.group_name_prefix = group_name_prefix
        self.openai_oauth_redirect_uri = openai_oauth_redirect_uri
        self.assignment_mode = assignment_mode
        self.rotation_store = rotation_store
        self.rotation_service = rotation_service

    def start_flow(self, email: str) -> ProvisionStartResponse:
        logger.info("Starting provisioning flow for email=%s", email)
        flow_id = str(uuid.uuid4())
        state = secrets.token_urlsafe(24)
        self._record_event(
            flow_id=flow_id,
            event_type=ProvisionEventType.start_requested,
            status=ProvisionEventStatus.info,
            message="Provisioning flow requested",
            details={"email": email},
        )
        try:
            user = self.sub2api_client.create_user(
                email=email,
                password=self.default_user_password,
            )
            self._record_event(
                flow_id=flow_id,
                event_type=ProvisionEventType.user_created,
                status=ProvisionEventStatus.succeeded,
                message="Sub2API user created",
                details={"user_id": user["id"], "email": email},
            )
            group_id, assignment_mode, assignment_reason = self._resolve_group_assignment(email)
            self._record_event(
                flow_id=flow_id,
                event_type=ProvisionEventType.group_resolved,
                status=ProvisionEventStatus.succeeded,
                message="Target group assignment resolved",
                details={
                    "group_id": group_id,
                    "assignment_mode": assignment_mode.value,
                    "reason": assignment_reason,
                },
            )
            self.sub2api_client.set_user_group(user_id=user["id"], group_id=group_id)
            self._record_event(
                flow_id=flow_id,
                event_type=ProvisionEventType.user_bound,
                status=ProvisionEventStatus.succeeded,
                message="User bound to target group",
                details={"user_id": user["id"], "group_id": group_id},
            )
            oauth = self.sub2api_client.generate_openai_auth_url(
                email=email,
                state=state,
                redirect_uri=self.openai_oauth_redirect_uri,
            )
            self._record_event(
                flow_id=flow_id,
                event_type=ProvisionEventType.oauth_url_generated,
                status=ProvisionEventStatus.succeeded,
                message="OpenAI OAuth handoff URL generated",
                details={"redirect_uri": self.openai_oauth_redirect_uri},
            )
        except Exception as exc:
            self._record_event(
                flow_id=flow_id,
                event_type=ProvisionEventType.failed,
                status=ProvisionEventStatus.failed,
                message="Provisioning flow failed during start",
                details={"error": str(exc)},
            )
            raise

        flow = ProvisionFlow(
            flow_id=flow_id,
            email=email,
            user_id=user["id"],
            group_id=group_id,
            state=state,
            status=FlowStatus.pending_oauth,
            assignment_mode=assignment_mode,
            assignment_reason=assignment_reason,
            account_name=email,
            oauth_url=oauth["url"],
        )
        self.flow_store.save(flow)
        self._record_event(
            flow_id=flow_id,
            event_type=ProvisionEventType.pending_oauth,
            status=ProvisionEventStatus.info,
            message="Provisioning flow is pending OAuth callback",
            details={"state": state},
        )
        logger.info(
            "Provisioning flow created | flow_id=%s | user_id=%s | group_id=%s",
            flow_id,
            user["id"],
            group_id,
        )

        return ProvisionStartResponse(
            flow_id=flow.flow_id,
            email=flow.email,
            user_id=flow.user_id,
            group_id=flow.group_id,
            account_name=flow.account_name,
            oauth_url=flow.oauth_url or "",
            oauth_redirect_uri=self.openai_oauth_redirect_uri,
        )

    def complete_oauth_from_callback_url(self, callback_url: str) -> ProvisionCompleteResponse:
        code, state = self.parse_oauth_callback_url(callback_url)
        flow = self.complete_oauth(code=code, state=state)
        return ProvisionCompleteResponse(
            flow_id=flow.flow_id,
            email=flow.email,
            group_id=flow.group_id,
            oauth_account_id=flow.oauth_account_id,
            status=flow.status.value,
        )

    def complete_oauth(self, code: str, state: str) -> ProvisionFlow:
        if not state:
            raise InvalidOAuthStateError("Missing OAuth state")

        flow = self.flow_store.get_by_state(state)
        if not flow:
            raise FlowNotFoundError("No provisioning flow found for the provided state")

        logger.info("Completing OAuth flow | flow_id=%s | email=%s", flow.flow_id, flow.email)
        try:
            self._record_event(
                flow_id=flow.flow_id,
                event_type=ProvisionEventType.callback_parsed,
                status=ProvisionEventStatus.succeeded,
                message="OAuth callback parsed",
                details={"state": state},
            )
            exchange = self.sub2api_client.exchange_openai_code(
                code=code,
                state=state,
                redirect_uri=self.openai_oauth_redirect_uri,
            )
            self._record_event(
                flow_id=flow.flow_id,
                event_type=ProvisionEventType.oauth_exchanged,
                status=ProvisionEventStatus.succeeded,
                message="OAuth code exchanged",
                details={
                    "provider_user_id": exchange["exchange"].get("provider_user_id"),
                    "received_token_payload": True,
                },
            )
            account = self.sub2api_client.create_openai_account_from_oauth(
                name=flow.email,
                oauth_payload=exchange["exchange"],
                group_id=flow.group_id,
            )
            self._record_event(
                flow_id=flow.flow_id,
                event_type=ProvisionEventType.account_created,
                status=ProvisionEventStatus.succeeded,
                message="OpenAI OAuth account created",
                details={"account_id": account["id"], "group_id": flow.group_id},
            )
            self.sub2api_client.bind_account_to_group(
                account_id=account["id"],
                group_id=flow.group_id,
            )
            self._record_event(
                flow_id=flow.flow_id,
                event_type=ProvisionEventType.account_bound,
                status=ProvisionEventStatus.succeeded,
                message="OpenAI OAuth account bound to group",
                details={"account_id": account["id"], "group_id": flow.group_id},
            )
        except Exception as exc:
            logger.exception("OAuth completion failed | flow_id=%s", flow.flow_id)
            flow.status = FlowStatus.failed
            flow.error_message = str(exc)
            flow.updated_at = datetime.now(timezone.utc)
            self.flow_store.update(flow)
            self._record_event(
                flow_id=flow.flow_id,
                event_type=ProvisionEventType.failed,
                status=ProvisionEventStatus.failed,
                message="OAuth completion failed",
                details={"error": str(exc)},
            )
            raise

        flow.status = FlowStatus.completed
        flow.oauth_account_id = account["id"]
        flow.oauth_exchange_payload = exchange["exchange"]
        flow.error_message = None
        flow.updated_at = datetime.now(timezone.utc)
        self.flow_store.update(flow)
        self._record_event(
            flow_id=flow.flow_id,
            event_type=ProvisionEventType.completed,
            status=ProvisionEventStatus.succeeded,
            message="Provisioning flow completed",
            details={"oauth_account_id": account["id"], "group_id": flow.group_id},
        )
        self.rotation_service.sync_assignment_after_provision(
            user_id=flow.user_id,
            email=flow.email,
            group_id=flow.group_id,
            assignment_mode=flow.assignment_mode,
            reason=flow.assignment_reason,
        )
        logger.info(
            "OAuth flow completed | flow_id=%s | oauth_account_id=%s",
            flow.flow_id,
            flow.oauth_account_id,
        )
        return flow

    def parse_oauth_callback_url(self, callback_url: str) -> tuple[str, str]:
        raw_value = callback_url.strip()
        parsed = urlparse(raw_value)
        candidate_query = parsed.query or parsed.fragment or raw_value.lstrip("?")
        params = parse_qs(candidate_query)

        if params.get("error"):
            error_message = params["error"][0]
            raise InvalidOAuthCallbackPayloadError(
                f"OAuth callback contains error: {error_message}"
            )

        code = self._first_param(params, "code")
        state = self._first_param(params, "state")
        if not code or not state:
            raise InvalidOAuthCallbackPayloadError(
                "Unable to parse code and state from pasted callback URL"
            )
        return code, state

    def fail_flow(self, state: str, message: str) -> ProvisionFlow | None:
        flow = self.flow_store.get_by_state(state)
        if not flow:
            return None
        flow.status = FlowStatus.failed
        flow.error_message = message
        flow.updated_at = datetime.now(timezone.utc)
        self.flow_store.update(flow)
        self._record_event(
            flow_id=flow.flow_id,
            event_type=ProvisionEventType.failed,
            status=ProvisionEventStatus.failed,
            message="Provisioning flow marked failed",
            details={"error": message},
        )
        return flow

    def _build_group_name(self, email: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", email).strip("-").lower()
        group_name = f"{self.group_name_prefix}{slug}"
        return group_name[:128]

    def _resolve_group_assignment(self, email: str) -> tuple[object, AssignmentMode, str]:
        if self.assignment_mode == ProvisioningAssignmentMode.dedicated:
            group_name = self._build_group_name(email)
            group = self.sub2api_client.create_group(group_name)
            return group["id"], AssignmentMode.dedicated, "dedicated provisioning group"

        default_group = self.rotation_store.get_default_rotation_pool_group()
        if default_group is None:
            raise RotationPoolEmptyError(
                "Managed-pool provisioning is enabled but no rotation pool group is available"
            )
        return (
            default_group.group_id,
            AssignmentMode.managed_pool,
            "managed-pool default target",
        )

    def _first_param(self, params: dict[str, list[str]], key: str) -> str | None:
        values = params.get(key) or []
        if not values:
            return None
        return values[0]

    def _record_event(
        self,
        *,
        flow_id: str,
        event_type: ProvisionEventType,
        status: ProvisionEventStatus,
        message: str,
        details: dict[str, object] | None = None,
    ) -> ProvisionEvent:
        event = ProvisionEvent(
            flow_id=flow_id,
            event_type=event_type,
            status=status,
            message=message,
            details=details,
        )
        return self.flow_store.save_provision_event(event)
