from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import ACCESS_KEY_COOKIE_NAME, AuthSession, EphemeralAdminAuthManager
from app.clients.sub2api import Sub2APIAuthError, Sub2APIClient, Sub2APIError
from app.config import OPERATIONAL_RUNTIME_INTERVAL_SECONDS, Settings, get_settings
from app.errors import FlowNotFoundError, ProvisioningError
from app.logging_config import setup_logging
from app.models.credit import (
    CreditAdjustmentOutcome,
    CreditAuditOperation,
    CreditAuditRecord,
    CreditBalanceOperation,
    CreditRechargePolicy,
    CreditRechargeRunRecord,
    CreditRechargeSchedule,
    CreditScheduleKind,
    CreditTargetScope,
    CreditTargetScopeKind,
    CreditUsageWindow,
)
from app.models.flow import AssignmentMode, FlowStatus
from app.models.notification import NotificationSettings
from app.models.operational_data import (
    CreditControlRuntimeSettings,
    OperationalDataRuntimeSettings,
    ProvisioningRuntimeSettings,
)
from app.models.rotation import AutoRotationUsageWindow, RotationPoolGroup, RotationPoolKind
from app.models.schemas import (
    AutoRotationConfigEnvelope,
    AutoRotationConfigRequest,
    AutoRotationConfigResponse,
    AutoRotationRunRequest,
    AutoRotationRunResponse,
    AutoRotationRunsEnvelope,
    AutoRotationSchedulerStatusResponse,
    AuthSessionResponse,
    CreditControlAdjustmentEnvelope,
    CreditControlAdjustmentItemResponse,
    CreditControlAdjustmentRequest,
    CreditControlAuditEnvelope,
    CreditControlAuditResponse,
    CreditControlApiKeyResponse,
    CreditControlPoliciesEnvelope,
    CreditControlPolicyEnvelope,
    CreditControlPolicyRequest,
    CreditControlPolicyResponse,
    CreditControlRunResponse,
    CreditControlRunsEnvelope,
    CreditControlRuntimeSettingsEnvelope,
    CreditControlRuntimeSettingsRequest,
    CreditControlRuntimeSettingsResponse,
    CreditControlSchedulerStatusResponse,
    CreditControlUserDetailEnvelope,
    CreditControlUserResponse,
    CreditControlUsersEnvelope,
    ErrorResponse,
    LoginRequest,
    LoginResponse,
    ManualRotationRequest,
    NotificationDeliveriesEnvelope,
    NotificationDeliveryOutcomeResponse,
    NotificationDeliveryRecordResponse,
    NotificationEvaluateRequest,
    NotificationEvaluateResponse,
    OperationalDataRuntimeSettingsEnvelope,
    OperationalDataRuntimeSettingsRequest,
    OperationalDataRuntimeSettingsResponse,
    OperationalDataStatusResponse,
    OperationalDataSourceStatusResponse,
    NotificationRuleStateResponse,
    NotificationTestRequest,
    NotificationTestResponse,
    OrchestrationAccountResponse,
    OrchestrationAccountsEnvelope,
    OrchestrationApiKeyAssignRequest,
    OrchestrationApiKeyResponse,
    OrchestrationApiKeysEnvelope,
    OrchestrationAssignRequest,
    OrchestrationGroupResponse,
    OrchestrationGroupsEnvelope,
    OrchestrationUserResponse,
    OrchestrationUsersEnvelope,
    ProvisionCompleteRequest,
    ProvisionFlowDetailResponse,
    ProvisionFlowsEnvelope,
    ProvisioningRuntimeSettingsEnvelope,
    ProvisioningRuntimeSettingsRequest,
    ProvisioningRuntimeSettingsResponse,
    ProvisionStartRequest,
    RotationExecutionResponse,
    RotationPoolGroupRemoveRequest,
    RotationPoolCandidateResponse,
    RotationPoolCandidatesEnvelope,
    RotationPoolGroupRequest,
    Sub2APILoginRequest,
)
from app.services.dashboard import flow_detail_response, flow_summary_response
from app.services.credit_control import CreditControlError, CreditControlService
from app.services.credit_scheduler import CreditControlScheduler
from app.services.notification import (
    NotificationConfigError,
    NotificationService,
    NotificationTestError,
    redact_settings,
)
from app.services.notification_delivery import NotificationDeliveryService
from app.services.operational_data import OperationalDataCollector
from app.services.notification_scheduler import NotificationScheduler
from app.services.provisioning import ProvisioningService
from app.services.rotation import RotationExecutionResult, RotationService
from app.services.rotation_scheduler import AutoRotationScheduler
from app.stores.postgres import PostgresFlowStore

setup_logging()
logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
UI_DIST_DIR = APP_DIR / "static" / "ui"
UI_INDEX_FILE = UI_DIST_DIR / "index.html"

@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    get_settings()
    get_flow_store()
    get_auth_manager()
    notification_service = get_notification_service()
    notification_scheduler = NotificationScheduler(
        notification_service=notification_service,
        cadence_seconds=OPERATIONAL_RUNTIME_INTERVAL_SECONDS,
        enabled_provider=lambda: get_operational_data_runtime_settings().enabled,
        cadence_provider=lambda: get_operational_data_runtime_settings().collect_interval_seconds,
    )
    app_instance.state.notification_scheduler = notification_scheduler
    try:
        notification_service.refresh_samples(now=datetime.now(timezone.utc))
    except Exception:
        logger.exception("Initial operational data refresh failed")
    notification_scheduler.start()
    rotation_service = get_rotation_service()
    rotation_scheduler = AutoRotationScheduler(
        rotation_service=rotation_service,
        cadence_seconds=OPERATIONAL_RUNTIME_INTERVAL_SECONDS,
        enabled_provider=lambda: get_rotation_service().get_auto_rotation_config().enabled,
    )
    app_instance.state.auto_rotation_scheduler = rotation_scheduler
    rotation_scheduler.start()
    credit_scheduler = CreditControlScheduler(
        credit_service=get_credit_control_service(),
        cadence_seconds=OPERATIONAL_RUNTIME_INTERVAL_SECONDS,
        enabled_provider=lambda: get_credit_control_runtime_settings().enabled,
    )
    app_instance.state.credit_control_scheduler = credit_scheduler
    credit_scheduler.start()
    try:
        yield
    finally:
        rotation_scheduler.stop()
        notification_scheduler.stop()
        credit_scheduler.stop()


app = FastAPI(
    title="Sub2API OpenAI OAuth Orchestrator",
    version="0.4.0",
    lifespan=lifespan,
)

if UI_DIST_DIR.exists():
    app.mount("/ui-static", StaticFiles(directory=str(UI_DIST_DIR)), name="ui-static")


@lru_cache(maxsize=1)
def get_flow_store() -> PostgresFlowStore:
    settings = get_settings()
    return PostgresFlowStore(database_url=settings.database_url)


@lru_cache(maxsize=1)
def get_auth_manager() -> EphemeralAdminAuthManager:
    settings = get_settings()
    return EphemeralAdminAuthManager(
        username=settings.app_auth_username,
        password=settings.app_auth_password,
        access_key_ttl_hours=settings.app_access_key_ttl_hours,
    )


@lru_cache(maxsize=1)
def get_sub2api_client() -> Sub2APIClient:
    settings = get_settings()
    return Sub2APIClient(
        base_url=settings.sub2api_base_url,
        admin_api_key=settings.sub2api_admin_api_key,
        provisioning_defaults=settings.sub2api_provisioning_defaults,
        timeout_seconds=settings.request_timeout_seconds,
    )


@lru_cache(maxsize=1)
def get_rotation_service() -> RotationService:
    settings = get_settings()
    return RotationService(
        store=get_flow_store(),
        sub2api_client=get_sub2api_client(),
        settings=settings,
    )


