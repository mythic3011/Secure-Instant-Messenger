@echo off
setlocal ENABLEEXTENSIONS

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "PROJECT_ROOT=%%~fI"
pushd "%PROJECT_ROOT%" >nul

set "CHECK_ONLY=0"
set "SKIP_HEALTH_CHECK=0"
if "%UV_CACHE_DIR%"=="" set "UV_CACHE_DIR=%PROJECT_ROOT%\.uv-cache"
set "SERVER_URL=https://localhost:8443"
set "SERVER_URL_SET=0"
set "CA_CERT="
set "FORWARD_ARGS="

:parse_args
if "%~1"=="" goto args_done
set "CURRENT_ARG=%~1"

if "%~1"=="--check" (
  set "CHECK_ONLY=1"
  goto args_done
)

if "%~1"=="--no-health-check" (
  set "SKIP_HEALTH_CHECK=1"
  shift
  goto parse_args
)

if "%~1"=="--ca-cert" (
  if "%~2"=="" (
    echo [run-client] --ca-cert requires a path 1>&2
    popd >nul
    exit /b 2
  )
  set "CA_CERT=%~2"
  if defined FORWARD_ARGS (
    set "FORWARD_ARGS=%FORWARD_ARGS% %1 %2"
  ) else (
    set "FORWARD_ARGS=%1 %2"
  )
  shift
  shift
  goto parse_args
)

if /I "%CURRENT_ARG:~0,7%"=="http://" (
  if "%SERVER_URL_SET%"=="0" (
    set "SERVER_URL=%CURRENT_ARG%"
    set "SERVER_URL_SET=1"
    shift
    goto parse_args
  )
)

if /I "%CURRENT_ARG:~0,8%"=="https://" (
  if "%SERVER_URL_SET%"=="0" (
    set "SERVER_URL=%CURRENT_ARG%"
    set "SERVER_URL_SET=1"
    shift
    goto parse_args
  )
)

if defined FORWARD_ARGS (
  set "FORWARD_ARGS=%FORWARD_ARGS% %1"
) else (
  set "FORWARD_ARGS=%1"
)
shift
goto parse_args

:args_done

where uv >nul 2>nul
if errorlevel 1 (
  echo [run-client] missing prerequisite: uv 1>&2
  echo [run-client] install uv manually first. 1>&2
  echo [run-client] canonical installer: install.sh 1>&2
  echo [run-client] run it from a POSIX shell if available: sh ./install.sh --check 1>&2
  echo [run-client] otherwise follow manual setup in docs\DEPLOY.md 1>&2
  popd >nul
  exit /b 1
)

uv run python -c "import textual, httpx, aiosqlite, cryptography, pyotp" >nul 2>nul
if errorlevel 1 (
  echo [run-client] environment not prepared or dependencies may be unsynced 1>&2
  echo [run-client] canonical installer: install.sh 1>&2
  echo [run-client] run it from a POSIX shell if available: sh ./install.sh --fix 1>&2
  echo [run-client] otherwise follow manual setup in docs\DEPLOY.md 1>&2
  popd >nul
  exit /b 1
)

if "%CHECK_ONLY%"=="1" (
  uv run python -c "import client.main" >nul
  if errorlevel 1 (
    popd >nul
    exit /b 1
  )
  echo [run-client] check passed
  popd >nul
  exit /b 0
)

if not "%SKIP_HEALTH_CHECK%"=="1" (
  if defined CA_CERT (
    uv run python scripts/lib/check_server_health.py --server "%SERVER_URL%" --ca-cert "%CA_CERT%" >nul
  ) else (
    uv run python scripts/lib/check_server_health.py --server "%SERVER_URL%" >nul
  )
  if errorlevel 1 (
    echo [run-client] server health check failed 1>&2
    if not defined CA_CERT (
      echo [run-client] if using a local cert, pass --ca-cert .\certs\server.crt 1>&2
    )
    echo [run-client] rerun with --no-health-check to bypass preflight 1>&2
    popd >nul
    exit /b 1
  )
)

uv run python -m client.main --server "%SERVER_URL%" %FORWARD_ARGS%
set "RESULT=%ERRORLEVEL%"
popd >nul
exit /b %RESULT%
