#!/usr/bin/env bash
set -euo pipefail
umask 077
export VLLM_API_KEY
VLLM_API_KEY=$(cat /run/secrets/model_api_key)
[[ ${#VLLM_API_KEY} -ge 32 ]] || { echo 'API key missing or too short' >&2; exit 1; }
[[ -f /model/SHA256SUMS ]] || { echo 'Model verification has not completed' >&2; exit 1; }
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy HF_TOKEN HUGGING_FACE_HUB_TOKEN
exec python -m vllm.entrypoints.cli.main "$@"