@lru_cache(maxsize=1)
def get_provisioning_service() -> ProvisioningService:
    settings: Settings = get_settings()
    return ProvisioningService(
        flow_store=get_flow_store(),
        sub2api_client=get_sub2api_client(),
        openai_oauth_redirect_uri=settings.openai_oauth_redirect_uri,
        assignment_mode_provider=lambda: get_provisioning_runtime_settings().assignment_mode,
    )


@lru_cache(maxsize=1)
def get_notification_service() -> NotificationService:
    store = get_flow_store()
    return NotificationService(
        store=store,
        delivery=NotificationDeliveryService(store=store),
        operational_data_collector=OperationalDataCollector(
            client=get_sub2api_client(),
            store=store,
        ),
    )


@lru_cache(maxsize=1)
def get_credit_control_service() -> CreditControlService:
    return CreditControlService(
        store=get_flow_store(),
        sub2api_client=get_sub2api_client(),
    )


def get_operational_data_runtime_settings() -> OperationalDataRuntimeSettings:
    stored = get_flow_store().get_operational_data_runtime_settings()
    if stored is not None:
        return stored
    return OperationalDataRuntimeSettings()


def save_operational_data_runtime_settings(
    payload: OperationalDataRuntimeSettingsRequest,
) -> OperationalDataRuntimeSettings:
    existing = get_flow_store().get_operational_data_runtime_settings()
    now = datetime.now(timezone.utc)
    settings = OperationalDataRuntimeSettings(
        enabled=payload.enabled,
        collect_interval_seconds=payload.collect_interval_seconds,
        expiration=payload.expiration,
        retention_seconds=payload.retention_seconds,
        max_storage_mb=payload.max_storage_mb,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    return get_flow_store().save_operational_data_runtime_settings(settings)


def operational_data_runtime_settings_response(
    settings: OperationalDataRuntimeSettings | None = None,
) -> OperationalDataRuntimeSettingsEnvelope:
    settings = settings if settings is not None else get_operational_data_runtime_settings()
    return OperationalDataRuntimeSettingsEnvelope(
        settings=OperationalDataRuntimeSettingsResponse(
            enabled=settings.enabled,
            collect_interval_seconds=settings.collect_interval_seconds,
            expiration=settings.expiration,
            retention_seconds=settings.retention_seconds,
            max_storage_mb=settings.max_storage_mb,
            updated_at=settings.updated_at,
        )
    )


def operational_data_status_response() -> OperationalDataStatusResponse:
    scheduler = getattr(app.state, "notification_scheduler", None)
    runtime_settings = get_operational_data_runtime_settings()
    if scheduler is None:
        return OperationalDataStatusResponse(
            enabled=runtime_settings.enabled,
            running=False,
            cadence_seconds=runtime_settings.collect_interval_seconds,
            collect_interval_seconds=runtime_settings.collect_interval_seconds,
            expiration=runtime_settings.expiration,
            retention_seconds=runtime_settings.retention_seconds,
            max_storage_mb=runtime_settings.max_storage_mb,
            storage_bytes=get_flow_store().operational_data_storage_bytes(),
            tick_count=0,
            source_statuses=[
                OperationalDataSourceStatusResponse(**status.model_dump())
                for status in get_flow_store().list_operational_data_source_statuses()
            ],
        )
    snapshot = scheduler.snapshot()
    return OperationalDataStatusResponse(
        enabled=snapshot.enabled,
        running=snapshot.running,
        cadence_seconds=snapshot.cadence_seconds,
        collect_interval_seconds=runtime_settings.collect_interval_seconds,
        expiration=runtime_settings.expiration,
        retention_seconds=runtime_settings.retention_seconds,
        max_storage_mb=runtime_settings.max_storage_mb,
        storage_bytes=get_flow_store().operational_data_storage_bytes(),
        tick_count=snapshot.tick_count,
        last_tick_started_at=snapshot.last_tick_started_at,
        last_tick_finished_at=snapshot.last_tick_finished_at,
        last_tick_error=snapshot.last_tick_error,
        last_sampling_started_at=snapshot.last_sampling_started_at,
        last_sampling_finished_at=snapshot.last_sampling_finished_at,
        last_sampling_error=snapshot.last_sampling_error,
        sampled_signal_count=snapshot.sampled_signal_count,
        source_statuses=[
            OperationalDataSourceStatusResponse(**status.model_dump())
            for status in snapshot.source_statuses or []
        ],
    )


def get_credit_control_runtime_settings() -> CreditControlRuntimeSettings:
    stored = get_flow_store().get_credit_control_runtime_settings()
    if stored is not None:
        return stored
    return CreditControlRuntimeSettings()


def save_credit_control_runtime_settings(
    payload: CreditControlRuntimeSettingsRequest,
) -> CreditControlRuntimeSettings:
    existing = get_flow_store().get_credit_control_runtime_settings()
    now = datetime.now(timezone.utc)
    settings = CreditControlRuntimeSettings(
        enabled=payload.enabled,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    return get_flow_store().save_credit_control_runtime_settings(settings)


def credit_control_runtime_settings_response(
    settings: CreditControlRuntimeSettings | None = None,
) -> CreditControlRuntimeSettingsEnvelope:
    settings = settings if settings is not None else get_credit_control_runtime_settings()
    return CreditControlRuntimeSettingsEnvelope(
        settings=CreditControlRuntimeSettingsResponse(
            enabled=settings.enabled,
            updated_at=settings.updated_at,
        )
    )


def get_provisioning_runtime_settings() -> ProvisioningRuntimeSettings:
    stored = get_flow_store().get_provisioning_runtime_settings()
    if stored is not None:
        return stored
    return ProvisioningRuntimeSettings()


def save_provisioning_runtime_settings(
    payload: ProvisioningRuntimeSettingsRequest,
) -> ProvisioningRuntimeSettings:
    existing = get_flow_store().get_provisioning_runtime_settings()
    now = datetime.now(timezone.utc)
    settings = ProvisioningRuntimeSettings(
        assignment_mode=AssignmentMode(payload.assignment_mode),
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    return get_flow_store().save_provisioning_runtime_settings(settings)


def provisioning_runtime_settings_response(
    settings: ProvisioningRuntimeSettings | None = None,
) -> ProvisioningRuntimeSettingsEnvelope:
    settings = settings if settings is not None else get_provisioning_runtime_settings()
    return ProvisioningRuntimeSettingsEnvelope(
        settings=ProvisioningRuntimeSettingsResponse(
            assignment_mode=settings.assignment_mode.value,
            updated_at=settings.updated_at,
        )
    )


@app.exception_handler(RequestValidationError)
def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(detail=str(exc)).model_dump(),
    )


@app.exception_handler(StarletteHTTPException)
def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(detail=detail).model_dump(),
        headers=exc.headers,
    )


@app.exception_handler(Sub2APIError)
def handle_sub2api_error(_: Request, exc: Sub2APIError) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content=ErrorResponse(detail=str(exc)).model_dump(),
    )


@app.exception_handler(ProvisioningError)
def handle_provisioning_error(_: Request, exc: ProvisioningError) -> JSONResponse:
    if isinstance(exc, FlowNotFoundError):
        status_code = 404
    elif isinstance(exc, NotificationConfigError):
        status_code = 422
    else:
        status_code = 400
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(detail=str(exc)).model_dump(),
    )


@app.exception_handler(CreditControlError)
def handle_credit_control_error(_: Request, exc: CreditControlError) -> JSONResponse:
    detail = str(exc) or "Credit-control request failed"
    status_code = 404 if "not found" in detail.lower() else 422
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(detail=detail).model_dump(),
    )


def extract_access_key(request: Request) -> str | None:
    header_key = request.headers.get("x-access-key")
    if header_key:
        return header_key.strip()

    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        bearer_value = auth_header[7:].strip()
        if bearer_value:
            return bearer_value

    cookie_key = request.cookies.get(ACCESS_KEY_COOKIE_NAME)
    if cookie_key:
        return cookie_key.strip()
    return None


