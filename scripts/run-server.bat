@echo off
setlocal ENABLEEXTENSIONS
set "CHECK_ONLY=0"
if "%UV_CACHE_DIR%"=="" set "UV_CACHE_DIR=%CD%\.uv-cache"

if "%~1"=="--check" set "CHECK_ONLY=1"

where uv >nul 2>nul
if errorlevel 1 (
  echo [run-server] uv not found. Install uv and run "uv sync" first.
  exit /b 1
)

if not exist .env.local (
  echo [run-server] .env.local not found. Run "scripts\bootstrap-env.bat" first.
  exit /b 1
)

uv run python -c "import fastapi, uvicorn, aiosqlite, cryptography, argon2, pyotp, httpx" >nul 2>nul
if errorlevel 1 (
  echo [run-server] Missing Python dependencies. Run "uv sync" first.
  exit /b 1
)

if "%CHECK_ONLY%"=="1" (
  uv run python -c "from server.main import app; print(app.title)" >nul
  if errorlevel 1 exit /b 1
  echo [run-server] check passed
  exit /b 0
)

uv run uvicorn server.main:app --host 0.0.0.0 --port 8443
