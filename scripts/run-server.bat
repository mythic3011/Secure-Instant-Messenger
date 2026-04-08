@echo off
rem Legacy compatibility shim.
rem Canonical path: scripts\launch\run-server.bat
rem No logic should be added here.
call "%~dp0launch\run-server.bat" %*
exit /b %ERRORLEVEL%