def get_optional_auth_session(request: Request) -> AuthSession | None:
    auth_manager = get_auth_manager()
    return auth_manager.get_session(extract_access_key(request))


def require_api_auth(request: Request) -> AuthSession:
    session = get_optional_auth_session(request)
    if session:
        return session

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def set_auth_cookie(response: JSONResponse, access_key: str) -> None:
    response.set_cookie(
        key=ACCESS_KEY_COOKIE_NAME,
        value=access_key,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=get_auth_manager().cookie_max_age_seconds,
        expires=get_auth_manager().cookie_max_age_seconds,
        path=cookie_path(),
    )


def clear_auth_cookie(response: JSONResponse) -> None:
    response.delete_cookie(key=ACCESS_KEY_COOKIE_NAME, path=cookie_path())


def serve_react_app() -> Response:
    if UI_INDEX_FILE.exists():
        html = UI_INDEX_FILE.read_text(encoding="utf-8")
        html = html.replace('src="/ui-static/', f'src="{external_path("/ui-static/")}')
        html = html.replace('href="/ui-static/', f'href="{external_path("/ui-static/")}')
        runtime_config = (
            '<script>'
            f"window.__SUB2API_SIDECAR_BASE_PATH__ = {json.dumps(get_settings().app_base_path)};"
            "</script>"
        )
        html = html.replace("</head>", f"    {runtime_config}\n  </head>", 1)
        return HTMLResponse(html)

    return HTMLResponse(
        """
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Sub2API UI</title>
  </head>
  <body>
    <div id="root" data-ui-build="missing">
      React frontend build is missing. Run `cd frontend && npm install && npm run build`.
    </div>
  </body>
</html>
        """.strip()
    )


def external_path(path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    base_path = get_settings().app_base_path
    if not base_path:
        return normalized_path
    if normalized_path == "/":
        return f"{base_path}/"
    return f"{base_path}{normalized_path}"


def cookie_path() -> str:
    return get_settings().app_base_path or "/"


def safe_operator_next_path(value: str | None) -> str:
    logical_value = strip_external_path(value or "")
    if logical_value in {
        "/orchestration",
        "/orchestration/manual",
        "/orchestration/dynamic",
        "/dynamic",
        "/dashboard",
        "/provision",
        "/notifications",
        "/credit-control",
        "/credit-control/users",
        "/credit-control/policies",
        "/credit-control/runs",
        "/credit-control/audit",
    }:
        return logical_value
    return "/"


def strip_external_path(path: str) -> str:
    if not path:
        return "/"
    base_path = get_settings().app_base_path
    if not base_path:
        return path
    if path == base_path:
        return "/"
    if path.startswith(f"{base_path}/"):
        return path[len(base_path) :] or "/"
    return path


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    if get_optional_auth_session(request):
        next_path = safe_operator_next_path(request.query_params.get("next"))
        return RedirectResponse(url=external_path(next_path), status_code=status.HTTP_303_SEE_OTHER)

    return serve_react_app()


@app.post("/auth/login")
def auth_login(payload: LoginRequest) -> JSONResponse:
    auth_manager = get_auth_manager()
    session = auth_manager.login(payload.username, payload.password)
    if not session:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(detail="Invalid username or password").model_dump(),
            headers={"WWW-Authenticate": "Bearer"},
        )

    response = JSONResponse(
        status_code=200,
        content=LoginResponse(
            username=session.username,
            access_key=session.access_key,
            expires_at=session.expires_at,
        ).model_dump(mode="json"),
    )
    set_auth_cookie(response, session.access_key)
    return response


@app.post("/auth/sub2api-login")
def sub2api_login(payload: Sub2APILoginRequest) -> JSONResponse:
    try:
        profile = get_sub2api_client().validate_admin_jwt(payload.token)
    except Sub2APIAuthError as exc:
        status_code = (
            exc.status_code
            if exc.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}
            else status.HTTP_401_UNAUTHORIZED
        )
        return JSONResponse(
            status_code=status_code,
            content=ErrorResponse(detail=str(exc)).model_dump(),
            headers={"WWW-Authenticate": "Bearer"},
        )

    username = str(profile.get("username") or profile.get("email") or "sub2api-admin")
    session = get_auth_manager().create_external_session(username=username)
    response = JSONResponse(
        status_code=200,
        content=LoginResponse(
            username=session.username,
            access_key=session.access_key,
            expires_at=session.expires_at,
        ).model_dump(mode="json"),
    )
    set_auth_cookie(response, session.access_key)
    return response


@app.get("/auth/session")
def auth_session(session: AuthSession = Depends(require_api_auth)) -> AuthSessionResponse:
    return AuthSessionResponse(username=session.username, expires_at=session.expires_at)


