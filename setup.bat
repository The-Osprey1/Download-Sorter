@echo off
setlocal
cd /d "%~dp0"

echo =======================================================
echo          Download Sorter - Quick Setup
echo =======================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python was not found on your PATH.
    echo Please install Python 3.10+ from https://www.python.org/
    echo Make sure to check "Add python.exe to PATH" during installation.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [*] Creating virtual environment (.venv)...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
) else (
    echo [OK] Virtual environment already exists (.venv).
)

echo [*] Installing dependencies from requirements.txt...
".venv\Scripts\pip.exe" install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo [OK] Dependencies installed successfully.
echo.

set /p INSTALL_STARTUP="Do you want Download Sorter to run automatically at Windows login? [Y/n]: "
if /i not "%INSTALL_STARTUP%"=="n" (
    echo [*] Registering Windows Scheduled Task...
    powershell -ExecutionPolicy Bypass -File install_startup.ps1
)

echo.
set /p INSTALL_MENU="Do you want Explorer right-click context menus (Sort Folder, 3D file peek)? [Y/n]: "
if /i not "%INSTALL_MENU%"=="n" (
    echo [*] Registering context menus in HKCU...
    powershell -ExecutionPolicy Bypass -File install_context_menu.ps1
)

echo.
echo [*] Starting Download Sorter system tray icon...
start "" ".venv\Scripts\pythonw.exe" tray.py
echo.
echo =======================================================
echo  Setup complete! Look for the folder icon in your tray.
echo =======================================================
timeout /t 4 >nul
