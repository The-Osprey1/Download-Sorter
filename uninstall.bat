@echo off
setlocal
cd /d "%~dp0"

powershell -ExecutionPolicy Bypass -File uninstall.ps1 %*
if %errorlevel% neq 0 (
    pause
    exit /b %errorlevel%
)
timeout /t 3 >nul
