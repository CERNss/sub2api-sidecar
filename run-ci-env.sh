#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="${IMAGE_NAME:-sub2api-sidecar-ci-env}"
IMAGE_TAG="${IMAGE_TAG:-py312}"
WORKDIR_IN_CONTAINER="${WORKDIR_IN_CONTAINER:-/work}"
PROJECT_DIR="${PROJECT_DIR:-$SCRIPT_DIR}"
CACHE_DIR="${CACHE_DIR:-$PROJECT_DIR/.ci-cache/pip}"
CONTAINER_USER="${CONTAINER_USER:-$(id -u):$(id -g)}"

usage() {
  cat <<'EOF'
Usage: ./run-ci-env.sh [command...]

Run commands inside the local CI environment image with the current project mounted in.

Environment variables:
  IMAGE_NAME
  IMAGE_TAG
  WORKDIR_IN_CONTAINER
  PROJECT_DIR
  CACHE_DIR
  CONTAINER_USER

Examples:
  ./run-ci-env.sh
  ./run-ci-env.sh /opt/ci-venv/bin/pytest
  ./run-ci-env.sh /opt/ci-venv/bin/python -m compileall app tests
  ./run-ci-env.sh bash -lc "/opt/ci-venv/bin/pytest && ./build.sh --load"
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required but was not found in PATH." >&2
  exit 1
fi

mkdir -p "${CACHE_DIR}"

if [[ $# -eq 0 ]]; then
  set -- bash
fi

docker run --rm -it \
  -u "${CONTAINER_USER}" \
  -e PIP_CACHE_DIR=/tmp/pip-cache \
  -v "${PROJECT_DIR}:${WORKDIR_IN_CONTAINER}" \
  -v "${CACHE_DIR}:/tmp/pip-cache" \
  -w "${WORKDIR_IN_CONTAINER}" \
  "${IMAGE_NAME}:${IMAGE_TAG}" \
  "$@"