@app.post("/auth/logout")
def auth_logout(request: Request) -> JSONResponse:
    get_auth_manager().revoke(extract_access_key(request))
    response = JSONResponse(status_code=200, content={"success": True})
    clear_auth_cookie(response)
    return response


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> Response:
    session = get_optional_auth_session(request)
    if not session:
        return RedirectResponse(url=external_path("/login"), status_code=status.HTTP_303_SEE_OTHER)

    return RedirectResponse(
        url=external_path("/orchestration/manual"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/orchestration", response_class=HTMLResponse)
@app.get("/orchestration/manual", response_class=HTMLResponse)
@app.get("/orchestration/dynamic", response_class=HTMLResponse)
@app.get("/dynamic", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/provision", response_class=HTMLResponse)
@app.get("/notifications", response_class=HTMLResponse)
@app.get("/credit-control", response_class=HTMLResponse)
@app.get("/credit-control/users", response_class=HTMLResponse)
@app.get("/credit-control/policies", response_class=HTMLResponse)
@app.get("/credit-control/runs", response_class=HTMLResponse)
@app.get("/credit-control/audit", response_class=HTMLResponse)
def operator_view(request: Request) -> Response:
    session = get_optional_auth_session(request)
    if not session:
        return RedirectResponse(
            url=f"{external_path('/login')}?next={external_path(request.url.path)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return serve_react_app()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ping")
def ping() -> dict[str, str]:
    return {"status": "ok"}


def rotation_execution_response(
    result: RotationExecutionResult,
    *,
    run_record: Any | None = None,
) -> RotationExecutionResponse:
    return RotationExecutionResponse(
        run_id=run_record.run_id if run_record else None,
        run_kind=run_record.run_kind.value if run_record else None,
        tag=run_record.tag if run_record else None,
        user_id=result.user_id,
        email=result.email,
        source_group_id=result.source_group_id,
        target_group_id=result.target_group_id,
        trigger_type=result.trigger_type.value,
        status=result.status.value,
        reason=result.reason,
        migrated_keys=result.migrated_keys,
        usage_window=result.usage_window.value if result.usage_window else None,
        usage_value=result.usage_value,
        usage_snapshot=result.usage_snapshot,
        metadata=result.metadata,
    )


def auto_rotation_run_response(record: Any) -> AutoRotationRunResponse:
    return AutoRotationRunResponse(
        run_id=record.run_id,
        run_kind=record.run_kind.value,
        tag=record.tag,
        status=record.status,
        window=record.window.value if record.window else "",
        dry_run=record.dry_run,
        created_at=record.created_at,
        updated_at=record.updated_at,
        synced=record.synced,
        config=record.config,
        dead_band_skipped=record.dead_band_skipped,
        planned=[RotationExecutionResponse(**item) for item in record.planned],
        moved=[RotationExecutionResponse(**item) for item in record.moved],
        skipped=[RotationExecutionResponse(**item) for item in record.skipped],
        failed=[RotationExecutionResponse(**item) for item in record.failed],
        rollback_results=[
            RotationExecutionResponse(**item) for item in record.rollback_results
        ],
        rollback_status=record.rollback_status,
        rollback_reason=record.rollback_reason,
    )


def group_response(candidate: dict[str, object]) -> OrchestrationGroupResponse:
    unsupported_reason = candidate.get("unsupported_reason")
    if unsupported_reason is None and candidate.get("rotation_supported") is False:
        if candidate.get("is_subscription"):
            unsupported_reason = "subscription groups require single-key updates"
        elif candidate.get("is_exclusive") is False:
            unsupported_reason = "group is not exclusive"
    return OrchestrationGroupResponse(
        group_id=candidate["id"],
        name=str(candidate.get("name") or ""),
        group_kind=candidate.get("group_kind"),
        platform=candidate.get("platform"),
        status=candidate.get("status"),
        is_exclusive=bool(candidate.get("is_exclusive")),
        is_subscription=bool(candidate.get("is_subscription")),
        rotation_supported=bool(candidate.get("rotation_supported")),
        unsupported_reason=str(unsupported_reason) if unsupported_reason else None,
        account_count=candidate.get("account_count"),
        active_account_count=candidate.get("active_account_count"),
        rpm_limit=candidate.get("rpm_limit"),
        rate_multiplier=candidate.get("rate_multiplier"),
        daily_limit_usd=candidate.get("daily_limit_usd"),
        weekly_limit_usd=candidate.get("weekly_limit_usd"),
        monthly_limit_usd=candidate.get("monthly_limit_usd"),
    )


def credit_usage_window(value: str | None) -> CreditUsageWindow:
    try:
        return CreditUsageWindow(value or CreditUsageWindow.window_1d.value)
    except ValueError as exc:
        raise CreditControlError("unsupported credit usage window") from exc


def credit_user_response(item: Any) -> CreditControlUserResponse:
    return CreditControlUserResponse(
        user_id=item.user_id,
        email=item.email,
        username=item.username,
        name=item.name,
        display_name=item.display_name,
        status=item.status,
        group_id=item.current_group_id,
        group_name=item.current_group_name,
        group_ids=list(item.group_ids or []),
        balance=item.balance,
        balance_display=item.balance_display,
        balance_unit=item.balance_unit,
        consumption=item.consumption,
        usage_window=item.usage_window.value,
        usage=item.usage,
    )


def credit_api_key_response(item: dict[str, Any], window: CreditUsageWindow) -> CreditControlApiKeyResponse:
    usage = (
        item.get(f"usage_{window.value}")
        or item.get("usage")
        or item.get("cost")
        or item.get("total_cost")
    )
    if isinstance(usage, str):
        try:
            usage = float(usage.strip().rstrip("%"))
        except ValueError:
            usage = None
    raw_group = item.get("group")
    group_id = item.get("group_id")
    group_name = item.get("group_name")
    if isinstance(raw_group, dict):
        group_id = group_id or raw_group.get("id") or raw_group.get("group_id")
        group_name = group_name or raw_group.get("name") or raw_group.get("group_name")
    return CreditControlApiKeyResponse(
        key_id=item.get("id") or item.get("key_id"),
        name=item.get("name"),
        usage=float(usage) if isinstance(usage, (int, float)) and not isinstance(usage, bool) else None,
        group_id=group_id,
        group_name=group_name,
        raw=redact_credit_payload(item),
    )


def credit_adjustment_response(record: CreditRechargeRunRecord) -> CreditControlAdjustmentEnvelope:
    items = [credit_adjustment_item_response(item) for item in record.outcomes]
    return CreditControlAdjustmentEnvelope(
        run_id=record.run_id,
        status=record.status.value,
        dry_run=record.dry_run,
        affected_count=record.target_count,
        total_amount=record.amount * record.target_count,
        items=items,
        details=redact_credit_payload(record.model_dump(mode="json")),
    )


def credit_adjustment_item_response(
    item: CreditAdjustmentOutcome,
) -> CreditControlAdjustmentItemResponse:
    return CreditControlAdjustmentItemResponse(
        user_id=item.user_id,
        email=item.email,
        amount=item.amount,
        operation=item.operation.value if item.operation else None,
        balance_before=item.balance_before,
        balance_after=item.balance_after,
        status=item.status.value,
        error=item.error_message,
        skipped_reason=item.skipped_reason,
    )


def credit_policy_response(policy: CreditRechargePolicy) -> CreditControlPolicyResponse:
    target_scope = "all"
    target_group_id: Any | None = None
    target_user_ids: list[Any] = []
    target_balance_below: float | None = None
    if policy.target_scope.kind == CreditTargetScopeKind.explicit_user_ids:
        target_scope = "users"
        target_user_ids = list(policy.target_scope.user_ids)
    elif policy.target_scope.kind == CreditTargetScopeKind.group_ids:
        target_scope = "group"
        target_group_id = policy.target_scope.group_ids[0] if policy.target_scope.group_ids else None
    elif policy.target_scope.kind == CreditTargetScopeKind.balance_threshold:
        target_scope = "balance_threshold"
        target_balance_below = policy.target_scope.balance_below

    return CreditControlPolicyResponse(
        policy_id=policy.policy_id,
        name=policy.name,
        enabled=policy.enabled,
        amount=policy.amount,
        schedule_type="one_time" if policy.schedule.kind == CreditScheduleKind.once else "recurring",
        schedule=policy_schedule_display(policy),
        timezone=policy.schedule.timezone,
        target_scope=target_scope,
        target_group_id=target_group_id,
        target_user_ids=target_user_ids,
        target_balance_below=target_balance_below,
        next_run_at=policy.next_run_at,
        last_run_at=policy.last_run_at,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
        raw=redact_credit_payload(policy.model_dump(mode="json")),
    )


def policy_schedule_display(policy: CreditRechargePolicy) -> str:
    start = policy.schedule.start_at.astimezone(ZoneInfo(policy.schedule.timezone))
    if policy.schedule.kind == CreditScheduleKind.once:
        return start.isoformat()
    return f"{policy.schedule.kind.value} {start.strftime('%H:%M')}"


def credit_run_response(record: CreditRechargeRunRecord) -> CreditControlRunResponse:
    first_error = next(
        (outcome.error_message for outcome in record.outcomes if outcome.error_message),
        None,
    )
    return CreditControlRunResponse(
        run_id=record.run_id,
        policy_id=record.policy_id,
        policy_name=record.policy_name,
        status=record.status.value,
        dry_run=record.dry_run,
        affected_count=record.target_count,
        total_amount=record.amount * record.target_count,
        started_at=record.started_at,
        finished_at=record.finished_at,
        scheduled_for=record.scheduled_for,
        error_message=first_error,
        details=redact_credit_payload(record.model_dump(mode="json")),
    )


def credit_audit_response(record: CreditAuditRecord) -> CreditControlAuditResponse:
    details = redact_credit_payload(record.details)
    return CreditControlAuditResponse(
        audit_id=record.audit_id,
        event_id=record.audit_id,
        user_id=record.user_id,
        policy_id=record.policy_id,
        run_id=record.run_id,
        actor=record.actor,
        action=record.operation_type.value,
        status=record.status,
        amount=optional_float(details.get("amount")),
        balance_before=optional_float(details.get("balance_before")),
        balance_after=optional_float(details.get("balance_after")),
        reason=str(details.get("reason")) if details.get("reason") not in (None, "") else None,
        summary=record.summary,
        details=details,
        created_at=record.created_at,
    )


def credit_policy_from_request(
    payload: CreditControlPolicyRequest,
    *,
    policy_id: str | None = None,
    existing: CreditRechargePolicy | None = None,
) -> CreditRechargePolicy:
    timezone_name = payload.timezone.strip() or "Asia/Shanghai"
    try:
        zone = ZoneInfo(timezone_name)
    except Exception as exc:
        raise CreditControlError(f"unknown timezone: {timezone_name}") from exc
    schedule = credit_schedule_from_request(payload, zone)
    return CreditRechargePolicy(
        policy_id=policy_id or (existing.policy_id if existing else None) or str(uuid.uuid4()),
        name=payload.name.strip(),
        enabled=payload.enabled,
        amount=payload.amount,
        target_scope=credit_target_scope_from_policy_request(payload),
        schedule=schedule,
        reason_template=(payload.reason_template or f"automatic recharge: {payload.name}").strip(),
        next_run_at=existing.next_run_at if existing else None,
        last_run_at=existing.last_run_at if existing else None,
        created_at=existing.created_at if existing else datetime.now(timezone.utc),
    )


def credit_schedule_from_request(
    payload: CreditControlPolicyRequest,
    zone: ZoneInfo,
) -> CreditRechargeSchedule:
    start_at = parse_credit_schedule_start(payload.schedule, zone)
    if payload.schedule_type == "one_time":
        kind = CreditScheduleKind.once
    elif payload.schedule_type == "recurring":
        kind = parse_recurring_schedule_kind(payload.schedule)
    else:
        raise CreditControlError("schedule_type must be one_time or recurring")
    return CreditRechargeSchedule(kind=kind, start_at=start_at, timezone=str(zone))


def parse_credit_schedule_start(value: str | None, zone: ZoneInfo) -> datetime:
    if not value or not value.strip():
        return datetime.now(zone) + timedelta(minutes=5)
    text = value.strip()
    if text.startswith("@"):
        text = text[1:].strip()
    cron_parts = text.split()
    if len(cron_parts) == 5 and cron_parts[0].isdigit() and cron_parts[1].isdigit():
        minute = int(cron_parts[0])
        hour = int(cron_parts[1])
        now = datetime.now(zone)
        candidate = datetime.combine(now.date(), time(hour=hour, minute=minute), tzinfo=zone)
        if candidate < now:
            candidate += timedelta(days=1)
        return candidate
    if " " in text and "T" not in text and ":" in text:
        parts = text.split()
        time_part = next((part for part in parts if ":" in part), None)
        if time_part:
            text = time_part
    try:
        if ":" in text and "T" not in text and "-" not in text:
            hour_text, minute_text, *_ = text.split(":")
            now = datetime.now(zone)
            candidate = datetime.combine(
                now.date(),
                time(hour=int(hour_text), minute=int(minute_text)),
                tzinfo=zone,
            )
            if candidate < now:
                candidate += timedelta(days=1)
            return candidate
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CreditControlError("schedule must be an ISO timestamp or HH:MM time") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed


def parse_recurring_schedule_kind(value: str | None) -> CreditScheduleKind:
    text = (value or "").strip().lower()
    if text.startswith("monthly"):
        return CreditScheduleKind.monthly
    if text.startswith("weekly") or text.endswith(" * * 1") or " * * 1" in text:
        return CreditScheduleKind.weekly
    return CreditScheduleKind.daily


def credit_target_scope_from_policy_request(
    payload: CreditControlPolicyRequest,
) -> CreditTargetScope:
    if payload.target_scope == "all":
        return CreditTargetScope(kind=CreditTargetScopeKind.all_users)
    if payload.target_scope == "users":
        return CreditTargetScope(
            kind=CreditTargetScopeKind.explicit_user_ids,
            user_ids=tuple(payload.target_user_ids),
        )
    if payload.target_scope == "group":
        return CreditTargetScope(
            kind=CreditTargetScopeKind.group_ids,
            group_ids=(payload.target_group_id,) if payload.target_group_id not in (None, "") else (),
        )
    if payload.target_scope == "balance_threshold":
        return CreditTargetScope(
            kind=CreditTargetScopeKind.balance_threshold,
            balance_below=payload.target_balance_below,
        )
    raise CreditControlError("target_scope must be all, users, group, or balance_threshold")


def credit_target_scope_from_adjustment(
    payload: CreditControlAdjustmentRequest,
) -> CreditTargetScope:
    if payload.target.mode == "users":
        user_ids = [str(user_id) for user_id in payload.target.user_ids]
        if len(user_ids) != len(set(user_ids)):
            raise CreditControlError("duplicate user ids are not allowed")
        return CreditTargetScope(
            kind=CreditTargetScopeKind.explicit_user_ids,
            user_ids=tuple(payload.target.user_ids),
        )
    if payload.target.mode == "filter":
        return CreditTargetScope(kind=CreditTargetScopeKind.all_users)
    raise CreditControlError("target mode must be users or filter")


def credit_adjustment_operation(amount: float) -> tuple[CreditBalanceOperation, float]:
    if amount == 0:
        raise CreditControlError("amount must not be zero")
    operation = CreditBalanceOperation.add if amount > 0 else CreditBalanceOperation.subtract
    return operation, abs(amount)


def redact_credit_payload(value: Any) -> Any:
    sensitive_keys = {
        "api_key",
        "access_key",
        "authorization",
        "bearer",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
    }
    if isinstance(value, dict):
        return {
            key: "***REDACTED***"
            if any(marker in str(key).lower() for marker in sensitive_keys)
            else redact_credit_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_credit_payload(item) for item in value]
    return value


def optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


@app.get("/orchestration/users")
def orchestration_users(
    email: str | None = None,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    upstream_users = get_sub2api_client().list_users(email=email)
    local_assignments = {
        str(assignment.user_id): assignment
        for assignment in get_flow_store().list_user_assignments()
    }
    items: list[OrchestrationUserResponse] = []
    for user in upstream_users:
        local_assignment = local_assignments.get(str(user["id"]))
        username = user.get("username")
        display_name = user.get("display_name")
        items.append(
            OrchestrationUserResponse(
                user_id=user["id"],
                email=str(user.get("email") or ""),
                name=user.get("name"),
                username=str(username) if username is not None else None,
                display_name=str(display_name) if display_name is not None else None,
                status=user.get("status"),
                current_group_id=user.get("current_group_id"),
                current_group_name=user.get("current_group_name"),
                local_group_id=local_assignment.current_group_id if local_assignment else None,
                local_group_name=local_assignment.current_group_name if local_assignment else None,
                has_local_assignment=local_assignment is not None,
            )
        )
    payload = OrchestrationUsersEnvelope(items=items, total=len(items))
    return JSONResponse(status_code=200, content=payload.model_dump(mode="json"))


@app.get("/api/credit-control/users")
def credit_control_users(
    window: str = "1d",
    search: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    group_id: str | None = None,
    balance_min: float | None = None,
    balance_max: float | None = None,
    consumption_min: float | None = None,
    consumption_max: float | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    service = get_credit_control_service()
    items, total, aggregates = service.list_users(
        usage_window=credit_usage_window(window),
        search=search,
        status=status_filter,
        group_id=group_id,
        balance_min=balance_min,
        balance_max=balance_max,
        consumption_min=consumption_min,
        consumption_max=consumption_max,
        limit=limit,
        offset=offset,
    )
    payload = CreditControlUsersEnvelope(
        items=[credit_user_response(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
        aggregates=aggregates,
    )
    return JSONResponse(status_code=200, content=payload.model_dump(mode="json"))


@app.get("/api/credit-control/users/{user_id}")
def credit_control_user_detail(
    user_id: str,
    window: str = "1d",
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    usage_window = credit_usage_window(window)
    item, audits = get_credit_control_service().get_user_detail(
        user_id,
        usage_window=usage_window,
    )
    api_keys = [
        credit_api_key_response(api_key, usage_window)
        for api_key in get_credit_control_service().get_user_api_keys(user_id)
    ]
    response_item = credit_user_response(item)
    payload = CreditControlUserDetailEnvelope(
        item=response_item,
        api_keys=api_keys,
        audit_items=[credit_audit_response(record) for record in audits],
    )
    item_payload = payload.model_dump(mode="json")
    item_payload["item"]["api_keys"] = item_payload.pop("api_keys")
    return JSONResponse(status_code=200, content=item_payload)


@app.post("/api/credit-control/adjustments/preview")
def credit_control_adjustment_preview(
    payload: CreditControlAdjustmentRequest,
    session: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    response = run_credit_adjustment(payload, session=session, preview=True)
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


@app.post("/api/credit-control/adjustments")
def credit_control_adjustment_execute(
    payload: CreditControlAdjustmentRequest,
    session: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    response = run_credit_adjustment(payload, session=session, preview=False)
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


def run_credit_adjustment(
    payload: CreditControlAdjustmentRequest,
    *,
    session: AuthSession,
    preview: bool,
) -> CreditControlAdjustmentEnvelope:
    service = get_credit_control_service()
    operation, amount = credit_adjustment_operation(payload.amount)
    target_scope = credit_target_scope_from_adjustment(payload)
    if payload.target.mode == "filter":
        users = service.preview_filter_target(
            usage_window=credit_usage_window(payload.target.window),
            search=payload.target.search,
            status=payload.target.status,
            group_id=payload.target.group_id,
            balance_min=payload.target.balance_min,
            balance_max=payload.target.balance_max,
            consumption_min=payload.target.consumption_min,
            consumption_max=payload.target.consumption_max,
        )
        record = (
            service.preview_adjustment_for_users(
                users=users,
                target_scope=target_scope,
                amount=amount,
                operation=operation,
                reason=payload.reason,
                actor=session.username,
            )
            if preview
            else service.execute_adjustment_for_users(
                users=users,
                target_scope=target_scope,
                amount=amount,
                operation=operation,
                reason=payload.reason,
                actor=session.username,
            )
        )
    else:
        record = (
            service.preview_adjustment(
                target_scope=target_scope,
                amount=amount,
                operation=operation,
                reason=payload.reason,
                actor=session.username,
            )
            if preview
            else service.execute_adjustment(
                target_scope=target_scope,
                amount=amount,
                operation=operation,
                reason=payload.reason,
                actor=session.username,
            )
        )
    return credit_adjustment_response(record)


@app.get("/api/credit-control/policies")
def credit_control_policies(_: AuthSession = Depends(require_api_auth)) -> JSONResponse:
    items = get_credit_control_service().list_policies()
    payload = CreditControlPoliciesEnvelope(
        items=[credit_policy_response(policy) for policy in items],
        total=len(items),
    )
    return JSONResponse(status_code=200, content=payload.model_dump(mode="json"))


@app.post("/api/credit-control/policies")
def credit_control_policy_create(
    payload: CreditControlPolicyRequest,
    session: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    policy = credit_policy_from_request(payload)
    saved = get_credit_control_service().save_policy(policy, actor=session.username)
    response = CreditControlPolicyEnvelope(item=credit_policy_response(saved))
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


@app.put("/api/credit-control/policies/{policy_id}")
def credit_control_policy_update(
    policy_id: str,
    payload: CreditControlPolicyRequest,
    session: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    service = get_credit_control_service()
    existing = service.get_policy(policy_id)
    policy = credit_policy_from_request(payload, policy_id=policy_id, existing=existing)
    saved = service.save_policy(policy, actor=session.username, previous=existing)
    response = CreditControlPolicyEnvelope(item=credit_policy_response(saved))
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


@app.delete("/api/credit-control/policies/{policy_id}")
def credit_control_policy_delete(
    policy_id: str,
    session: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    get_credit_control_service().delete_policy(policy_id, actor=session.username)
    return JSONResponse(status_code=200, content={"success": True})


@app.post("/api/credit-control/policies/preview")
def credit_control_policy_preview(
    payload: CreditControlPolicyRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    policy = credit_policy_from_request(payload)
    record = get_credit_control_service().preview_policy(policy)
    response = credit_adjustment_response(record)
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


@app.post("/api/credit-control/policies/{policy_id}/run")
def credit_control_policy_run(
    policy_id: str,
    session: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    record = get_credit_control_service().run_policy_now(policy_id, actor=session.username)
    response = credit_adjustment_response(record)
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


@app.get("/api/credit-control/runs")
def credit_control_runs(
    policy_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    records = get_credit_control_service().list_runs(policy_id=policy_id, limit=limit)
    payload = CreditControlRunsEnvelope(
        items=[credit_run_response(record) for record in records],
        total=len(records),
    )
    return JSONResponse(status_code=200, content=payload.model_dump(mode="json"))


@app.get("/api/credit-control/settings")
def credit_control_settings_get(
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content=credit_control_runtime_settings_response().model_dump(mode="json"),
    )


@app.put("/api/credit-control/settings")
def credit_control_settings_put(
    payload: CreditControlRuntimeSettingsRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    settings = save_credit_control_runtime_settings(payload)
    return JSONResponse(
        status_code=200,
        content=credit_control_runtime_settings_response(settings).model_dump(mode="json"),
    )


@app.get("/api/credit-control/scheduler")
def credit_control_scheduler_status(
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    scheduler = getattr(app.state, "credit_control_scheduler", None)
    if scheduler is None:
        runtime_settings = get_credit_control_runtime_settings()
        response = CreditControlSchedulerStatusResponse(
            enabled=runtime_settings.enabled,
            running=False,
            cadence_seconds=OPERATIONAL_RUNTIME_INTERVAL_SECONDS,
            tick_count=0,
        )
    else:
        response = CreditControlSchedulerStatusResponse(**scheduler.snapshot().__dict__)
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


@app.get("/api/credit-control/audit")
def credit_control_audit(
    user_id: str | None = None,
    policy_id: str | None = None,
    run_id: str | None = None,
    operation_type: str | None = None,
    audit_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    parsed_operation = None
    if operation_type:
        try:
            parsed_operation = CreditAuditOperation(operation_type)
        except ValueError as exc:
            raise CreditControlError("unsupported audit operation_type") from exc
    records = get_credit_control_service().list_audit_records(
        user_id=user_id,
        policy_id=policy_id,
        run_id=run_id,
        operation_type=parsed_operation,
        status=audit_status,
        limit=limit,
    )
    payload = CreditControlAuditEnvelope(
        items=[credit_audit_response(record) for record in records],
        total=len(records),
    )
    return JSONResponse(status_code=200, content=payload.model_dump(mode="json"))


@app.get("/orchestration/groups")
def orchestration_groups(_: AuthSession = Depends(require_api_auth)) -> JSONResponse:
    groups = get_sub2api_client().list_groups(
        platform=get_settings().sub2api_provisioning_defaults.group_platform
    )
    items = [group_response(group) for group in groups]
    payload = OrchestrationGroupsEnvelope(items=items, total=len(items))
    return JSONResponse(status_code=200, content=payload.model_dump(mode="json"))


@app.get("/orchestration/accounts")
def orchestration_accounts(_: AuthSession = Depends(require_api_auth)) -> JSONResponse:
    client = get_sub2api_client()
    accounts = client.list_openai_accounts()
    items = [
        OrchestrationAccountResponse(
            account_id=account["id"],
            name=str(account.get("name") or ""),
            email=account.get("email"),
            provider=account.get("provider"),
            platform=account.get("platform"),
            account_type=account.get("account_type"),
            status=account.get("status"),
            availability_status=account.get("availability_status") or "unknown",
            availability_reason=account.get("availability_reason"),
            is_available=account.get("is_available"),
            temporary_unschedulable=bool(account.get("temporary_unschedulable")),
            rate_limited=bool(account.get("rate_limited")),
            quota_remaining=account.get("quota_remaining"),
            last_error=account.get("last_error"),
            availability_updated_at=account.get("availability_updated_at"),
            concurrency=account.get("concurrency"),
            current_concurrency=account.get("current_concurrency"),
            usage_5h_percent=account.get("usage_5h_percent"),
            usage_7d_percent=account.get("usage_7d_percent"),
            usage_updated_at=account.get("usage_updated_at"),
            group_ids=list(account.get("group_ids") or []),
            group_names=[str(name) for name in account.get("group_names") or []],
        )
        for account in accounts
    ]
    payload = OrchestrationAccountsEnvelope(items=items, total=len(items))
    return JSONResponse(status_code=200, content=payload.model_dump(mode="json"))


@app.get("/orchestration/users/{user_id}/api-keys")
def orchestration_user_api_keys(
    user_id: str,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    response = get_sub2api_client().get_user_api_keys(user_id)
    items = []
    for item in response["items"]:
        items.append(
            OrchestrationApiKeyResponse(
                key_id=item.get("id") or item.get("key_id"),
                name=item.get("name"),
                group_id=item.get("group_id") or item.get("current_group_id"),
                group_name=item.get("group_name") or item.get("current_group_name"),
                status=item.get("status"),
                usage_5h=item.get("usage_5h"),
                usage_1d=item.get("usage_1d"),
                usage_7d=item.get("usage_7d"),
            )
        )
    payload = OrchestrationApiKeysEnvelope(items=items, total=response["total"])
    return JSONResponse(status_code=200, content=payload.model_dump(mode="json"))


@app.post("/orchestration/assignments/replace-group")
def orchestration_replace_group(
    payload: OrchestrationAssignRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    service = get_rotation_service()
    result = service.orchestrate_existing_assignment(
        user_id=payload.user_id,
        email=payload.email,
        source_group_id=payload.source_group_id,
        target_group_id=payload.target_group_id,
        reason=payload.reason,
    )
    run_record = service.save_manual_run_record(tag="manual_user_group", result=result)
    response = rotation_execution_response(result, run_record=run_record)
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


@app.post("/orchestration/api-keys/update-group")
def orchestration_update_api_key_group(
    payload: OrchestrationApiKeyAssignRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    service = get_rotation_service()
    result = service.orchestrate_existing_api_key(
        user_id=payload.user_id,
        email=payload.email,
        key_id=payload.key_id,
        source_group_id=payload.source_group_id,
        target_group_id=payload.target_group_id,
        reason=payload.reason,
    )
    run_record = service.save_manual_run_record(tag="manual_api_key", result=result)
    response = rotation_execution_response(result, run_record=run_record)
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


@app.get("/provision/flows")
def provision_flows(
    status_filter: FlowStatus | None = Query(default=None, alias="status"),
    assignment_mode: AssignmentMode | None = None,
    email: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    store = get_flow_store()
    flows = store.list_flows(
        status=status_filter,
        assignment_mode=assignment_mode,
        email=email,
        limit=limit,
        offset=offset,
    )
    total = store.count_flows(
        status=status_filter,
        assignment_mode=assignment_mode,
        email=email,
    )
    payload = ProvisionFlowsEnvelope(
        items=[flow_summary_response(flow) for flow in flows],
        total=total,
        limit=limit,
        offset=offset,
    )
    return JSONResponse(status_code=200, content=payload.model_dump(mode="json"))


@app.get("/provision/flows/{flow_id}")
def provision_flow_detail(
    flow_id: str,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    store = get_flow_store()
    flow = store.get_by_flow_id(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="Provisioning flow not found")

    payload: ProvisionFlowDetailResponse = flow_detail_response(
        flow,
        oauth_redirect_uri=get_settings().openai_oauth_redirect_uri,
        events=store.list_provision_events(flow_id),
    )
    return JSONResponse(status_code=200, content=payload.model_dump(mode="json"))


@app.get("/api/provisioning/settings")
def provisioning_settings_get(
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content=provisioning_runtime_settings_response().model_dump(mode="json"),
    )


@app.put("/api/provisioning/settings")
def provisioning_settings_put(
    payload: ProvisioningRuntimeSettingsRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    settings = save_provisioning_runtime_settings(payload)
    return JSONResponse(
        status_code=200,
        content=provisioning_runtime_settings_response(settings).model_dump(mode="json"),
    )


@app.post("/provision/start")
def provision_start(
    payload: ProvisionStartRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    service = get_provisioning_service()
    result = service.start_flow(str(payload.email))
    return JSONResponse(status_code=200, content=result.model_dump())


@app.post("/provision/oauth/complete")
def provision_oauth_complete(
    payload: ProvisionCompleteRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    service = get_provisioning_service()
    result = service.complete_oauth_from_callback_url(payload.callback_url)
    return JSONResponse(status_code=200, content=result.model_dump())


@app.get("/rotation/pool/candidates")
def rotation_pool_candidates(_: AuthSession = Depends(require_api_auth)) -> JSONResponse:
    service = get_rotation_service()
    items = [
        RotationPoolCandidateResponse(**candidate).model_dump()
        for candidate in service.list_pool_candidates()
    ]
    payload = RotationPoolCandidatesEnvelope(items=items)
    return JSONResponse(status_code=200, content=payload.model_dump())


def parse_rotation_pool_kind(value: str | None) -> RotationPoolKind:
    try:
        return RotationPoolKind(value or RotationPoolKind.rotation.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="pool_kind must be either landing or rotation",
        ) from exc


def selected_pool_response(group: RotationPoolGroup) -> RotationPoolCandidateResponse:
    is_rotation = group.pool_kind == RotationPoolKind.rotation
    is_landing = group.pool_kind == RotationPoolKind.landing
    return RotationPoolCandidateResponse(
        group_id=group.group_id,
        name=group.group_name,
        group_kind=group.group_kind,
        platform=group.platform,
        status=group.status,
        is_exclusive=group.is_exclusive,
        is_subscription=group.is_subscription,
        rotation_supported=group.rotation_supported,
        unsupported_reason=None
        if group.rotation_supported
        else "selected group is not supported for automatic rotation",
        selected=True,
        rotation_selected=is_rotation,
        landing_selected=is_landing,
        priority=group.priority if is_rotation else None,
        landing_priority=group.priority if is_landing else None,
    )


def auto_rotation_config_response() -> AutoRotationConfigEnvelope:
    service = get_rotation_service()
    config = service.get_auto_rotation_config()
    store = get_flow_store()
    landing_pool = [
        selected_pool_response(group)
        for group in store.list_rotation_pool_groups(RotationPoolKind.landing)
    ]
    rotation_pool = [
        selected_pool_response(group)
        for group in store.list_rotation_pool_groups(RotationPoolKind.rotation)
    ]
    return AutoRotationConfigEnvelope(
        config=AutoRotationConfigResponse(
            enabled=config.enabled,
            auto_assign_new_users=config.auto_assign_new_users,
            cooldown_minutes=config.cooldown_minutes,
            usage_window=config.usage_window.value,
            usage_thresholds=list(config.usage_thresholds),
            imbalance_epsilon=config.imbalance_epsilon,
            improvement_delta=config.improvement_delta,
            schedule_source_group_ids=list(config.schedule_source_group_ids),
        ),
        landing_pool=landing_pool,
        rotation_pool=rotation_pool,
    )


@app.get("/rotation/auto/config")
def rotation_auto_config(_: AuthSession = Depends(require_api_auth)) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content=auto_rotation_config_response().model_dump(mode="json"),
    )


@app.put("/rotation/auto/config")
def rotation_auto_config_update(
    payload: AutoRotationConfigRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    try:
        usage_window = AutoRotationUsageWindow(payload.usage_window)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="usage_window must be one of: 5h, 1d, 7d, 30d",
        ) from exc

    get_rotation_service().update_auto_rotation_config(
        enabled=payload.enabled,
        auto_assign_new_users=payload.auto_assign_new_users,
        cooldown_minutes=payload.cooldown_minutes,
        usage_window=usage_window,
        usage_thresholds=tuple(payload.usage_thresholds),
        imbalance_epsilon=payload.imbalance_epsilon,
        improvement_delta=payload.improvement_delta,
        schedule_source_group_ids=tuple(payload.schedule_source_group_ids),
    )
    return JSONResponse(
        status_code=200,
        content=auto_rotation_config_response().model_dump(mode="json"),
    )


@app.post("/rotation/pool/groups")
def rotation_pool_add_group(
    payload: RotationPoolGroupRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    service = get_rotation_service()
    pool_kind = parse_rotation_pool_kind(payload.pool_kind)
    group = service.add_group_to_pool(
        group_id=payload.group_id,
        priority=payload.priority,
        pool_kind=pool_kind,
    )
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "group_id": group.group_id,
            "pool_kind": group.pool_kind.value,
            "name": group.group_name,
            "group_kind": group.group_kind,
            "priority": group.priority,
            "is_exclusive": group.is_exclusive,
            "is_subscription": group.is_subscription,
            "rotation_supported": group.rotation_supported,
        },
    )


@app.delete("/rotation/pool/groups/{group_id}")
def rotation_pool_remove_group(
    group_id: str,
    pool_kind: str | None = Query(default=None),
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    parsed_pool_kind = parse_rotation_pool_kind(pool_kind)
    remove_rotation_pool_group(group_id, parsed_pool_kind)
    return JSONResponse(
        status_code=200,
        content={"success": True, "group_id": group_id, "pool_kind": parsed_pool_kind.value},
    )


@app.post("/rotation/pool/groups/remove")
def rotation_pool_remove_group_post(
    payload: RotationPoolGroupRemoveRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    parsed_pool_kind = parse_rotation_pool_kind(payload.pool_kind)
    remove_rotation_pool_group(payload.group_id, parsed_pool_kind)
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "group_id": str(payload.group_id),
            "pool_kind": parsed_pool_kind.value,
        },
    )


def remove_rotation_pool_group(group_id: Any, pool_kind: RotationPoolKind) -> None:
    get_rotation_service().remove_group_from_pool(group_id, pool_kind)


@app.post("/rotation/manual")
def rotation_manual(
    payload: ManualRotationRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    service = get_rotation_service()
    result = service.manual_rotate(
        user_id=payload.user_id,
        target_group_id=payload.target_group_id,
        reason=payload.reason,
    )
    run_record = service.save_manual_run_record(tag="manual_user_group", result=result)
    response = rotation_execution_response(result, run_record=run_record)
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


@app.post("/rotation/auto/run")
def rotation_auto_run(
    payload: AutoRotationRunRequest | None = None,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    record = get_rotation_service().run_auto_rotation(dry_run=payload.dry_run if payload else False)
    return JSONResponse(
        status_code=200,
        content=auto_rotation_run_response(record).model_dump(mode="json"),
    )


@app.get("/rotation/auto/runs")
def rotation_auto_runs(
    limit: int = Query(default=20, ge=1, le=100),
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    records = get_rotation_service().list_orchestration_runs(limit=limit)
    payload = AutoRotationRunsEnvelope(
        items=[auto_rotation_run_response(record) for record in records],
        total=len(records),
    )
    return JSONResponse(status_code=200, content=payload.model_dump(mode="json"))


@app.get("/rotation/auto/scheduler")
def rotation_auto_scheduler_status(
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    scheduler = getattr(app.state, "auto_rotation_scheduler", None)
    if scheduler is None:
        runtime_config = get_rotation_service().get_auto_rotation_config()
        response = AutoRotationSchedulerStatusResponse(
            enabled=runtime_config.enabled,
            running=False,
            cadence_seconds=OPERATIONAL_RUNTIME_INTERVAL_SECONDS,
            tick_count=0,
        )
    else:
        response = AutoRotationSchedulerStatusResponse(**scheduler.snapshot().__dict__)
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


@app.get("/rotation/auto/runs/{run_id}")
def rotation_auto_run_detail(
    run_id: str,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    record = get_rotation_service().get_orchestration_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run record not found")
    return JSONResponse(
        status_code=200,
        content=auto_rotation_run_response(record).model_dump(mode="json"),
    )


@app.post("/rotation/auto/runs/{run_id}/rollback")
def rotation_auto_run_rollback(
    run_id: str,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    record = get_rotation_service().rollback_orchestration_run(run_id)
    return JSONResponse(
        status_code=200,
        content=auto_rotation_run_response(record).model_dump(mode="json"),
    )


@app.get("/notifications/config")
def notifications_config_get(_: AuthSession = Depends(require_api_auth)) -> JSONResponse:
    settings = get_notification_service().load_config()
    return JSONResponse(status_code=200, content=redact_settings(settings))


@app.get("/api/operational-data/settings")
def operational_data_settings_get(
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content=operational_data_runtime_settings_response().model_dump(mode="json"),
    )


@app.put("/api/operational-data/settings")
def operational_data_settings_put(
    payload: OperationalDataRuntimeSettingsRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    settings = save_operational_data_runtime_settings(payload)
    return JSONResponse(
        status_code=200,
        content=operational_data_runtime_settings_response(settings).model_dump(mode="json"),
    )


@app.get("/api/operational-data/status")
def operational_data_status(
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content=operational_data_status_response().model_dump(mode="json"),
    )


@app.put("/notifications/config")
async def notifications_config_put(
    request: Request,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    raw = await request.json()
    try:
        payload = NotificationSettings.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    settings = get_notification_service().save_config(payload)
    return JSONResponse(status_code=200, content=redact_settings(settings))


@app.post("/notifications/test")
def notifications_test(
    payload: NotificationTestRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    rule, outcomes = get_notification_service().run_test(payload.rule_id)
    response = NotificationTestResponse(
        rule_id=rule.id,
        rule_name=rule.name or rule.signal_key,
        outcomes=[
            NotificationDeliveryOutcomeResponse(
                receiver_id=outcome.receiver_id,
                provider=outcome.provider.value,
                status=outcome.status.value,
                attempt_count=outcome.attempt_count,
                response_status=outcome.response_status,
                error_message=outcome.error_message,
            )
            for outcome in outcomes
        ],
    )
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


@app.post("/notifications/evaluate")
def notifications_evaluate(
    payload: NotificationEvaluateRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    outcome = get_notification_service().evaluate_once(payload.rule_id)
    response = NotificationEvaluateResponse(
        rule_id=outcome.rule.id,
        rule_name=outcome.rule.name or outcome.rule.signal_key,
        action=outcome.decision.action.value,
        reason=outcome.decision.reason,
        state=NotificationRuleStateResponse(
            rule_id=outcome.decision.next_state.rule_id,
            last_evaluated_at=outcome.decision.next_state.last_evaluated_at,
            last_value=outcome.decision.next_state.last_value,
            breach_started_at=outcome.decision.next_state.breach_started_at,
            last_alert_at=outcome.decision.next_state.last_alert_at,
            is_firing=outcome.decision.next_state.is_firing,
            last_error=outcome.decision.next_state.last_error,
        ),
        deliveries=[
            NotificationDeliveryOutcomeResponse(
                receiver_id=delivery.receiver_id,
                provider=delivery.provider.value,
                status=delivery.status.value,
                attempt_count=delivery.attempt_count,
                response_status=delivery.response_status,
                error_message=delivery.error_message,
            )
            for delivery in outcome.deliveries
        ],
    )
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


@app.get("/notifications/deliveries")
def notifications_deliveries(
    limit: int = Query(default=50, ge=1, le=500),
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    records = get_flow_store().list_notification_deliveries(limit=limit)
    items = [
        NotificationDeliveryRecordResponse(
            delivery_id=record.delivery_id,
            receiver_id=record.receiver_id,
            rule_id=record.rule_id,
            provider=record.provider.value,
            severity=record.severity.value,
            trigger=record.trigger.value,
            status=record.status.value,
            attempt_index=record.attempt_index,
            response_status=record.response_status,
            error_message=record.error_message,
            payload_digest=record.payload_digest,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        for record in records
    ]
    payload = NotificationDeliveriesEnvelope(items=items, total=len(items))
    return JSONResponse(status_code=200, content=payload.model_dump(mode="json"))
