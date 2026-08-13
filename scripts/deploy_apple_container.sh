#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Copy a saved Urus Apple container image to a Mac mini and run API/UI + scheduler.

Usage:
  REMOTE=macmini scripts/deploy_apple_container.sh artifacts/container-images/urus-<tag>-linux-arm64.env

Environment:
  REMOTE                  SSH target; alternative SSH_HOST/SSH_USER
  SSH_PORT                Default: 22
  ENV_FILE                Private Urus env file copied to the Mac mini
  DATABASE_FILE           Optional existing SQLite file to seed/replace remote data
  REMOTE_STORAGE_ROOT     Optional absolute root; defaults to ~/.urus paths
  REMOTE_DIR              Deploy files directory
  REMOTE_DATA_DIR         Persistent SQLite/scheduler directory
  REMOTE_ENV_FILE         Default: REMOTE_DIR/urus.env
  REMOTE_CONTAINER_CLI    Default: container
  CONTAINER_NAME          Default: urus
  SCHEDULER_NAME          Default: urus-scheduler
  CONTAINER_NETWORK       Default: urus-internal
  HOST_PORT               Default: 5173
  APP_PORT                Default: 8000
EOF
}

fail() { echo "error: $*" >&2; exit 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"; }
shell_quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\''/g")"; }

source_path="${1:-${URUS_IMAGE_METADATA:-}}"
[[ -n "$source_path" && -f "$source_path" ]] || { usage; exit 2; }
if [[ "$source_path" == *.env ]]; then
    # shellcheck disable=SC1090
    source "$source_path"
else
    URUS_IMAGE_ARCHIVE="$source_path"
fi
archive_path="${URUS_IMAGE_ARCHIVE:-}"
image_ref="${IMAGE_REF:-${URUS_IMAGE_REF:-}}"
[[ -f "$archive_path" ]] || fail "image archive does not exist: $archive_path"
[[ -n "$image_ref" ]] || fail "IMAGE_REF is required"

if [[ -n "${REMOTE:-}" ]]; then
    ssh_target="$REMOTE"
elif [[ -n "${SSH_HOST:-}" ]]; then
    ssh_target="${SSH_USER:+$SSH_USER@}$SSH_HOST"
else
    fail "set REMOTE or SSH_HOST"
fi

