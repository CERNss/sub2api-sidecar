## Context

The repository already includes a `Dockerfile` and a `docker-compose.yaml`, which cover container packaging and local runtime. The missing piece is a small operator-facing build helper that standardizes cross-platform image builds, especially building a Linux x64 image from macOS.

Because this project ships as a Python web service, "cross-platform build" here means producing a container image for a target platform rather than compiling a native binary. Docker Buildx already provides the right abstraction, so the design should wrap it instead of inventing a custom packaging flow.

## Goals / Non-Goals

**Goals:**

- Add a simple `build.sh` entrypoint for Docker Buildx builds.
- Default to `linux/amd64`.
- Support common overrides without forcing the operator to edit the script.
- Keep the script usable both for local `--load` flows and registry `--push` flows.
- Document the usage clearly.

**Non-Goals:**

- Building native Python binaries.
- Replacing `docker compose` as the local runtime entrypoint.
- Adding CI pipeline files in this change.

## Decisions

### 1. Use Docker Buildx directly

The script will shell out to `docker buildx build` instead of wrapping `docker build`.

Why:

- Buildx is the right tool for cross-platform images.
- It supports `linux/amd64` targets from macOS hosts.
- It keeps the implementation small and familiar to operators.

### 2. Default to `linux/amd64` and `--load`

The default script behavior will target `linux/amd64` and use `--load`.

Why:

- The user explicitly asked for Linux x64 builds.
- `--load` is the most convenient local default for verification after building.
- `--push` remains available when publishing is needed.

### 3. Support overrides through flags and env vars

The script will accept flags for image name, tag, platform, builder, Dockerfile path, context path, and output mode, while also honoring matching environment variables.

Why:

- It stays friendly for one-off local usage.
- It avoids hardcoding registry-specific values.
- It keeps future CI integration simple.

## Risks / Trade-offs

- [Buildx builder creation may surprise first-time users] → The script will auto-create a named builder when missing and print what it is doing.
- [Cross-platform builds may be slower on macOS] → Acceptable trade-off for a standard Linux x64 artifact.
- [The script depends on Docker Buildx availability] → The script fails early with a clear error message if Docker or Buildx is missing.

## Migration Plan

1. Add `build.sh`.
2. Document how to use it for local and registry builds.
3. Keep existing `Dockerfile` and `docker-compose.yaml` behavior unchanged.

Rollback is trivial: remove `build.sh` and the related docs without affecting the runtime service behavior.
