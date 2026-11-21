param(
    [Parameter(Mandatory=$true)][string]$KeyFile,
    [ValidateRange(1,32767)][int]$MaxOutputTokens = 16000,
    [ValidateRange(0,10000)][int]$MaxTaskRequests = 60
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $KeyFile -PathType Leaf)) {
    throw 'Model API key file is missing. Copy it privately over SSH/SCP first.'
}
$modelApiKey = ([string](Get-Content -LiteralPath $KeyFile -Raw -Encoding UTF8)).Trim()
if ([string]::IsNullOrWhiteSpace($modelApiKey)) { throw 'Model API key file is empty.' }

# Process environment is inherited by both API and Worker launched here.
# Never print the credential or save it in a tracked file/user environment.
$env:HANGZHI_UPSTREAM_MODEL_URL = 'http://127.0.0.1:8001/v1'
$env:HANGZHI_UPSTREAM_MODEL_ID = 'hangzhi-qwen38'
$env:HANGZHI_UPSTREAM_BILLING = 'internal_zero'
$env:HANGZHI_UPSTREAM_API_KEY = $modelApiKey
$env:HANGZHI_UPSTREAM_TIMEOUT_SECONDS = '180'
$env:HANGZHI_UPSTREAM_MAX_OUTPUT_TOKENS = [string]$MaxOutputTokens
$env:HANGZHI_UPSTREAM_MAX_TASK_REQUESTS = [string]$MaxTaskRequests
$env:HANGZHI_UPSTREAM_TEMPERATURE = '0'
$env:HANGZHI_UPSTREAM_TOP_P = '1'
$env:HANGZHI_UPSTREAM_ENABLE_THINKING = 'false'
$env:HANGZHI_CONTEXT_TOKENS = '32768'
$env:HANGZHI_MODEL_SLOTS = '2'
# This app flag currently blocks intranet HTTP as well as cloud calls.
# The A100 model container still has no external network route.
$env:HANGZHI_OFFLINE = 'false'
Remove-Variable modelApiKey
Write-Output 'Configured hangzhi-qwen38 at http://127.0.0.1:8001/v1; dedicated API key loaded without displaying it.'
Write-Output 'Restart this workspace API and Worker from this PowerShell session to apply the configuration.'
