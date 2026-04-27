#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="${IMAGE_NAME:-sub2api-sidecar-ci-env}"
IMAGE_TAG="${IMAGE_TAG:-py312}"
PLATFORM="${PLATFORM:-linux/amd64}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
BUILDER_NAME="${BUILDER_NAME:-sub2api-sidecar-ci-builder}"
BUILD_CONTEXT="${BUILD_CONTEXT:-$SCRIPT_DIR}"
DOCKERFILE_PATH="${DOCKERFILE_PATH:-$SCRIPT_DIR/Dockerfile.ci}"
APT_PACKAGES="${APT_PACKAGES:-bash build-essential ca-certificates curl git jq make openssh-client pkg-config tar gzip unzip}"
PIP_EXTRA_PACKAGES="${PIP_EXTRA_PACKAGES:-pip-audit virtualenv}"
INSTALL_PROJECT_DEPS="${INSTALL_PROJECT_DEPS:-true}"
OUTPUT_MODE="${OUTPUT_MODE:-load}"

usage() {
  cat <<'EOF'
Usage: ./build-ci-env.sh [options]

Build a reusable CI environment image for validation and build stages.

Options:
  --name <image>             Override image name (default: sub2api-sidecar-ci-env)
  --tag <tag>                Override image tag (default: py312)
  --platform <value>         Override target platform (default: linux/amd64)
  --python-version <value>   Override Python version (default: 3.12)
  --builder <name>           Override buildx builder name
  --file <path>              Override Dockerfile path (default: ./Dockerfile.ci)
  --context <path>           Override build context path
  --apt-packages <value>     Override apt package list
  --pip-extra <value>        Override extra pip package list
  --skip-project-deps        Do not bake requirements-dev.txt into the image
  --push                     Push image to registry instead of loading locally
  --load                     Load image into the local Docker engine (default)
  -h, --help                 Show this help message

Environment variables:
  IMAGE_NAME
  IMAGE_TAG
  PLATFORM
  PYTHON_VERSION
  BUILDER_NAME
  BUILD_CONTEXT
  DOCKERFILE_PATH
  APT_PACKAGES
  PIP_EXTRA_PACKAGES
  INSTALL_PROJECT_DEPS
  OUTPUT_MODE

Examples:
  ./build-ci-env.sh
  ./build-ci-env.sh --name ghcr.io/acme/sub2api-ci --tag py312-v1 --push
  ./build-ci-env.sh --skip-project-deps --pip-extra "pip-audit virtualenv build"
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
    --python-version)
      PYTHON_VERSION="$2"
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
    --apt-packages)
      APT_PACKAGES="$2"
      shift 2
      ;;
    --pip-extra)
      PIP_EXTRA_PACKAGES="$2"
      shift 2
      ;;
    --skip-project-deps)
      INSTALL_PROJECT_DEPS="false"
      shift
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

if [[ ! -d "$BUILD_CONTEXT" ]]; then
  echo "Build context directory not found: $BUILD_CONTEXT" >&2
  exit 1
fi

if [[ ! -f "$DOCKERFILE_PATH" ]]; then
  echo "Dockerfile not found: $DOCKERFILE_PATH" >&2
  exit 1
fi

if [[ "$OUTPUT_MODE" != "load" && "$OUTPUT_MODE" != "push" ]]; then
  echo "OUTPUT_MODE must be either 'load' or 'push'." >&2
  exit 1
fi

if [[ ! -f "$BUILD_CONTEXT/requirements.txt" || ! -f "$BUILD_CONTEXT/requirements-dev.txt" ]]; then
  echo "Build context must contain requirements.txt and requirements-dev.txt." >&2
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
  --build-arg "PYTHON_VERSION=${PYTHON_VERSION}"
  --build-arg "APT_PACKAGES=${APT_PACKAGES}"
  --build-arg "PIP_EXTRA_PACKAGES=${PIP_EXTRA_PACKAGES}"
  --build-arg "INSTALL_PROJECT_DEPS=${INSTALL_PROJECT_DEPS}"
)

if [[ "$OUTPUT_MODE" == "push" ]]; then
  BUILD_CMD+=(--push)
else
  BUILD_CMD+=(--load)
fi

BUILD_CMD+=("$BUILD_CONTEXT")

echo "Building CI image ${IMAGE_NAME}:${IMAGE_TAG}"
echo "Platform: ${PLATFORM}"
echo "Python version: ${PYTHON_VERSION}"
echo "Install project dependencies: ${INSTALL_PROJECT_DEPS}"
echo "Output mode: ${OUTPUT_MODE}"
echo "Builder: ${BUILDER_NAME}"
echo "Build context: ${BUILD_CONTEXT}"
echo "Dockerfile: ${DOCKERFILE_PATH}"

"${BUILD_CMD[@]}"

echo "Build finished: ${IMAGE_NAME}:${IMAGE_TAG}"
