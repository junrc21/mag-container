#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   BASE_IMAGE=ghcr.io/... REGISTRY=ghcr.io/<user> IMAGE=hermes-mag TAG=2026-05-29 ./scripts/build_and_push.sh
#
# Notes:
# - Requires: docker login to your REGISTRY
# - Does NOT embed secrets; configure env vars in EasyPanel.

: "${BASE_IMAGE:?Set BASE_IMAGE to the exact image you currently use in EasyPanel}"
: "${REGISTRY:?Set REGISTRY (e.g. ghcr.io/juniorcarvalho)}"
: "${IMAGE:=hermes-mag}"
: "${TAG:=clone}"

FULL_IMAGE="${REGISTRY}/${IMAGE}:${TAG}"

docker build --build-arg "BASE_IMAGE=${BASE_IMAGE}" -t "${FULL_IMAGE}" .
docker push "${FULL_IMAGE}"

echo "Pushed: ${FULL_IMAGE}"
