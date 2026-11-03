# Source this before starting an API/worker. Never download at runtime.
$workspaceMediaHome = Join-Path (Split-Path -Parent $PSScriptRoot) '.local/offline-media'
$mediaHome = if ($env:HANGZHI_MEDIA_HOME) { $env:HANGZHI_MEDIA_HOME } elseif (Test-Path -LiteralPath $workspaceMediaHome) { $workspaceMediaHome } else { 'E:\Hangzhi\offline-media' }
foreach ($programName in @('tesseract','ffmpeg','ffprobe')) {
    $varName = 'HANGZHI_'+$programName.ToUpperInvariant()+'_PATH'
    $programFile = Join-Path $mediaHome "runtime/Library/bin/$programName.exe"
    if (-not [Environment]::GetEnvironmentVariable($varName) -and (Test-Path -LiteralPath $programFile)) {
        [Environment]::SetEnvironmentVariable($varName,$programFile,'Process')
    }
}
$tessDir = Join-Path $mediaHome 'runtime/share/tessdata'
if (-not $env:TESSDATA_PREFIX -and (Test-Path -LiteralPath $tessDir)) { $env:TESSDATA_PREFIX=$tessDir }
if (-not $env:HANGZHI_OCR_LANG -and (Test-Path -LiteralPath (Join-Path $tessDir 'rus.traineddata'))) { $env:HANGZHI_OCR_LANG='eng+rus' }
$modelDir = Join-Path $mediaHome 'models/whisper-base'
if (-not $env:HANGZHI_ASR_MODEL_DIR -and (Test-Path -LiteralPath (Join-Path $modelDir 'model.bin'))) { $env:HANGZHI_ASR_MODEL_DIR=$modelDir }
$env:HF_HUB_OFFLINE='1'
$env:PROJ_NETWORK='OFF'
foreach ($route in @('en_zh','ru_en')) {
    $path=Join-Path $mediaHome ("translation/translate-"+$route+"-1_9")
    $key='HANGZHI_MT_'+$route.ToUpperInvariant()
    if (-not [Environment]::GetEnvironmentVariable($key) -and (Test-Path -LiteralPath (Join-Path $path 'model/model.bin'))) {
        [Environment]::SetEnvironmentVariable($key,$path,'Process')
    }
}
