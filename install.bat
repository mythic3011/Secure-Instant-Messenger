@echo off
setlocal ENABLEEXTENSIONS

set "MODE=check"
set "VERBOSE=0"
set "SCRIPT_PATH=%~f0"
for %%I in ("%SCRIPT_PATH%") do set "PROJECT_ROOT=%%~dpI"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--check" (
  set "MODE=check"
  shift
  goto parse_args
)
if /I "%~1"=="--fix" (
  set "MODE=fix"
  shift
  goto parse_args
)
if /I "%~1"=="--verbose" (
  set "VERBOSE=1"
  shift
  goto parse_args
)
echo Usage: install.bat [--check^|--fix] [--verbose] 1>&2
exit /b 2

:args_done

if "%UV_CACHE_DIR%"=="" set "UV_CACHE_DIR=%PROJECT_ROOT%\.uv-cache"

set "PYTHON_EXE="
for /f "delims=" %%I in ('where python 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%I"
if not defined PYTHON_EXE (
  echo [install] unsupported environment: python command not found on Windows PATH 1>&2
  exit /b 2
)
"%PYTHON_EXE%" --version >nul 2>&1
if errorlevel 1 (
  echo [install] unsupported environment: python command is not runnable on Windows PATH 1>&2
  exit /b 2
)

set "UV_EXE="
for /f "delims=" %%I in ('where uv 2^>nul') do if not defined UV_EXE set "UV_EXE=%%I"
if not defined UV_EXE (
  echo [install] check failed: uv not found 1>&2
  echo [install] install uv first, then rerun install.bat --fix 1>&2
  exit /b 1
)

if not exist "%PROJECT_ROOT%\pyproject.toml" (
  echo [install] unsupported environment: project metadata missing at %PROJECT_ROOT%\pyproject.toml 1>&2
  exit /b 2
)

if "%VERBOSE%"=="1" (
  echo [install] project root: %PROJECT_ROOT%
)

if /I "%MODE%"=="check" (
  echo [install] check passed
  exit /b 0
)

pushd "%PROJECT_ROOT%" >nul || (
  echo [install] unsupported environment: unable to enter project root 1>&2
  exit /b 2
)

if not exist "%PROJECT_ROOT%\.env.local" (
  if not exist "%PROJECT_ROOT%\.env.example" (
    popd >nul
    echo [install] fix failed: .env.example not found 1>&2
    exit /b 1
  )
  copy /Y "%PROJECT_ROOT%\.env.example" "%PROJECT_ROOT%\.env.local" >nul || (
    popd >nul
    echo [install] fix failed: unable to create .env.local 1>&2
    exit /b 1
  )
  if "%VERBOSE%"=="1" echo [install] created .env.local from .env.example
)

"%PYTHON_EXE%" "%PROJECT_ROOT%\scripts\lib\bootstrap_env.py" dev >nul || (
  popd >nul
  echo [install] fix failed: bootstrap env update failed 1>&2
  exit /b 1
)

if "%VERBOSE%"=="1" echo [install] running uv sync in %PROJECT_ROOT%
"%UV_EXE%" --project "%PROJECT_ROOT%" sync || (
  popd >nul
  echo [install] fix failed: uv sync failed 1>&2
  exit /b 1
)

echo [install] fix completed
popd >nul
exit /b 0
