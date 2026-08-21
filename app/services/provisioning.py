from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from app.clients.sub2api import Sub2APIClient, Sub2APIError
from app.errors import (
    FlowNotFoundError,
    InvalidOAuthCallbackPayloadError,
    InvalidOAuthStateError,
    ProvisioningError,
)
from app.models.flow import (
    AssignmentMode,
    FlowStatus,
    ProvisionEvent,
    ProvisionEventStatus,
    ProvisionEventType,
    ProvisionFlow,
)
from app.models.rotation import RotationPoolGroup, RotationPoolKind
from app.models.schemas import (
    ProvisionApiKeyStartResponse,
    ProvisionCompleteResponse,
    ProvisionStartResponse,
)
from app.stores.postgres import PostgresFlowStore

logger = logging.getLogger(__name__)

# Where each platform's upstream OAuth helper listens for the callback. Used only as
# the display fallback when the generated auth URL carries no `redirect_uri` of its
# own — the URL upstream actually minted always wins.
DEFAULT_OAUTH_REDIRECT_URIS: dict[str, str] = {
    "openai": "http://localhost:1455/auth/callback",
    "grok": "http://127.0.0.1:56121/callback",
}


class ProvisioningService:
    def __init__(
        self,
        flow_store: PostgresFlowStore,
        sub2api_client: Sub2APIClient,
        openai_oauth_redirect_uri: str,
        default_upstream_id: str,
    ) -> None:
        self.flow_store = flow_store
        self.sub2api_client = sub2api_client
        self.openai_oauth_redirect_uri = openai_oauth_redirect_uri
        self.default_upstream_id = default_upstream_id

    def start_flow(self, email: str) -> ProvisionStartResponse:
        return self.start_flow_for_upstream(email=email, upstream_id=self.default_upstream_id)

    def start_flow_for_upstream(
        self,
        *,
        email: str,
        upstream_id: str,
        platform: str | None = None,
        sub2api_client: Sub2APIClient | None = None,
    ) -> ProvisionStartResponse:
        client = sub2api_client or self.sub2api_client
        platform = self._resolve_platform(client, platform)
        logger.info(
            "Starting provisioning flow for email=%s | platform=%s", email, platform
        )
        flow_id = str(uuid.uuid4())
        requested_state = secrets.token_urlsafe(24)
        self._record_event(
            flow_id=flow_id,
            event_type=ProvisionEventType.start_requested,
            status=ProvisionEventStatus.info,
            message="Provisioning flow requested",
            details={"email": email, "platform": platform},
        )
        try:
            # An OAuth handoff needs both the platform's endpoints and its credential
            # assembly, so refuse before any group or account is written rather than
            # halfway through.
            self._require_oauth_platform(platform)
            group_id, assignment_mode, assignment_reason = self._resolve_group_assignment(
                email,
                upstream_id=upstream_id,
                platform=platform,
                sub2api_client=client,
            )
            self._record_event(
                flow_id=flow_id,
                event_type=ProvisionEventType.group_resolved,
                status=ProvisionEventStatus.succeeded,
                message="Target group assignment resolved",
                details={
                    "group_id": group_id,
                    "assignment_mode": assignment_mode.value,
                    "reason": assignment_reason,
                    "platform": platform,
                },
            )
            existing_account = self._find_oauth_account(
                email,
                platform=platform,
                sub2api_client=client,
            )
            if existing_account is not None:
                account, account_action = self._configure_existing_oauth_account(
                    existing_account=existing_account,
                    email=email,
                    group_id=group_id,
                    flow_id=flow_id,
                    platform=platform,
                    sub2api_client=client,
                )
                flow = ProvisionFlow(
                    flow_id=flow_id,
                    upstream_id=upstream_id,
                    email=email,
                    group_id=group_id,
                    state=requested_state,
                    status=FlowStatus.completed,
                    assignment_mode=assignment_mode,
                    assignment_reason=assignment_reason,
                    account_name=email,
                    oauth_url=None,
                    oauth_session_id=None,
                    oauth_account_id=account["id"],
                )
                self.flow_store.save(flow)
                self._record_event(
                    flow_id=flow_id,
                    event_type=ProvisionEventType.account_created,
                    status=ProvisionEventStatus.succeeded,
                    message="Existing OAuth account configured",
                    details={
                        "account_id": account["id"],
                        "group_id": group_id,
                        "action": account_action,
                        "platform": platform,
                    },
                )
                self._record_event(
                    flow_id=flow_id,
                    event_type=ProvisionEventType.account_bound,
                    status=ProvisionEventStatus.succeeded,
                    message="OAuth account group assignment resolved",
                    details={
                        "account_id": account["id"],
                        "group_id": group_id,
                        "action": account_action,
                        "platform": platform,
                    },
                )
                self._record_event(
                    flow_id=flow_id,
                    event_type=ProvisionEventType.completed,
                    status=ProvisionEventStatus.succeeded,
                    message="Provisioning flow completed without OAuth handoff",
                    details={
                        "oauth_account_id": account["id"],
                        "group_id": group_id,
                        "platform": platform,
                    },
                )
                logger.info(
                    "Provisioning flow completed with existing account | flow_id=%s | account_id=%s",
                    flow_id,
                    account["id"],
                )
                return ProvisionStartResponse(
                    upstream_id=flow.upstream_id,
                    flow_id=flow.flow_id,
                    email=flow.email,
                    group_id=flow.group_id,
                    assignment_mode=flow.assignment_mode.value,
                    assignment_reason=flow.assignment_reason,
                    account_name=flow.account_name,
                    status=flow.status.value,
                    oauth_required=False,
                    oauth_account_id=flow.oauth_account_id,
                    oauth_url=None,
                    oauth_redirect_uri=self._default_redirect_uri(platform),
                )

            oauth = client.generate_oauth_auth_url(
                email=email,
                state=requested_state,
                platform=platform,
            )
            state = str(oauth.get("state") or requested_state)
            self._record_event(
                flow_id=flow_id,
                event_type=ProvisionEventType.oauth_url_generated,
                status=ProvisionEventStatus.succeeded,
                message="OAuth handoff URL generated",
                details={
                    "redirect_uri": self._oauth_redirect_uri_from_url(
                        oauth.get("url"), platform
                    ),
                    "session_id": oauth.get("session_id"),
                    "platform": platform,
                },
            )
        except Exception as exc:
            self._record_event(
                flow_id=flow_id,
                event_type=ProvisionEventType.failed,
                status=ProvisionEventStatus.failed,
                message="Provisioning flow failed during start",
                details={"error": str(exc), "platform": platform},
            )
            raise

        flow = ProvisionFlow(
            flow_id=flow_id,
            upstream_id=upstream_id,
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
            details={"state": state, "platform": platform},
        )
        logger.info(
            "Provisioning flow created | flow_id=%s | group_id=%s | platform=%s",
            flow_id,
            group_id,
            platform,
        )

        return ProvisionStartResponse(
            upstream_id=flow.upstream_id,
            flow_id=flow.flow_id,
            email=flow.email,
            group_id=flow.group_id,
            assignment_mode=flow.assignment_mode.value,
            assignment_reason=flow.assignment_reason,
            account_name=flow.account_name,
            status=flow.status.value,
            oauth_required=True,
            oauth_account_id=None,
            oauth_url=flow.oauth_url or "",
            oauth_redirect_uri=self._oauth_redirect_uri_from_url(flow.oauth_url, platform),
        )

    def start_apikey_flow_for_upstream(
        self,
        *,
        name: str,
        api_base_url: str,
        api_key: str,
        upstream_id: str,
        platform: str | None = None,
        sub2api_client: Sub2APIClient | None = None,
    ) -> ProvisionApiKeyStartResponse:
        client = sub2api_client or self.sub2api_client
        # No OAuth support check here: an API key account needs no per-platform
        # endpoints or credential assembly, and upstream applies no platform
        # whitelist, so any platform the operator names is a legal target.
        platform = self._resolve_platform(client, platform)
        logger.info(
            "Starting API key provisioning flow for name=%s | platform=%s", name, platform
        )
        flow_id = str(uuid.uuid4())
        state = secrets.token_urlsafe(24)
        self._record_event(
            flow_id=flow_id,
            event_type=ProvisionEventType.start_requested,
            status=ProvisionEventStatus.info,
            message="API key provisioning flow requested",
            details={"name": name, "platform": platform},
        )
        try:
            group_id, assignment_mode, assignment_reason = self._resolve_group_assignment(
                name,
                upstream_id=upstream_id,
                platform=platform,
                sub2api_client=client,
            )
            self._record_event(
                flow_id=flow_id,
                event_type=ProvisionEventType.group_resolved,
                status=ProvisionEventStatus.succeeded,
                message="Target group assignment resolved",
                details={
                    "group_id": group_id,
                    "assignment_mode": assignment_mode.value,
                    "reason": assignment_reason,
                    "platform": platform,
                },
            )
            account, account_action = self._resolve_apikey_account(
                name=name,
                api_base_url=api_base_url,
                api_key=api_key,
                group_id=group_id,
                flow_id=flow_id,
                platform=platform,
                sub2api_client=client,
            )
            self._record_event(
                flow_id=flow_id,
                event_type=ProvisionEventType.account_created,
                status=ProvisionEventStatus.succeeded,
                message="API key account configured",
                details={
                    "account_id": account["id"],
                    "group_id": group_id,
                    "action": account_action,
                    "platform": platform,
                },
            )
            self._record_event(
                flow_id=flow_id,
                event_type=ProvisionEventType.account_bound,
                status=ProvisionEventStatus.succeeded,
                message="API key account group assignment resolved",
                details={
                    "account_id": account["id"],
                    "group_id": group_id,
                    "action": account_action,
                    "platform": platform,
                },
            )
        except Exception as exc:
            self._record_event(
                flow_id=flow_id,
                event_type=ProvisionEventType.failed,
                status=ProvisionEventStatus.failed,
                message="API key provisioning flow failed during start",
                details={"error": str(exc), "platform": platform},
            )
            raise

        flow = ProvisionFlow(
            flow_id=flow_id,
            upstream_id=upstream_id,
            email=name,
            group_id=group_id,
            state=state,
            status=FlowStatus.completed,
            assignment_mode=assignment_mode,
            assignment_reason=assignment_reason,
            account_name=name,
            oauth_url=None,
            oauth_session_id=None,
            oauth_account_id=account["id"],
        )
        self.flow_store.save(flow)
        self._record_event(
            flow_id=flow_id,
            event_type=ProvisionEventType.completed,
            status=ProvisionEventStatus.succeeded,
            message="API key provisioning flow completed",
            details={
                "account_id": account["id"],
                "group_id": group_id,
                "platform": platform,
            },
        )
        logger.info(
            "API key provisioning flow completed | flow_id=%s | account_id=%s | platform=%s",
            flow_id,
            account["id"],
            platform,
        )
        return ProvisionApiKeyStartResponse(
            upstream_id=flow.upstream_id,
            flow_id=flow.flow_id,
            name=name,
            group_id=group_id,
            assignment_mode=assignment_mode.value,
            assignment_reason=assignment_reason,
            account_name=name,
            status=flow.status.value,
            account_id=account["id"],
        )

    def complete_oauth_from_callback_url(
        self, callback_url: str, flow_id: str | None = None
    ) -> ProvisionCompleteResponse:
        code, state = self.parse_oauth_callback_url(callback_url)
        state = self.resolve_callback_flow(state=state, flow_id=flow_id).state
        flow = self.complete_oauth(code=code, state=state)
        return ProvisionCompleteResponse(
            upstream_id=flow.upstream_id,
            flow_id=flow.flow_id,
            email=flow.email,
            group_id=flow.group_id,
            oauth_account_id=flow.oauth_account_id,
            status=flow.status.value,
        )

    def complete_oauth(self, code: str, state: str) -> ProvisionFlow:
        return self.complete_oauth_with_client(code=code, state=state)

    def complete_oauth_with_client(
        self,
        *,
        code: str,
        state: str,
        sub2api_client: Sub2APIClient | None = None,
    ) -> ProvisionFlow:
        if not state:
            raise InvalidOAuthStateError("Missing OAuth state")

        flow = self.flow_store.get_by_state(state)
        if not flow:
            raise FlowNotFoundError("No provisioning flow found for the provided state")

        client = sub2api_client or self.sub2api_client
        platform = self._platform_for_flow(client, flow)
        logger.info(
            "Completing OAuth flow | flow_id=%s | email=%s | platform=%s",
            flow.flow_id,
            flow.email,
            platform,
        )
        try:
            self._require_oauth_platform(platform)
            self._record_event(
                flow_id=flow.flow_id,
                event_type=ProvisionEventType.callback_parsed,
                status=ProvisionEventStatus.succeeded,
                message="OAuth callback parsed",
                details={"state": state, "platform": platform},
            )
            try:
                exchange = client.exchange_oauth_code(
                    code=code,
                    state=state,
                    session_id=flow.oauth_session_id,
                    platform=platform,
                )
            except Sub2APIError as exc:
                # The authorization session is consumed by the attempt, successful
                # or not (grok's also expires 30 minutes after it is minted), so a
                # second paste against the same handoff can never work. Say so:
                # the fix is always to restart the authorization.
                raise Sub2APIError(
                    f"{exc} — the authorization session is single-use and expires "
                    "(grok: 30 minutes), so this handoff is now spent. Restart the "
                    "authorization for this account and paste the new callback URL "
                    "or code.",
                    status_code=exc.status_code,
                ) from exc
            self._record_event(
                flow_id=flow.flow_id,
                event_type=ProvisionEventType.oauth_exchanged,
                status=ProvisionEventStatus.succeeded,
                message="OAuth code exchanged",
                details={
                    "provider_user_id": exchange["exchange"].get("provider_user_id"),
                    "received_token_payload": True,
                    "platform": platform,
                },
            )
            account, account_action = self._resolve_oauth_account(
                email=flow.email,
                oauth_payload=exchange["exchange"],
                group_id=flow.group_id,
                flow_id=flow.flow_id,
                platform=platform,
                sub2api_client=client,
            )
            if account_action == "created":
                account_message = "OAuth account created"
            else:
                account_message = "Existing OAuth account reused"
            self._record_event(
                flow_id=flow.flow_id,
                event_type=ProvisionEventType.account_created,
                status=ProvisionEventStatus.succeeded,
                message=account_message,
                details={
                    "account_id": account["id"],
                    "group_id": flow.group_id,
                    "action": account_action,
                    "platform": platform,
                },
            )
            self._record_event(
                flow_id=flow.flow_id,
                event_type=ProvisionEventType.account_bound,
                status=ProvisionEventStatus.succeeded,
                message="OAuth account group assignment resolved",
                details={
                    "account_id": account["id"],
                    "group_id": flow.group_id,
                    "action": account_action,
                    "platform": platform,
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
                details={"error": str(exc), "platform": platform},
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
            details={
                "oauth_account_id": account["id"],
                "group_id": flow.group_id,
                "platform": platform,
            },
        )
        logger.info(
            "OAuth flow completed | flow_id=%s | oauth_account_id=%s | platform=%s",
            flow.flow_id,
            flow.oauth_account_id,
            platform,
        )
        return flow

    def parse_oauth_callback_url(self, callback_url: str) -> tuple[str, str]:
        """Read back whatever the operator pasted: callback URL *or* bare code.

        Not every authorization page redirects to a localhost callback — grok's
        often just prints the code — so anything that does not carry a `code`
        parameter is taken to be the code itself. The returned state may be empty:
        state is never the operator's to supply, sidecar already stored the
        upstream one on the flow, so an empty state here means "locate the flow by
        its id instead" (see /provision/oauth/complete).

        The full-URL test is deliberately "did a non-empty `code` key come out of
        the parse", not "did the parse produce anything": a bare code carries `=`
        padding (`ory_ac_x==`), which parse_qs happily reads as the single pair
        `{"ory_ac_x": ["="]}`.
        """
        raw_value = callback_url.strip()
        if not raw_value:
            raise InvalidOAuthCallbackPayloadError(
                "Paste the OAuth callback URL or the authorization code; the value is empty"
            )
        parsed = urlparse(raw_value)
        candidate_query = parsed.query or parsed.fragment or raw_value.lstrip("?")
        params = parse_qs(candidate_query)

        if params.get("error"):
            error_message = params["error"][0]
            raise InvalidOAuthCallbackPayloadError(
                f"OAuth callback contains error: {error_message}"
            )

        code = self._first_param(params, "code")
        if code:
            return code, self._first_param(params, "state") or ""

        # Nothing named `code` came out. Read the paste as a bare code only when it
        # could not have been a callback in the first place: an actual URL, or a
        # query string that carried `state` but lost its `code`, is a paste
        # accident, and forwarding it upstream as a code would spend the one-shot
        # authorization session on a value that cannot work.
        if (parsed.scheme and parsed.netloc) or params.get("state"):
            raise InvalidOAuthCallbackPayloadError(
                "Unable to parse code and state from pasted callback URL. Paste the "
                "full callback URL, or paste only the authorization code."
            )
        return raw_value, ""

    def resolve_callback_flow(
        self, *, state: str, flow_id: str | None
    ) -> ProvisionFlow:
        """Find the flow the pasted callback value belongs to.

        A pasted callback URL identifies its flow by the state it carries; a bare
        authorization code carries none, so the caller must name the flow it
        started (``flow_id`` comes straight back from /provision/start). Guessing
        "the only pending flow" is deliberately not a fallback — two operators
        authorizing at once would cross their codes into each other's accounts.
        """
        if state:
            flow = self.flow_store.get_by_state(state)
            if not flow:
                raise FlowNotFoundError(
                    "No provisioning flow found for the provided state"
                )
            return flow

        requested_flow_id = (flow_id or "").strip()
        if not requested_flow_id:
            raise InvalidOAuthCallbackPayloadError(
                "The pasted value carries no OAuth state, so it is read as a bare "
                "authorization code and needs flow_id to say which flow it belongs "
                "to. Paste the full callback URL instead, or restart the "
                "authorization and paste the code it issues."
            )

        flow = self.flow_store.get_by_flow_id(requested_flow_id)
        if not flow:
            raise InvalidOAuthCallbackPayloadError(
                f"No provisioning flow found for flow_id '{requested_flow_id}'. "
                "Paste the full callback URL instead, or restart the authorization "
                "and paste the code it issues."
            )
        if not flow.state:
            raise InvalidOAuthStateError(
                f"Provisioning flow '{requested_flow_id}' has no stored OAuth state; "
                "restart the authorization for this account."
            )
        return flow

    def _oauth_redirect_uri_from_url(
        self, oauth_url: str | None, platform: str | None = None
    ) -> str:
        """What to tell the operator to paste back, parsed from the auth URL.

        The redirect_uri baked into the URL upstream minted is the only one the
        provider will actually call back, so it wins whenever it is there; the
        per-platform default is only for URLs that carry none.
        """
        fallback = self._default_redirect_uri(platform or "openai")
        if not oauth_url:
            return fallback
        parsed = urlparse(oauth_url)
        params = parse_qs(parsed.query or parsed.fragment)
        redirect_uri = self._first_param(params, "redirect_uri")
        return redirect_uri or fallback

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

    def _default_platform(self, client: Sub2APIClient) -> str:
        """The upstream's configured default provisioning platform.

        This is the single place the configured default is read; every helper
        below takes the platform as an explicit argument so a caller can drive a
        different platform without the deep layers reaching back into config.
        """
        return str(client.provisioning_defaults.group_platform or "").strip() or "openai"

    def _resolve_platform(self, client: Sub2APIClient, platform: str | None) -> str:
        """A request's explicit platform wins; config only fills the blank.

        Kept an opaque string on purpose — upstream applies no platform whitelist to
        account creation, so a platform sidecar has never heard of is a valid group
        target. Only the OAuth flow narrows this further (see _require_oauth_platform),
        because that one really does need per-platform endpoints and credentials.
        """
        explicit = str(platform).strip() if platform is not None else ""
        return explicit or self._default_platform(client)

    def _require_oauth_platform(self, platform: str) -> None:
        supported = Sub2APIClient.supported_oauth_platforms()
        if platform in supported:
            return
        raise ProvisioningError(
            f"OAuth provisioning is not supported for platform '{platform}'. "
            f"Supported platforms: {', '.join(supported)}"
        )

    def _platform_for_flow(self, client: Sub2APIClient, flow: ProvisionFlow) -> str:
        """The platform a pending flow belongs to, recovered at callback time.

        The flow record predates multi-platform provisioning and carries no platform
        column, so the target group is what remembers it: the account is about to be
        bound to that group and upstream keeps account and group platform in step, so
        the group's own platform is the authoritative answer. A group that cannot be
        read back (or carries no platform) falls back to the configured default, which
        is exactly the pre-change behavior.
        """
        try:
            for group in client.list_groups():
                if str(group.get("id")) == str(flow.group_id):
                    platform = str(group.get("platform") or "").strip()
                    if platform:
                        return platform
                    break
        except Exception:
            logger.warning(
                "Falling back to the configured platform: group lookup failed | flow_id=%s | group_id=%s",
                flow.flow_id,
                flow.group_id,
                exc_info=True,
            )
        return self._default_platform(client)

    def _default_redirect_uri(self, platform: str) -> str:
        """Redirect URI to show when the auth URL itself does not name one.

        openai keeps reading the configured value (it is deployment-specific and has
        always been configurable); every other platform uses its upstream helper's
        fixed loopback callback.
        """
        if platform == "openai":
            return self.openai_oauth_redirect_uri
        return DEFAULT_OAUTH_REDIRECT_URIS.get(platform, self.openai_oauth_redirect_uri)

    def _build_group_name(self, email: str, platform: str) -> str:
        """`{email}_{platform}`, capped at the upstream's 128-char group name limit.

        The suffix is what tells operators which platform a dedicated group serves,
        so an over-long email loses its tail rather than the suffix. Platform is
        still never parsed back out of the name; `group.platform` is the truth.
        """
        suffix = f"_{platform}"
        if len(suffix) >= 128:
            return suffix[:128]
        return f"{email[: 128 - len(suffix)]}{suffix}"

    def _resolve_group_assignment(
        self,
        email: str,
        *,
        upstream_id: str,
        platform: str,
        sub2api_client: Sub2APIClient | None = None,
    ) -> tuple[object, AssignmentMode, str]:
        client = sub2api_client or self.sub2api_client
        group_name = self._build_group_name(email, platform)
        # `{email}_{platform}` is what provisioning writes today; the bare
        # `{email}` is the pre-suffix legacy name. A legacy group is still this
        # user's dedicated slot as long as its platform matches, so reuse it
        # rather than creating a second dedicated group beside it.
        existing_group = self._find_group_by_name(
            group_name,
            email,
            platform=platform,
            sub2api_client=client,
        )
        if existing_group is not None:
            return (
                existing_group["id"],
                AssignmentMode.dedicated,
                "existing dedicated provisioning group",
            )
        if upstream_id == self.default_upstream_id:
            landing_group = self._select_landing_pool_group(platform)
            if landing_group is not None:
                return (
                    landing_group.group_id,
                    AssignmentMode.managed_pool,
                    "landing pool assignment",
                )
        group = client.create_group(group_name, platform=platform)
        return group["id"], AssignmentMode.dedicated, "dedicated provisioning group"

    def _select_landing_pool_group(self, platform: str) -> RotationPoolGroup | None:
        # A landing group only works for the platform its upstream group serves,
        # so a pool that mixes platforms still yields one candidate set per platform.
        groups = [
            group
            for group in self.flow_store.list_rotation_pool_groups(RotationPoolKind.landing)
            if not group.is_subscription
            and str(group.platform or "").strip().lower() == platform.strip().lower()
        ]
        if not groups:
            return None
        return min(
            groups,
            key=lambda group: (
                group.priority,
                group.created_at,
                str(group.group_id),
            ),
        )

    def _find_group_by_name(
        self,
        *group_names: str,
        platform: str,
        sub2api_client: Sub2APIClient | None = None,
    ) -> dict[str, object] | None:
        """First group on ``platform`` whose name matches, in candidate order.

        Several names are accepted so a caller can spell out its preferred name
        plus legacy spellings without paying for a second upstream list call.
        """
        client = sub2api_client or self.sub2api_client
        groups = client.list_groups(platform=platform)
        wanted_platform = platform.strip().lower()
        for group_name in group_names:
            needle = group_name.strip().lower()
            if not needle:
                continue
            for group in groups:
                if str(group.get("name") or "").strip().lower() != needle:
                    continue
                # The upstream list is already filtered server-side, but a same-named
                # group on another platform is a different dedicated slot, never a reuse
                # candidate — decided on the platform field, never on the name suffix.
                if str(group.get("platform") or "").strip().lower() != wanted_platform:
                    continue
                return group
        return None

    def _resolve_oauth_account(
        self,
        *,
        email: str,
        oauth_payload: dict[str, object],
        group_id: object,
        flow_id: str,
        platform: str,
        sub2api_client: Sub2APIClient | None = None,
    ) -> tuple[dict[str, object], str]:
        client = sub2api_client or self.sub2api_client
        existing = self._find_oauth_account(email, platform=platform, sub2api_client=client)
        if existing is None:
            account = client.create_account_from_oauth(
                name=email,
                oauth_payload=oauth_payload,
                group_id=group_id,
                platform=platform,
            )
            self._ensure_default_scheduled_test_plan(flow_id, account["id"], client)
            return account, "created"

        account_id = existing["id"]
        if not self._account_has_group(existing, group_id):
            client.bind_account_to_group(account_id, group_id)
            account = self._existing_account_payload(existing, email)
            self._ensure_default_scheduled_test_plan(flow_id, account["id"], client)
            return account, "bound_existing"

        account = self._existing_account_payload(existing, email)
        self._ensure_default_scheduled_test_plan(flow_id, account["id"], client)
        return account, "already_bound"

    def _resolve_apikey_account(
        self,
        *,
        name: str,
        api_base_url: str,
        api_key: str,
        group_id: object,
        flow_id: str,
        platform: str,
        sub2api_client: Sub2APIClient | None = None,
    ) -> tuple[dict[str, object], str]:
        client = sub2api_client or self.sub2api_client
        existing = self._find_apikey_account(name, platform=platform, sub2api_client=client)
        if existing is None:
            account = client.create_account_from_apikey(
                name=name,
                base_url=api_base_url,
                api_key=api_key,
                group_id=group_id,
                platform=platform,
            )
            self._ensure_default_scheduled_test_plan(flow_id, account["id"], client)
            return account, "created"

        had_group = self._account_has_group(existing, group_id)
        account = client.configure_existing_apikey_account(
            account=existing,
            name=name,
            base_url=api_base_url,
            api_key=api_key,
            group_id=group_id,
            platform=platform,
        )
        # The configure PUT sends the union of the account's current groups and the
        # target group, so the binding already landed; a follow-up bind would only
        # re-send the identical group_ids as a second equivalent PUT.
        self._ensure_default_scheduled_test_plan(flow_id, account["id"], client)
        return account, "configured_existing" if had_group else "configured_and_bound"

    def _find_apikey_account(
        self,
        name: str,
        *,
        platform: str,
        sub2api_client: Sub2APIClient | None = None,
    ) -> dict[str, object] | None:
        client = sub2api_client or self.sub2api_client
        candidate = self._find_oauth_account(name, platform=platform, sub2api_client=client)
        if candidate is None:
            return None
        # Only reuse an existing account if it is already an API key account; never
        # reconfigure (and clobber) an OAuth account that happens to share the name.
        expected_type = str(client.provisioning_defaults.account_apikey_type).strip().lower()
        account_type = str(candidate.get("account_type") or "").strip().lower()
        if account_type and account_type != expected_type:
            return None
        return candidate

    def _configure_existing_oauth_account(
        self,
        *,
        existing_account: dict[str, object],
        email: str,
        group_id: object,
        flow_id: str,
        platform: str | None = None,
        sub2api_client: Sub2APIClient | None = None,
    ) -> tuple[dict[str, object], str]:
        client = sub2api_client or self.sub2api_client
        had_group = self._account_has_group(existing_account, group_id)
        account = client.configure_existing_oauth_account(
            account=existing_account,
            name=email,
            group_id=group_id,
            platform=platform,
        )
        # See _resolve_apikey_account: configure already writes the union of the
        # existing group_ids and the target group, so no follow-up bind is needed.
        self._ensure_default_scheduled_test_plan(flow_id, account["id"], client)
        return account, "configured_existing" if had_group else "configured_and_bound"

    def _ensure_default_scheduled_test_plan(
        self,
        flow_id: str,
        account_id: object,
        sub2api_client: Sub2APIClient,
    ) -> None:
        try:
            sub2api_client.ensure_default_scheduled_test_plan(account_id)
        except Exception as exc:
            logger.warning(
                "Default scheduled test plan setup failed after account provisioning | account_id=%s",
                account_id,
                exc_info=True,
            )
            self._record_event(
                flow_id=flow_id,
                event_type=ProvisionEventType.failed,
                status=ProvisionEventStatus.failed,
                message="Default scheduled test plan setup failed after account provisioning",
                details={"account_id": account_id, "error": str(exc)},
            )

    def _find_oauth_account(
        self,
        email: str,
        *,
        platform: str,
        sub2api_client: Sub2APIClient | None = None,
    ) -> dict[str, object] | None:
        """The existing account for this name/email *on this platform*.

        Reuse means the caller will PUT the provisioning defaults over the
        account — platform, provider and type included. Doing that to a match
        from another platform would hijack it, so a known mismatch is treated as
        "not found" and the caller creates a fresh account instead. An account
        that carries no platform at all is still a match: it predates the field
        and the provisioning PUT is what gives it one.
        """
        client = sub2api_client or self.sub2api_client
        needle = email.strip().lower()
        wanted_platform = platform.strip().lower()
        for account in client.list_openai_accounts():
            account_platform = str(account.get("platform") or "").strip().lower()
            if account_platform and account_platform != wanted_platform:
                continue
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
