from __future__ import annotations

import logging
import re
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
from app.models.flow import FlowStatus, ProvisionFlow
from app.models.schemas import ProvisionCompleteResponse, ProvisionStartResponse
from app.stores.base import FlowStore

logger = logging.getLogger(__name__)


class ProvisioningService:
    def __init__(
        self,
        flow_store: FlowStore,
        sub2api_client: Sub2APIClient,
        default_user_password: str,
        group_name_prefix: str,
        openai_oauth_redirect_uri: str,
    ) -> None:
        self.flow_store = flow_store
        self.sub2api_client = sub2api_client
        self.default_user_password = default_user_password
        self.group_name_prefix = group_name_prefix
        self.openai_oauth_redirect_uri = openai_oauth_redirect_uri

    def start_flow(self, email: str) -> ProvisionStartResponse:
        logger.info("Starting provisioning flow for email=%s", email)
        flow_id = str(uuid.uuid4())
        state = secrets.token_urlsafe(24)
        group_name = self._build_group_name(email)

        group = self.sub2api_client.create_group(group_name)
        user = self.sub2api_client.create_user(email=email, password=self.default_user_password)
        self.sub2api_client.replace_user_group(user_id=user["id"], group_id=group["id"])
        oauth = self.sub2api_client.generate_openai_auth_url(
            email=email,
            state=state,
            redirect_uri=self.openai_oauth_redirect_uri,
        )

        flow = ProvisionFlow(
            flow_id=flow_id,
            email=email,
            user_id=user["id"],
            group_id=group["id"],
            state=state,
            status=FlowStatus.pending_oauth,
            account_name=email,
            oauth_url=oauth["url"],
        )
        self.flow_store.save(flow)
        logger.info(
            "Provisioning flow created | flow_id=%s | user_id=%s | group_id=%s",
            flow_id,
            user["id"],
            group["id"],
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
            exchange = self.sub2api_client.exchange_openai_code(
                code=code,
                state=state,
                redirect_uri=self.openai_oauth_redirect_uri,
            )
            account = self.sub2api_client.create_openai_account_from_oauth(
                name=flow.email,
                oauth_payload=exchange["exchange"],
                group_id=flow.group_id,
            )
            self.sub2api_client.bind_account_to_group(
                account_id=account["id"],
                group_id=flow.group_id,
            )
        except Exception as exc:
            logger.exception("OAuth completion failed | flow_id=%s", flow.flow_id)
            flow.status = FlowStatus.failed
            flow.error_message = str(exc)
            flow.updated_at = datetime.now(timezone.utc)
            self.flow_store.update(flow)
            raise

        flow.status = FlowStatus.completed
        flow.oauth_account_id = account["id"]
        flow.oauth_exchange_payload = exchange["exchange"]
        flow.error_message = None
        flow.updated_at = datetime.now(timezone.utc)
        self.flow_store.update(flow)
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
        return flow

    def _build_group_name(self, email: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", email).strip("-").lower()
        group_name = f"{self.group_name_prefix}{slug}"
        return group_name[:128]

    def _first_param(self, params: dict[str, list[str]], key: str) -> str | None:
        values = params.get(key) or []
        if not values:
            return None
        return values[0]
