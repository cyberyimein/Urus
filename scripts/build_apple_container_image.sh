#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Build Urus with Apple container and save it as an OCI archive.

Environment:
  CONTAINER_CLI          Apple container CLI. Default: container
  IMAGE_NAME             Default: urus
  IMAGE_TAG              Default: git short SHA or timestamp
  PLATFORM               Default: linux/arm64
  OUTPUT_DIR             Default: artifacts/container-images
  DOCKERFILE             Default: docker/urus/Dockerfile
  START_CONTAINER_SYSTEM Default: 1
EOF
}

fail() { echo "error: $*" >&2; exit 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"; }
sanitize() { printf '%s' "$1" | tr '/:@' '---' | tr -cs 'A-Za-z0-9._-' '-' | sed 's/^-//; s/-$//'; }
write_env_value() {
    local key="$1" value="$2"
    printf '%s="' "$key"
    printf '%s' "$value" | sed 's/\\/\\\\/g; s/"/\\"/g'
    printf '"\n'
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then usage; exit 0; fi

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/.." && pwd)
CONTAINER_CLI="${CONTAINER_CLI:-container}"
IMAGE_NAME="${IMAGE_NAME:-urus}"
PLATFORM="${PLATFORM:-linux/arm64}"
OUTPUT_DIR="${OUTPUT_DIR:-$repo_root/artifacts/container-images}"
DOCKERFILE="${DOCKERFILE:-$repo_root/docker/urus/Dockerfile}"
START_CONTAINER_SYSTEM="${START_CONTAINER_SYSTEM:-1}"

if [[ -z "${IMAGE_TAG:-}" ]]; then
    IMAGE_TAG=$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)
fi
IMAGE_REF="${IMAGE_REF:-${IMAGE_NAME}:${IMAGE_TAG}}"
archive_name="$(sanitize "$IMAGE_NAME")-$(sanitize "$IMAGE_TAG")-$(sanitize "$PLATFORM").tar"
ARCHIVE_PATH="${ARCHIVE_PATH:-$OUTPUT_DIR/$archive_name}"
METADATA_PATH="${METADATA_PATH:-${ARCHIVE_PATH%.tar}.env}"

require_command "$CONTAINER_CLI"
mkdir -p "$OUTPUT_DIR"
if [[ "$START_CONTAINER_SYSTEM" == "1" ]]; then "$CONTAINER_CLI" system start >/dev/null 2>&1 || true; fi

echo "Building $IMAGE_REF"
"$CONTAINER_CLI" build --tag "$IMAGE_REF" --file "$DOCKERFILE" --platform "$PLATFORM" "$repo_root"
echo "Saving $IMAGE_REF to $ARCHIVE_PATH"
"$CONTAINER_CLI" image save --output "$ARCHIVE_PATH" --platform "$PLATFORM" "$IMAGE_REF"

{
    write_env_value URUS_IMAGE_REF "$IMAGE_REF"
    write_env_value URUS_IMAGE_ARCHIVE "$ARCHIVE_PATH"
    write_env_value URUS_IMAGE_PLATFORM "$PLATFORM"
    write_env_value URUS_IMAGE_DOCKERFILE "$DOCKERFILE"
    write_env_value URUS_IMAGE_CREATED_AT "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$METADATA_PATH"
echo "$METADATA_PATH"