SSH_PORT="${SSH_PORT:-22}"
REMOTE_STORAGE_ROOT="${REMOTE_STORAGE_ROOT:-}"
if [[ -n "$REMOTE_STORAGE_ROOT" ]]; then
    [[ "$REMOTE_STORAGE_ROOT" == /* ]] || fail "REMOTE_STORAGE_ROOT must be absolute"
    default_remote_dir="$REMOTE_STORAGE_ROOT/urus-deploy"
    default_data_dir="$REMOTE_STORAGE_ROOT/urus-data"
else
    default_remote_dir=".urus/urus-deploy"
    default_data_dir=".urus/urus-data"
fi
REMOTE_DIR="${REMOTE_DIR:-$default_remote_dir}"
REMOTE_DATA_DIR="${REMOTE_DATA_DIR:-$default_data_dir}"
REMOTE_ENV_FILE="${REMOTE_ENV_FILE:-$REMOTE_DIR/urus.env}"
REMOTE_CONTAINER_CLI="${REMOTE_CONTAINER_CLI:-container}"
CONTAINER_NAME="${CONTAINER_NAME:-urus}"
SCHEDULER_NAME="${SCHEDULER_NAME:-urus-scheduler}"
CONTAINER_NETWORK="${CONTAINER_NETWORK:-urus-internal}"
HOST_PORT="${HOST_PORT:-5173}"
APP_PORT="${APP_PORT:-8000}"
remote_archive="$REMOTE_DIR/$(basename "$archive_path")"
remote_database_upload=""

require_command ssh
require_command scp
ssh_args=(-p "$SSH_PORT")
scp_args=(-P "$SSH_PORT")
ssh "${ssh_args[@]}" "$ssh_target" "mkdir -p $(shell_quote "$REMOTE_DIR") $(shell_quote "$REMOTE_DATA_DIR")"
scp "${scp_args[@]}" "$archive_path" "$ssh_target:$remote_archive"
if [[ -n "${ENV_FILE:-}" ]]; then
    [[ -f "$ENV_FILE" ]] || fail "ENV_FILE does not exist: $ENV_FILE"
    scp "${scp_args[@]}" "$ENV_FILE" "$ssh_target:$REMOTE_ENV_FILE"
fi
if [[ -n "${DATABASE_FILE:-}" ]]; then
    [[ -f "$DATABASE_FILE" ]] || fail "DATABASE_FILE does not exist: $DATABASE_FILE"
    remote_database_upload="$REMOTE_DIR/urus.db.upload"
    scp "${scp_args[@]}" "$DATABASE_FILE" "$ssh_target:$remote_database_upload"
fi

ssh "${ssh_args[@]}" "$ssh_target" "bash -s" -- \
    "$REMOTE_CONTAINER_CLI" "$remote_archive" "$image_ref" "$CONTAINER_NAME" \
    "$SCHEDULER_NAME" "$CONTAINER_NETWORK" "$HOST_PORT" "$APP_PORT" \
    "$REMOTE_ENV_FILE" "$REMOTE_DATA_DIR" "$remote_database_upload" <<'REMOTE_SCRIPT'
set -Eeuo pipefail
container_cli="$1"; archive="$2"; image_ref="$3"; app_name="$4"; scheduler_name="$5"
network="$6"; host_port="$7"; app_port="$8"; env_file="$9"; data_dir="${10}"; database_upload="${11}"

"$container_cli" system start >/dev/null 2>&1 || true
if ! "$container_cli" network list | awk 'NR > 1 {print $1}' | grep -Fxq "$network"; then
    "$container_cli" network create "$network" >/dev/null
fi
"$container_cli" image load --input "$archive" \
    || "$container_cli" image load -i "$archive" \
    || "$container_cli" image load "$archive"

for name in "$scheduler_name" "$app_name"; do
    "$container_cli" stop "$name" >/dev/null 2>&1 || true
    "$container_cli" delete "$name" >/dev/null 2>&1 \
        || "$container_cli" rm "$name" >/dev/null 2>&1 \
        || true
done

mkdir -p "$data_dir"
if [[ ! -f "$env_file" ]]; then
    echo "remote Urus env file is missing: $env_file" >&2
    exit 1
fi
if [[ -n "$database_upload" && -f "$database_upload" ]]; then
    if [[ -f "$data_dir/urus.db" ]]; then
        cp "$data_dir/urus.db" "$data_dir/urus.db.backup.$(date +%Y%m%d%H%M%S)"
    fi
    mv "$database_upload" "$data_dir/urus.db"
fi

common=(--network "$network")
if [[ -f "$env_file" ]]; then common+=(--env-file "$env_file"); fi
# Deployment invariants must win over values copied from a development .env.
common+=(
    --env "APP_ENV=production"
    --env "APP_HOST=0.0.0.0"
    --env "APP_PORT=$app_port"
    --env "DATABASE_URL=sqlite:////data/urus.db"
    --env "MOOMOO_SDK_HOME=/data/moomoo_home"
    --env "URUS_SCHEDULER_DATA_DIR=/data/scheduled_collection"
    --volume "$data_dir:/data"
)

"$container_cli" run --detach --name "$app_name" "${common[@]}" \
    --publish "${host_port}:${app_port}" "$image_ref"

healthy=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS "http://127.0.0.1:${host_port}/api/health" >/dev/null; then healthy=1; break; fi
    sleep 2
done
if [[ "$healthy" != "1" ]]; then
    echo "Urus health check failed; scheduler was not started" >&2
    "$container_cli" logs "$app_name" 2>/dev/null || true
    exit 1
fi

app_ip=$("$container_cli" inspect "$app_name" | python3 -c '
import json, sys
record = json.load(sys.stdin)[0]
print(record["status"]["networks"][0]["ipv4Address"].split("/", 1)[0])
')
if [[ -z "$app_ip" ]]; then
    echo "cannot resolve Apple Container IPv4 address for $app_name" >&2
    exit 1
fi

"$container_cli" run --detach --name "$scheduler_name" "${common[@]}" "$image_ref" \
    uv run python scripts/schedule_market_data_collection.py \
    --api-base-url "http://${app_ip}:${app_port}/api" --backend-managed-externally

echo "health check passed: http://127.0.0.1:${host_port}/api/health"
"$container_cli" list 2>/dev/null || true
REMOTE_SCRIPT

echo "Deployed Urus at http://${SSH_HOST:-${ssh_target#*@}}:${HOST_PORT}"
