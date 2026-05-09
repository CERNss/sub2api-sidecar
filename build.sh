#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="${IMAGE_NAME:-sub2api-sidecar}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
PLATFORM="${PLATFORM:-}"
BUILDER_NAME="${BUILDER_NAME:-sub2api-sidecar-builder}"
DOCKERFILE_PATH="${DOCKERFILE_PATH:-$SCRIPT_DIR/Dockerfile}"
BUILD_CONTEXT="${BUILD_CONTEXT:-$SCRIPT_DIR}"
OUTPUT_MODE="${OUTPUT_MODE:-load}"

usage() {
  cat <<'EOF'
Usage: ./build.sh [options]

Build the container image with Docker Buildx.
Defaults to linux/amd64 for --load and linux/amd64,linux/arm64 for --push.

Options:
  --name <image>        Override image name (default: sub2api-sidecar)
  --tag <tag>           Override image tag (default: latest)
  --platform <value>    Override target platform
  --builder <name>      Override buildx builder name
  --file <path>         Override Dockerfile path
  --context <path>      Override build context path
  --push                Push image to registry instead of loading locally
  --load                Load image into the local Docker engine (default)
  -h, --help            Show this help message

Environment variables:
  IMAGE_NAME
  IMAGE_TAG
  PLATFORM
  BUILDER_NAME
  DOCKERFILE_PATH
  BUILD_CONTEXT
  OUTPUT_MODE

Examples:
  ./build.sh
  ./build.sh --name myrepo/sub2api-sidecar --tag v1.0.0
  ./build.sh --push --name registry.example.com/sub2api-sidecar --tag v1.0.0
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      IMAGE_NAME="$2"
      shift 2
      ;;
    --tag)
      IMAGE_TAG="$2"
      shift 2
      ;;
    --platform)
      PLATFORM="$2"
      shift 2
      ;;
    --builder)
      BUILDER_NAME="$2"
      shift 2
      ;;
    --file)
      DOCKERFILE_PATH="$2"
      shift 2
      ;;
    --context)
      BUILD_CONTEXT="$2"
      shift 2
      ;;
    --push)
      OUTPUT_MODE="push"
      shift
      ;;
    --load)
      OUTPUT_MODE="load"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required but was not found in PATH." >&2
  exit 1
fi

if ! docker buildx version >/dev/null 2>&1; then
  echo "docker buildx is required but is not available." >&2
  exit 1
fi

if [[ ! -f "$DOCKERFILE_PATH" ]]; then
  echo "Dockerfile not found: $DOCKERFILE_PATH" >&2
  exit 1
fi

if [[ ! -d "$BUILD_CONTEXT" ]]; then
  echo "Build context directory not found: $BUILD_CONTEXT" >&2
  exit 1
fi

if [[ "$OUTPUT_MODE" != "load" && "$OUTPUT_MODE" != "push" ]]; then
  echo "OUTPUT_MODE must be either 'load' or 'push'." >&2
  exit 1
fi

if [[ -z "$PLATFORM" ]]; then
  if [[ "$OUTPUT_MODE" == "push" ]]; then
    PLATFORM="linux/amd64,linux/arm64"
  else
    PLATFORM="linux/amd64"
  fi
fi

if [[ "$OUTPUT_MODE" == "load" && "$PLATFORM" == *,* ]]; then
  echo "Docker --load supports one platform at a time. Use --push for multi-platform builds." >&2
  exit 1
fi

if ! docker buildx inspect "$BUILDER_NAME" >/dev/null 2>&1; then
  echo "Creating docker buildx builder: $BUILDER_NAME"
  docker buildx create --name "$BUILDER_NAME" --driver docker-container --use >/dev/null
else
  docker buildx use "$BUILDER_NAME" >/dev/null
fi

docker buildx inspect --bootstrap >/dev/null

BUILD_CMD=(
  docker buildx build
  --platform "$PLATFORM"
  --file "$DOCKERFILE_PATH"
  --tag "${IMAGE_NAME}:${IMAGE_TAG}"
)

if [[ "$OUTPUT_MODE" == "push" ]]; then
  BUILD_CMD+=(--push)
else
  BUILD_CMD+=(--load)
fi

BUILD_CMD+=("$BUILD_CONTEXT")

echo "Building image ${IMAGE_NAME}:${IMAGE_TAG}"
echo "Platform: ${PLATFORM}"
echo "Output mode: ${OUTPUT_MODE}"
echo "Builder: ${BUILDER_NAME}"

"${BUILD_CMD[@]}"

echo "Build finished: ${IMAGE_NAME}:${IMAGE_TAG}"
