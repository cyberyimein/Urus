#!/usr/bin/env bash
set -Eeuo pipefail

metadata_path="${1:-}"
if [[ "$metadata_path" == "-h" || "$metadata_path" == "--help" || -z "$metadata_path" ]]; then
    cat <<'EOF'
Deploy the standalone urus-moomoo planner to a Mac mini.

Usage:
  REMOTE=macmini scripts/deploy_apple_moomoo.sh <image-metadata.env>

The private task TOML is generated on the Mac mini from its existing Urus
deployment environment. It is never copied into Git or into the image.
EOF
    [[ -n "$metadata_path" ]] || exit 2
    exit 0
fi

fail() { echo "error: $*" >&2; exit 1; }
quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\''/g")"; }

[[ -f "$metadata_path" ]] || fail "image metadata does not exist: $metadata_path"
# shellcheck disable=SC1090
source "$metadata_path"
[[ -f "${URUS_IMAGE_ARCHIVE:-}" ]] || fail "image archive does not exist: ${URUS_IMAGE_ARCHIVE:-}"
[[ -n "${URUS_IMAGE_REF:-}" ]] || fail "URUS_IMAGE_REF is missing"

REMOTE="${REMOTE:-macmini}"
REMOTE_STORAGE_ROOT="${REMOTE_STORAGE_ROOT:-/Users/mithridates/data/urus}"
REMOTE_ENV_FILE="${REMOTE_ENV_FILE:-$REMOTE_STORAGE_ROOT/deploy/urus.env}"
REMOTE_CONTAINER_CLI="${REMOTE_CONTAINER_CLI:-/opt/homebrew/bin/container}"
CONTAINER_NAME="${CONTAINER_NAME:-urus-moomoo}"
CONTAINER_NETWORK="${CONTAINER_NETWORK:-urus-internal}"
CONTAINER_CPUS="${CONTAINER_CPUS:-4}"
CONTAINER_MEMORY="${CONTAINER_MEMORY:-2G}"

remote_archive="$REMOTE_STORAGE_ROOT/deploy/$(basename "$URUS_IMAGE_ARCHIVE")"
remote_config="$REMOTE_STORAGE_ROOT/deploy/collection_tasks.local.toml"

ssh "$REMOTE" "mkdir -p $(quote "$REMOTE_STORAGE_ROOT/deploy")"
scp "$URUS_IMAGE_ARCHIVE" "$REMOTE:$remote_archive"

ssh "$REMOTE" "bash -s" -- \
    "$REMOTE_CONTAINER_CLI" "$remote_archive" "$URUS_IMAGE_REF" \
    "$REMOTE_ENV_FILE" "$remote_config" "$REMOTE_STORAGE_ROOT" \
    "$CONTAINER_NAME" "$CONTAINER_NETWORK" \
    "$CONTAINER_CPUS" "$CONTAINER_MEMORY" <<'REMOTE_SCRIPT'
set -Eeuo pipefail
container_cli="$1"; archive="$2"; image_ref="$3"; env_file="$4"; config="$5"; data_root="$6"
container_name="$7"; network="$8"; cpus="${9}"; memory="${10}"

[[ -f "$env_file" ]] || { echo "private Urus env file is missing: $env_file" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
. "$env_file"
set +a

python3 - "$config" <<'PY'
import json
import os
import pathlib
import sys

config = pathlib.Path(sys.argv[1])

def values(name):
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]

market_symbols = values("MOOMOO_MARKET_SYMBOLS")
intraday_symbols = values("INTRADAY_OPTIONS_SYMBOLS")
if not market_symbols or not intraday_symbols:
    raise SystemExit("private env must define market and intraday symbols")

host = os.environ.get("MOOMOO_HOST", "").strip()
port = int(os.environ.get("MOOMOO_PORT", "11111"))
market_timezone = os.environ.get("MARKET_TIMEZONE", "America/New_York").strip()
fast_band = float(os.environ.get("INTRADAY_OPTIONS_FAST_BAND_PERCENT", "3"))
structure_band = float(os.environ.get("INTRADAY_OPTIONS_STRUCTURE_BAND_PERCENT", "10"))

