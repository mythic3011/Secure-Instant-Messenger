@echo off
setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION

set "MODE=%~1"
if "%MODE%"=="" set "MODE=dev"

if /I not "%MODE%"=="dev" if /I not "%MODE%"=="production" (
  echo Usage: %~nx0 [dev^|production]
  exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
  echo [bootstrap-env] python not found in PATH
  exit /b 1
)

python -c "import pathlib; p=pathlib.Path('.env.example'); raise SystemExit(0 if p.exists() else 1)"
if errorlevel 1 (
  echo [bootstrap-env] .env.example not found
  exit /b 1
)

python -c "import pathlib, shutil; src=pathlib.Path('.env.example'); dst=pathlib.Path('.env.local'); (shutil.copyfile(src, dst), print('Created .env.local from .env.example')) if not dst.exists() else print('.env.local already exists')"
if errorlevel 1 exit /b 1

python scripts\bootstrap_env.py "%MODE%"
if errorlevel 1 exit /b 1

if not exist data mkdir data
if not exist certs mkdir certs

where openssl >nul 2>nul
if errorlevel 1 (
  echo [bootstrap-env] openssl not found in PATH
  echo [bootstrap-env] OpenSSL is required to generate local TLS certs for this project
  exit /b 1
)

if /I "%MODE%"=="production" goto gen_cert
if not exist certs\server.crt goto gen_cert
if not exist certs\server.key goto gen_cert
echo TLS cert/key already exist
exit /b 0

:gen_cert
echo generating self-signed TLS certificate...
openssl req -x509 -newkey rsa:2048 ^
  -keyout certs\server.key ^
  -out certs\server.crt ^
  -days 365 -nodes ^
  -subj "/C=HK/ST=HK/L=HongKong/O=LocalDev/OU=Dev/CN=localhost" ^
  -addext "subjectAltName=DNS:localhost,DNS:*.orb.local,DNS:server.comp3334-project.orb.local,IP:127.0.0.1" >nul 2>nul
if errorlevel 1 (
  echo [bootstrap-env] openssl certificate generation failed
  exit /b 1
)
echo TLS cert at certs\server.crt
echo TLS key at certs\server.key
exit /b 0
