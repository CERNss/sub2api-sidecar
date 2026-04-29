from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import ACCESS_KEY_COOKIE_NAME, AuthSession, EphemeralAdminAuthManager
from app.clients.sub2api import Sub2APIClient, Sub2APIError
from app.config import Settings, get_settings
from app.errors import FlowNotFoundError, ProvisioningError
from app.logging_config import setup_logging
from app.models.schemas import (
    AutoRotationRunResponse,
    ErrorResponse,
    LoginRequest,
    LoginResponse,
    ManualRotationRequest,
    ProvisionCompleteRequest,
    ProvisionStartRequest,
    RotationExecutionResponse,
    RotationPoolCandidateResponse,
    RotationPoolCandidatesEnvelope,
    RotationPoolGroupRequest,
)
from app.services.provisioning import ProvisioningService
from app.services.rotation import RotationService
from app.services.rotation_scheduler import AutoRotationScheduler
from app.stores.sqlite import SQLiteFlowStore

setup_logging()
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="app/templates")


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
        default_user_password=settings.default_user_password,
        group_name_prefix=settings.group_name_prefix,
        openai_oauth_redirect_uri=settings.openai_oauth_redirect_uri,
        assignment_mode=settings.assignment_mode,
        rotation_store=get_flow_store(),
        rotation_service=get_rotation_service(),
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


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    if get_optional_auth_session(request):
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    settings = get_settings()
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "app_title": "Sub2API OpenAI OAuth 编排服务",
            "auth_username": settings.app_auth_username,
        },
    )


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
def index(request: Request) -> HTMLResponse:
    session = get_optional_auth_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    settings = get_settings()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_title": "Sub2API OpenAI OAuth 编排服务",
            "current_user": session.username,
            "oauth_redirect_uri": settings.openai_oauth_redirect_uri,
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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


@app.post("/rotation/pool/groups")
def rotation_pool_add_group(
    payload: RotationPoolGroupRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    service = get_rotation_service()
    group = service.add_group_to_pool(group_id=payload.group_id, priority=payload.priority)
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "group_id": group.group_id,
            "name": group.group_name,
            "group_kind": group.group_kind,
            "priority": group.priority,
            "is_exclusive": group.is_exclusive,
            "is_subscription": group.is_subscription,
            "rotation_supported": group.is_exclusive and not group.is_subscription,
        },
    )


@app.delete("/rotation/pool/groups/{group_id}")
def rotation_pool_remove_group(
    group_id: str,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    get_rotation_service().remove_group_from_pool(group_id)
    return JSONResponse(status_code=200, content={"success": True, "group_id": group_id})


@app.post("/rotation/manual")
def rotation_manual(
    payload: ManualRotationRequest,
    _: AuthSession = Depends(require_api_auth),
) -> JSONResponse:
    result = get_rotation_service().manual_rotate(
        user_id=payload.user_id,
        target_group_id=payload.target_group_id,
        reason=payload.reason,
    )
    response = RotationExecutionResponse(
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
    )
    return JSONResponse(status_code=200, content=response.model_dump())


@app.post("/rotation/auto/run")
def rotation_auto_run(_: AuthSession = Depends(require_api_auth)) -> JSONResponse:
    result = get_rotation_service().run_auto_rotation()
    response = AutoRotationRunResponse(
        window=result["window"],
        moved=[RotationExecutionResponse(**item) for item in result["moved"]],
        skipped=[RotationExecutionResponse(**item) for item in result["skipped"]],
        failed=[RotationExecutionResponse(**item) for item in result["failed"]],
    )
    return JSONResponse(status_code=200, content=response.model_dump())