config.parent.mkdir(parents=True, exist_ok=True)
config.write_text(
    "\n".join(
        [
            "[runtime]",
            'timezone = "Asia/Tokyo"',
            'output_dir = "/data/urus-moomoo"',
            f'opend_host = {json.dumps(host)}',
            f"opend_port = {port}",
            f'market_timezone = {json.dumps(market_timezone)}',
            'exchange_calendar = "XNYS"',
            'poll_seconds = 10',
            'catch_up_seconds = 600',
            "",
            "[runtime.options]",
            "target_dtes = [0, 1]",
            "max_dte = 7",
            "strike_range_percent = 10",
            "batch_size = 400",
            "min_quote_coverage = 0.80",
            "",
            "[[tasks]]",
            'name = "market-post-close"',
            "enabled = true",
            'schedule = "post-close"',
            'operation = "instrument_cards"',
            "delay_seconds = 1800",
            f"symbols = {json.dumps(market_symbols)}",
            "",
            "[[tasks]]",
            'name = "capital-flow-post-close"',
            "enabled = true",
            'schedule = "post-close"',
            'operation = "capital_flow_day"',
            "delay_seconds = 1800",
            f"symbols = {json.dumps(market_symbols)}",
            "",
            "[[tasks]]",
            'name = "options-intraday"',
            "enabled = true",
            'schedule = "intraday"',
            'operation = "capture_intraday"',
            f"symbols = {json.dumps(intraday_symbols)}",
            "[tasks.params]",
            "interval_seconds = 300",
            "start_margin_seconds = 300",
            "end_margin_seconds = 300",
            'anchor_times = ["09:35", "10:00", "12:30", "15:00", "15:30", "15:50"]',
            f"fast_band_percent = {fast_band}",
            f"structure_band_percent = {structure_band}",
            "",
            "[[tasks]]",
            'name = "options-close-finalizer"',
            "enabled = true",
            'schedule = "post-close"',
            'operation = "capture_session_close"',
            "delay_seconds = 300",
            f"symbols = {json.dumps(intraday_symbols)}",
            "",
            "[[tasks]]",
            'name = "options-eoflora-eod"',
            "enabled = true",
            'schedule = "post-close"',
            'operation = "capture_eod_options"',
            "delay_seconds = 3600",
            f"symbols = {json.dumps(intraday_symbols)}",
            "[tasks.params]",
            "band_percent = 10",
            "max_dte = 45",
        ]
    )
    + "\n",
    encoding="utf-8",
)
PY

"$container_cli" system start >/dev/null 2>&1 || true
if ! "$container_cli" network list | awk 'NR > 1 {print $1}' | grep -Fxq "$network"; then
    "$container_cli" network create "$network" >/dev/null
fi
"$container_cli" image load --input "$archive" || "$container_cli" image load -i "$archive"

# Replace a previous planner instance, but leave the old Urus container intact
# until the new process has proved that it stays running.
"$container_cli" stop "$container_name" >/dev/null 2>&1 || true
"$container_cli" delete "$container_name" >/dev/null 2>&1 || "$container_cli" rm "$container_name" >/dev/null 2>&1 || true
"$container_cli" run --detach --name "$container_name" \
    --network "$network" --cpus "$cpus" --memory "$memory" --init \
    --volume "$data_root:/data" "$image_ref"

healthy=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    state=$("$container_cli" inspect "$container_name" 2>/dev/null | grep -m1 '"state"' || true)
    if printf '%s' "$state" | grep -q 'running'; then
        healthy=1
        break
    fi
    sleep 2
done
if [[ "$healthy" != "1" ]]; then
    echo "urus-moomoo container did not stay running" >&2
    "$container_cli" logs "$container_name" 2>/dev/null || true
    exit 1
fi

# The planner is an independent sidecar. The Urus API/planner container keeps
# running beside it and owns no collection schedule in this deployment.
echo "started $container_name; existing Urus container was left running"
"$container_cli" list
REMOTE_SCRIPT

echo "Deployed $URUS_IMAGE_REF to $REMOTE as $CONTAINER_NAME"
