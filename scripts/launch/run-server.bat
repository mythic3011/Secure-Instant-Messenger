@echo off
setlocal ENABLEEXTENSIONS
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "PROJECT_ROOT=%%~fI"
pushd "%PROJECT_ROOT%" >nul
set "CHECK_ONLY=0"
if "%UV_CACHE_DIR%"=="" set "UV_CACHE_DIR=%PROJECT_ROOT%\.uv-cache"

if "%~1"=="--check" set "CHECK_ONLY=1"

where uv >nul 2>nul
if errorlevel 1 (
  echo [run-server] uv not found. Install uv and run "uv sync" first.
  popd >nul
  exit /b 1
)

if not exist .env.local (
  echo [run-server] .env.local not found. Run "sh ./install.sh --fix" from a POSIX shell, or complete manual setup first.
  popd >nul
  exit /b 1
)

uv run python -c "import fastapi, uvicorn, aiosqlite, cryptography, argon2, pyotp, httpx" >nul 2>nul
if errorlevel 1 (
  echo [run-server] Missing Python dependencies. Run "uv sync" first.
  popd >nul
  exit /b 1
)

if "%CHECK_ONLY%"=="1" (
  uv run python -c "from server.main import app; print(app.title)" >nul
  if errorlevel 1 (
    popd >nul
    exit /b 1
  )
  echo [run-server] check passed
  popd >nul
  exit /b 0
)

uv run python -m server.main
set "RESULT=%ERRORLEVEL%"
popd >nul
exit /b %RESULT%
