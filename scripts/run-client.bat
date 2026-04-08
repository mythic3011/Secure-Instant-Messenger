@echo off
rem Legacy compatibility shim.
rem Canonical path: scripts\launch\run-client.bat
rem No logic should be added here.
call "%~dp0launch\run-client.bat" %*
exit /b %ERRORLEVEL%
