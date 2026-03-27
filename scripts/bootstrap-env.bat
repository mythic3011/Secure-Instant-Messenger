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

python -c "import pathlib, secrets, re, sys; mode=sys.argv[1]; env_path=pathlib.Path('.env.local'); text=env_path.read_text(); weak={'replace_with_random_64_hex_chars','changeme','secret','password','default','example'}; def repl(name, value): return re.sub(rf'^{name}=.*$', f'{name}={value}', text, flags=re.M) if re.search(rf'^{name}=.*$', text, flags=re.M) else text + f'\n{name}={value}\n'; current=dict(re.findall(r'^([A-Z_][A-Z0-9_]*)=(.*)$', text, flags=re.M));\nfor name in ('TOKEN_SECRET_KEY','TOTP_ENCRYPTION_KEY'):\n v=current.get(name,'');\n if mode=='production' or (not v or len(v) < 64 or v.lower() in weak):\n  text = re.sub(rf'^{name}=.*$', f'{name}={secrets.token_hex(32)}', text, flags=re.M) if re.search(rf'^{name}=.*$', text, flags=re.M) else text + f'\n{name}={secrets.token_hex(32)}\n';\napp_env='production' if mode=='production' else current.get('APP_ENV','development') or 'development';\ntext = re.sub(r'^APP_ENV=.*$', f'APP_ENV={app_env}', text, flags=re.M) if re.search(r'^APP_ENV=.*$', text, flags=re.M) else text + f'\nAPP_ENV={app_env}\n';\ntext = re.sub(r'^DATABASE_URL=.*$', 'DATABASE_URL=sqlite+aiosqlite:///./data/im.db', text, flags=re.M) if re.search(r'^DATABASE_URL=.*$', text, flags=re.M) else text + '\nDATABASE_URL=sqlite+aiosqlite:///./data/im.db\n';\ntext = re.sub(r'^TLS_CERT_FILE=.*$', 'TLS_CERT_FILE=./certs/server.crt', text, flags=re.M) if re.search(r'^TLS_CERT_FILE=.*$', text, flags=re.M) else text + '\nTLS_CERT_FILE=./certs/server.crt\n';\ntext = re.sub(r'^TLS_KEY_FILE=.*$', 'TLS_KEY_FILE=./certs/server.key', text, flags=re.M) if re.search(r'^TLS_KEY_FILE=.*$', text, flags=re.M) else text + '\nTLS_KEY_FILE=./certs/server.key\n';\nenv_path.write_text(text.strip() + '\n'); print('.env.local updated for', mode)" "%MODE%"
if errorlevel 1 exit /b 1

if not exist certs mkdir certs

where openssl >nul 2>nul
if errorlevel 1 (
  echo [bootstrap-env] openssl not found; skipping TLS certificate generation
  exit /b 0
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
  -subj "/C=HK/ST=HK/L=HongKong/O=LocalDev/OU=Dev/CN=localhost" >nul 2>nul
if errorlevel 1 (
  echo [bootstrap-env] openssl certificate generation failed
  exit /b 1
)
echo TLS cert at certs\server.crt
echo TLS key at certs\server.key
exit /b 0
