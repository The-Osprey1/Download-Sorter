@echo off
setlocal
cd /d "%~dp0"

echo =======================================================
echo     Download Sorter - Standalone Release Builder
echo =======================================================
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" build_release.py
) else (
    python build_release.py
)

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Build failed!
    pause
    exit /b %errorlevel%
)

echo.
pause
