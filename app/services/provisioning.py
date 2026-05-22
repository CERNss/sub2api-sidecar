from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from app.clients.sub2api import Sub2APIClient
from app.errors import (
    FlowNotFoundError,
    InvalidOAuthCallbackPayloadError,
    InvalidOAuthStateError,
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
from app.stores.postgres import PostgresFlowStore

logger = logging.getLogger(__name__)


class ProvisioningService:
    def __init__(
        self,
        flow_store: PostgresFlowStore,
        sub2api_client: Sub2APIClient,
        openai_oauth_redirect_uri: str,
    ) -> None:
        self.flow_store = flow_store
        self.sub2api_client = sub2api_client
        self.openai_oauth_redirect_uri = openai_oauth_redirect_uri

    def start_flow(self, email: str) -> ProvisionStartResponse:
        logger.info("Starting provisioning flow for email=%s", email)
        flow_id = str(uuid.uuid4())
        requested_state = secrets.token_urlsafe(24)
        self._record_event(
            flow_id=flow_id,
            event_type=ProvisionEventType.start_requested,
            status=ProvisionEventStatus.info,
            message="Provisioning flow requested",
            details={"email": email},
        )
        try:
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
            oauth = self.sub2api_client.generate_openai_auth_url(
                email=email,
                state=requested_state,
            )
            state = str(oauth.get("state") or requested_state)
            self._record_event(
                flow_id=flow_id,
                event_type=ProvisionEventType.oauth_url_generated,
                status=ProvisionEventStatus.succeeded,
                message="OpenAI OAuth handoff URL generated",
                details={
                    "redirect_uri": self.openai_oauth_redirect_uri,
                    "session_id": oauth.get("session_id"),
                },
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
            group_id=group_id,
            state=state,
            status=FlowStatus.pending_oauth,
            assignment_mode=assignment_mode,
            assignment_reason=assignment_reason,
            account_name=email,
            oauth_url=oauth["url"],
            oauth_session_id=oauth.get("session_id"),
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
            "Provisioning flow created | flow_id=%s | group_id=%s",
            flow_id,
            group_id,
        )

        return ProvisionStartResponse(
            flow_id=flow.flow_id,
            email=flow.email,
            group_id=flow.group_id,
            account_name=flow.account_name,
            oauth_url=flow.oauth_url or "",
            oauth_redirect_uri=self._oauth_redirect_uri_from_url(flow.oauth_url),
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
                session_id=flow.oauth_session_id,
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
            account, account_action = self._resolve_oauth_account(
                email=flow.email,
                oauth_payload=exchange["exchange"],
                group_id=flow.group_id,
            )
            if account_action == "created":
                account_message = "OpenAI OAuth account created"
            else:
                account_message = "Existing OpenAI OAuth account reused"
            self._record_event(
                flow_id=flow.flow_id,
                event_type=ProvisionEventType.account_created,
                status=ProvisionEventStatus.succeeded,
                message=account_message,
                details={
                    "account_id": account["id"],
                    "group_id": flow.group_id,
                    "action": account_action,
                },
            )
            self._record_event(
                flow_id=flow.flow_id,
                event_type=ProvisionEventType.account_bound,
                status=ProvisionEventStatus.succeeded,
                message="OpenAI OAuth account group assignment resolved",
                details={
                    "account_id": account["id"],
                    "group_id": flow.group_id,
                    "action": account_action,
                },
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

    def _oauth_redirect_uri_from_url(self, oauth_url: str | None) -> str:
        if not oauth_url:
            return self.openai_oauth_redirect_uri
        parsed = urlparse(oauth_url)
        params = parse_qs(parsed.query or parsed.fragment)
        redirect_uri = self._first_param(params, "redirect_uri")
        return redirect_uri or self.openai_oauth_redirect_uri

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
        return email[:128]

    def _resolve_group_assignment(self, email: str) -> tuple[object, AssignmentMode, str]:
        group_name = self._build_group_name(email)
        existing_group = self._find_group_by_name(group_name)
        if existing_group is not None:
            return (
                existing_group["id"],
                AssignmentMode.dedicated,
                "existing dedicated provisioning group",
            )
        group = self.sub2api_client.create_group(group_name)
        return group["id"], AssignmentMode.dedicated, "dedicated provisioning group"

    def _find_group_by_name(self, group_name: str) -> dict[str, object] | None:
        groups = self.sub2api_client.list_groups(
            platform=self.sub2api_client.provisioning_defaults.group_platform
        )
        for group in groups:
            if str(group.get("name") or "").strip().lower() == group_name.strip().lower():
                return group
        return None

    def _resolve_oauth_account(
        self,
        *,
        email: str,
        oauth_payload: dict[str, object],
        group_id: object,
    ) -> tuple[dict[str, object], str]:
        existing = self._find_oauth_account(email)
        if existing is None:
            account = self.sub2api_client.create_openai_account_from_oauth(
                name=email,
                oauth_payload=oauth_payload,
                group_id=group_id,
            )
            return account, "created"

        account_id = existing["id"]
        if not self._account_has_group(existing, group_id):
            self.sub2api_client.bind_account_to_group(account_id, group_id)
            return self._existing_account_payload(existing, email), "bound_existing"

        return self._existing_account_payload(existing, email), "already_bound"

    def _find_oauth_account(self, email: str) -> dict[str, object] | None:
        needle = email.strip().lower()
        for account in self.sub2api_client.list_openai_accounts():
            candidates = [
                account.get("name"),
                account.get("email"),
                self._nested_value(account, "raw.name"),
                self._nested_value(account, "raw.email"),
                self._nested_value(account, "raw.account_name"),
                self._nested_value(account, "raw.account_email"),
                self._nested_value(account, "raw.login_email"),
                self._nested_value(account, "raw.extra.email"),
                self._nested_value(account, "raw.credentials.email"),
            ]
            if any(
                str(value).strip().lower() == needle
                for value in candidates
                if value not in (None, "")
            ):
                return account
        return None

    def _existing_account_payload(
        self, account: dict[str, object], fallback_name: str
    ) -> dict[str, object]:
        return {
            "id": account["id"],
            "name": account.get("name") or fallback_name,
            "raw": account,
        }

    def _account_has_group(self, account: dict[str, object], group_id: object) -> bool:
        group_ids = account.get("group_ids")
        if not isinstance(group_ids, list):
            return False
        return any(str(value) == str(group_id) for value in group_ids)

    def _nested_value(self, payload: dict[str, object], path: str) -> object | None:
        current: object = payload
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

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
