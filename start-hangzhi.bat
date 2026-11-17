@echo off
setlocal EnableExtensions
title Hangzhi v2 Launcher
cd /d "%~dp0"
set "ROOT=%CD%"

rem ---- Paths. Only edit DM_EXE / DM_INI if DM8 was installed elsewhere. ----
set "DM_EXE=D:\dmdbms\bin\dmserver.exe"
set "DM_INI=D:\dmdbms\data\HZDM\HZDM\dm.ini"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "DSN=%ROOT%\.local\dm-dsn.txt"
set "API=http://127.0.0.1:8000"
set "API_LOG=.local\start-api.log"
set "WORKER_LOG=.local\start-worker.log"

echo.
echo ==========================================================
echo   Hangzhi v2  -  one-click launcher
echo   %ROOT%
echo ==========================================================
echo.

if not exist "%PY%" ( echo [ERROR] Python runtime missing: %PY% & goto :fail )
if not exist "%DSN%" ( echo [ERROR] DM connection file missing: %DSN% & goto :fail )
if not exist "%DM_EXE%" ( echo [ERROR] DM8 server missing: %DM_EXE% & goto :fail )
if not exist "%DM_INI%" ( echo [ERROR] DM8 config missing: %DM_INI% & goto :fail )

rem ---- Runtime settings: same as the working online configuration. ----
set "HANGZHI_DATABASE_URL="
set /p HANGZHI_DATABASE_URL=<"%DSN%"
if not defined HANGZHI_DATABASE_URL ( echo [ERROR] DM connection string is empty. & goto :fail )
set "HANGZHI_OFFLINE=false"
set "HANGZHI_BUDGET_LEDGER_PATH=%ROOT%\.local\budget.sqlite3"
set "AGNO_TELEMETRY=false"
set "HF_HUB_OFFLINE=1"
set "PROJ_NETWORK=OFF"

rem ==========================================================
echo [1/3] DM8 database
"%PY%" -c "from backend.app.platform.db import repo; repo.list('projects')" >nul 2>&1
if not errorlevel 1 (
  echo       OK - already running, skipped.
  goto :api
)
echo       Starting DM8 ...
start "Hangzhi DM8" /MIN "%DM_EXE%" "%DM_INI%" -noconsole listen_ipv6=0
for /L %%i in (1,1,40) do (
  "%PY%" -c "from backend.app.platform.db import repo; repo.list('projects')" >nul 2>&1
  if not errorlevel 1 goto :dm_ready
  ping -n 2 127.0.0.1 >nul 2>&1
)
echo       [ERROR] DM8 did not become ready within 40 seconds.
goto :fail
:dm_ready
echo       OK - database reachable.

rem ==========================================================
:api
echo [2/3] FastAPI  %API%
curl -fsS -o NUL "%API%/api/v2/health" >nul 2>&1
if not errorlevel 1 (
  echo       OK - already running, skipped.
  goto :worker
)
echo       Starting API ...
start "Hangzhi API" /MIN cmd /c ".venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 > %API_LOG% 2>&1"
for /L %%i in (1,1,40) do (
  curl -fsS -o NUL "%API%/api/v2/health" >nul 2>&1
  if not errorlevel 1 goto :api_ready
  ping -n 2 127.0.0.1 >nul 2>&1
)
echo       [ERROR] API did not become ready within 40 seconds. Log tail:
powershell -NoProfile -Command "if (Test-Path '%API_LOG%') { Get-Content '%API_LOG%' -Tail 12 }"
goto :fail
:api_ready
echo       OK - API is healthy.

rem ==========================================================
:worker
echo [3/3] Task worker
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*backend.app.platform.worker*' }) { exit 0 } else { exit 1 }" >nul 2>&1
if not errorlevel 1 (
  echo       OK - already running, skipped.
  goto :summary
)
echo       Starting worker ...
start "Hangzhi Worker" /MIN cmd /c ".venv\Scripts\python.exe -m backend.app.platform.worker > %WORKER_LOG% 2>&1"
ping -n 4 127.0.0.1 >nul 2>&1
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*backend.app.platform.worker*' }) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
  echo       [ERROR] Worker exited immediately. Log tail:
  powershell -NoProfile -Command "if (Test-Path '%WORKER_LOG%') { Get-Content '%WORKER_LOG%' -Tail 12 }"
  goto :fail
)
echo       OK - worker is running.

rem ==========================================================
:summary
echo.
echo ==========================================================
curl -fsS "%API%/api/v2/health"
echo.
echo   Platform : %API%/
echo   Manage   : %API%/manage
echo   Logs     : %API_LOG%  /  %WORKER_LOG%
echo   All services are up. Close this window anytime;
echo   the service windows keep running in the background.
echo ==========================================================
if /i "%~1"=="--no-pause" exit /b 0
echo.
pause
exit /b 0

:fail
echo.
echo Startup failed. Fix the message above and run this file again.
if /i "%~1"=="--no-pause" exit /b 1
pause
exit /b 1
