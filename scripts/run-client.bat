@echo off
setlocal ENABLEEXTENSIONS

set "CHECK_ONLY=0"
if "%UV_CACHE_DIR%"=="" set "UV_CACHE_DIR=%CD%\.uv-cache"
set "SERVER_URL=https://localhost:8443"

if "%~1"=="--check" (
  set "CHECK_ONLY=1"
) else if not "%~1"=="" (
  set "SERVER_URL=%~1"
)

where uv >nul 2>nul
if errorlevel 1 (
  echo [run-client] uv not found. Install uv and run "uv sync" first.
  exit /b 1
)

uv run python -c "import textual, httpx, aiosqlite, cryptography, pyotp" >nul 2>nul
if errorlevel 1 (
  echo [run-client] Missing Python dependencies. Run "uv sync" first.
  exit /b 1
)

if "%CHECK_ONLY%"=="1" (
  uv run python -m client.main --help >nul
  if errorlevel 1 exit /b 1
  echo [run-client] check passed
  exit /b 0
)

uv run python -m client.main --server "%SERVER_URL%"
