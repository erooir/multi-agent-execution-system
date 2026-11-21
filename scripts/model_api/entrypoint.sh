#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ -f /model/SHA256SUMS ]] || { echo 'Model verification has not completed' >&2; exit 1; }
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy HF_TOKEN HUGGING_FACE_HUB_TOKEN
unset VLLM_API_KEY
exec python /opt/hangzhi/serve_with_keys.py "$@"
