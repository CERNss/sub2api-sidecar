declare global {
  interface Window {
    __SUB2API_SIDECAR_BASE_PATH__?: string;
  }
}

export function appBasePath(): string {
  return normalizeBasePath(
    window.__SUB2API_SIDECAR_BASE_PATH__ ?? (import.meta.env.DEV ? import.meta.env.BASE_URL : "")
  );
}

export function apiUrl(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const basePath = normalizeBasePath(window.__SUB2API_SIDECAR_BASE_PATH__ ?? "");
  if (!basePath) {
    return normalizedPath;
  }
  if (normalizedPath === "/") {
    return `${basePath}/`;
  }
  return `${basePath}${normalizedPath}`;
}

function normalizeBasePath(rawValue: string): string {
  const trimmed = rawValue.trim().replace(/\/+$/, "");
  if (trimmed === "" || trimmed === "/") {
    return "";
  }
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}
