const SIDECAR_ROUTE_PREFIX = "/admin/sidecar";

export function normalizeFrontendRouteBase(base: string): string {
  const trimmed = base.replace(/\/+$/, "");
  return trimmed === "" ? "" : trimmed;
}

function detectFrontendRouteBase(): string {
  if (window.location.pathname === SIDECAR_ROUTE_PREFIX) {
    return SIDECAR_ROUTE_PREFIX;
  }
  if (window.location.pathname.startsWith(`${SIDECAR_ROUTE_PREFIX}/`)) {
    return SIDECAR_ROUTE_PREFIX;
  }
  if (import.meta.env.DEV) {
    return normalizeFrontendRouteBase(import.meta.env.BASE_URL);
  }
  return "";
}

export const frontendRouteBase = detectFrontendRouteBase();

export function stripFrontendRouteBase(pathname: string): string {
  if (!frontendRouteBase) {
    return pathname || "/";
  }
  if (pathname === frontendRouteBase) {
    return "/";
  }
  if (pathname.startsWith(`${frontendRouteBase}/`)) {
    return pathname.slice(frontendRouteBase.length) || "/";
  }
  return pathname || "/";
}

export function frontendRoutePath(pathname: string): string {
  const logicalPath = pathname.startsWith("/") ? pathname : `/${pathname}`;
  if (!frontendRouteBase) {
    return logicalPath;
  }
  if (logicalPath === "/") {
    return `${frontendRouteBase}/`;
  }
  return `${frontendRouteBase}${logicalPath}`;
}

export function currentLogicalPathname(): string {
  return stripFrontendRouteBase(window.location.pathname);
}

export function apiPath(pathname: string): string {
  const logicalPath = pathname.startsWith("/") ? pathname : `/${pathname}`;
  if (frontendRouteBase === SIDECAR_ROUTE_PREFIX) {
    return `${frontendRouteBase}${logicalPath}`;
  }
  return logicalPath;
}
