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
    ErrorResponse,
    LoginRequest,
    LoginResponse,
    ProvisionCompleteRequest,
    ProvisionStartRequest,
)
from app.services.provisioning import ProvisioningService
from app.stores.sqlite import SQLiteFlowStore

setup_logging()
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings()
    get_flow_store()
    get_auth_manager()
    yield


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
def get_provisioning_service() -> ProvisioningService:
    settings: Settings = get_settings()
    return ProvisioningService(
        flow_store=get_flow_store(),
        sub2api_client=get_sub2api_client(),
        default_user_password=settings.default_user_password,
        group_name_prefix=settings.group_name_prefix,
        openai_oauth_redirect_uri=settings.openai_oauth_redirect_uri,
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
