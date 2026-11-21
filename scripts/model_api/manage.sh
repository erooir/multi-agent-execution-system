#!/usr/bin/env bash
set -euo pipefail
umask 077
config=${HANGZHI_MODEL_SERVER_ENV:?Point HANGZHI_MODEL_SERVER_ENV at the private compose.env}
if [[ ! -f "$config" ]] || (( (8#$(stat -c %a "$config") & 077) != 0 )); then
    echo 'compose.env must exist with owner-only permissions' >&2
    exit 1
fi
directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
set -a
source "$config"
set +a
compose=(docker compose --env-file "$config" -f "$directory/compose.yaml")
operation=${1:-status}
case "$operation" in
    start|restart)
        [[ -f "$HANGZHI_MODEL_DIR/SHA256SUMS" ]] || { echo 'Model download not verified' >&2; exit 1; }
        [[ -s "$HANGZHI_MODEL_KEY_FILE" ]] || { echo 'API key missing' >&2; exit 1; }
        if (( (8#$(stat -c %a "$HANGZHI_MODEL_KEY_FILE") & 077) != 0 )); then
            echo 'API key must have owner-only permissions' >&2; exit 1
        fi
        if [[ "$operation" == restart ]]; then
            "${compose[@]}" stop --timeout 90
            sleep 2
        elif [[ -n $("${compose[@]}" ps --status running -q model-api) ]]; then
            echo 'Model container already running'; exit 0
        fi
        nvidia-smi --id="$HANGZHI_GPU_0,$HANGZHI_GPU_1" --query-gpu=memory.used --format=csv,noheader,nounits |
            awk '$1 > 256 { bad=1 } END { exit bad }' || { echo 'Selected GPUs are not idle' >&2; exit 1; }
        mkdir -p "$HANGZHI_INFERENCE_CACHE/cuda"
        "${compose[@]}" up -d --no-build --pull never
        ;;
    stop) "${compose[@]}" stop --timeout 90 ;;
    status) "${compose[@]}" ps ;;
    logs) "${compose[@]}" logs --tail 80 ;;
    *) echo 'Usage: bash manage.sh start|stop|restart|status|logs' >&2; exit 2 ;;
esac
