from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import ACCESS_KEY_COOKIE_NAME, AuthSession, EphemeralAdminAuthManager
from app.clients.sub2api import Sub2APIClient, Sub2APIError
from app.config import Settings, get_settings
from app.errors import FlowNotFoundError, ProvisioningError
from app.logging_config import setup_logging
from app.models.flow import AssignmentMode, FlowStatus
from app.models.rotation import AutoRotationUsageWindow, RotationPoolGroup, RotationPoolKind
from app.models.schemas import (
    AutoRotationConfigEnvelope,
    AutoRotationConfigRequest,
    AutoRotationConfigResponse,
    AutoRotationRunRequest,
    AutoRotationRunResponse,
    AutoRotationRunsEnvelope,
    ErrorResponse,
    LoginRequest,
    LoginResponse,
    ManualRotationRequest,
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
    ProvisionStartRequest,
    RotationExecutionResponse,
    RotationPoolCandidateResponse,
    RotationPoolCandidatesEnvelope,
    RotationPoolGroupRequest,
)
from app.services.dashboard import flow_detail_response, flow_summary_response
from app.services.provisioning import ProvisioningService
from app.services.rotation import RotationExecutionResult, RotationService
from app.services.rotation_scheduler import AutoRotationScheduler
from app.stores.sqlite import SQLiteFlowStore

setup_logging()
logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
UI_DIST_DIR = APP_DIR / "static" / "ui"
UI_INDEX_FILE = UI_DIST_DIR / "index.html"
APP_TITLE = "Sub2API OpenAI OAuth 编排服务"


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings()
    get_flow_store()
    get_auth_manager()
    settings = get_settings()
    scheduler: AutoRotationScheduler | None = None
    if settings.auto_rotation.enabled and settings.auto_rotation.interval_seconds > 0:
        scheduler = AutoRotationScheduler(
            rotation_service=get_rotation_service(),
            interval_seconds=settings.auto_rotation.interval_seconds,
        )
        scheduler.start()
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.stop()


app = FastAPI(
    title="Sub2API OpenAI OAuth Orchestrator",
    version="0.4.0",
    lifespan=lifespan,
)

if UI_DIST_DIR.exists():
    app.mount("/ui-static", StaticFiles(directory=str(UI_DIST_DIR)), name="ui-static")


@lru_cache(maxsize=1)
def get_flow_store() -> SQLiteFlowStore:
    settings = get_settings()
    return SQLiteFlowStore(database_path=settings.sqlite_db_path)


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
        group_name_prefix=settings.group_name_prefix,
        openai_oauth_redirect_uri=settings.openai_oauth_redirect_uri,
        assignment_mode=settings.assignment_mode,
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
    status_code = 404 if isinstance(exc, FlowNotFoundError) else 400
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(detail=str(exc)).model_dump(),
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
        path="/",
    )


def clear_auth_cookie(response: JSONResponse) -> None:
    response.delete_cookie(key=ACCESS_KEY_COOKIE_NAME, path="/")


def serve_react_app() -> Response:
    if UI_INDEX_FILE.exists():
        return FileResponse(UI_INDEX_FILE)

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


def safe_operator_next_path(value: str | None) -> str:
    if value in {
        "/orchestration",
        "/orchestration/manual",
        "/orchestration/dynamic",
        "/dynamic",
        "/dashboard",
        "/provision",
        "/notifications",
    }:
        return value
    return "/"


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    if get_optional_auth_session(request):
        next_path = safe_operator_next_path(request.query_params.get("next"))
        return RedirectResponse(url=next_path, status_code=status.HTTP_303_SEE_OTHER)

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
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    return RedirectResponse(url="/orchestration/manual", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/orchestration", response_class=HTMLResponse)
@app.get("/orchestration/manual", response_class=HTMLResponse)
@app.get("/orchestration/dynamic", response_class=HTMLResponse)
@app.get("/dynamic", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/provision", response_class=HTMLResponse)
@app.get("/notifications", response_class=HTMLResponse)
def operator_view(request: Request) -> Response:
    session = get_optional_auth_session(request)
    if not session:
        return RedirectResponse(
            url=f"/login?next={request.url.path}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return serve_react_app()


@app.get("/ui/config")
def ui_config(request: Request) -> dict[str, str | None]:
    settings = get_settings()
    session = get_optional_auth_session(request)
    return {
        "app_title": APP_TITLE,
        "auth_username": settings.app_auth_username,
        "oauth_redirect_uri": settings.openai_oauth_redirect_uri,
        "current_user": session.username if session else None,
    }


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
    )


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


@app.get("/orchestration/groups")
def orchestration_groups(_: AuthSession = Depends(require_api_auth)) -> JSONResponse:
    groups = get_sub2api_client().list_groups(
        platform=get_settings().sub2api_provisioning_defaults.group_platform
    )
    items = [group_response(group) for group in groups]
    payload = OrchestrationGroupsEnvelope(items=items, total=len(items))
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
    get_rotation_service().remove_group_from_pool(group_id, parsed_pool_kind)
    return JSONResponse(
        status_code=200,
        content={"success": True, "group_id": group_id, "pool_kind": parsed_pool_kind.value},
    )


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
