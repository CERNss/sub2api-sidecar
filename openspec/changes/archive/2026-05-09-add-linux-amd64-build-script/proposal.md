## Why

The project can already run in Docker, but building a Linux x64 image from a non-Linux host still requires remembering a long `docker buildx` command. We need a repeatable script so local builds and release packaging stay consistent.

## What Changes

- Add a `build.sh` helper that builds the service image with Docker Buildx.
- Default the build target to `linux/amd64` so macOS hosts can produce Linux x64 images directly.
- Support overriding image name, tag, platform, builder name, and push/load behavior.
- Document the script alongside the existing `Dockerfile` and `docker-compose.yaml`.

## Capabilities

### New Capabilities

- `deployment-tooling`: Container build and local runtime tooling for packaging and running the sidecar.

### Modified Capabilities

- None.

## Impact

- Affected code: `build.sh`, `README.md`, `.dockerignore`, `Dockerfile`, `docker-compose.yaml`
- Tooling: Docker Buildx workflow for local and release builds
- Operator workflow: simpler Linux x64 image builds from macOS
