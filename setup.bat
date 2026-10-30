@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "SILENT=0"
if /i "%~1"=="/silent" set "SILENT=1"
if /i "%~1"=="--silent" set "SILENT=1"
if /i "%~1"=="/s" set "SILENT=1"
if /i "%~1"=="-s" set "SILENT=1"
if /i "%~1"=="-y" set "SILENT=1"
if /i "%~1"=="--yes" set "SILENT=1"
if /i "%~1"=="/y" set "SILENT=1"
if /i "%~1"=="--quiet" set "SILENT=1"
if /i "%~1"=="/quiet" set "SILENT=1"

echo =======================================================
echo          Download Sorter - Quick Setup
echo =======================================================
echo.

:: ---------------------------------------------------------
:: 1. Standalone Binary Distribution Mode
:: ---------------------------------------------------------
if exist "DownloadSorter.exe" (
    echo [*] Standalone Windows distribution detected.
    echo.
    if "%SILENT%"=="1" (
        set "INSTALL_STARTUP=y"
        set "INSTALL_MENU=y"
    ) else (
        set /p INSTALL_STARTUP="Do you want Download Sorter to run automatically at Windows login? [Y/n]: "
        set /p INSTALL_MENU="Do you want Explorer right-click context menus (Sort Folder, 3D Peek)? [Y/n]: "
    )

    if /i not "!INSTALL_STARTUP!"=="n" (
        echo [*] Registering Windows Scheduled Task...
        powershell -ExecutionPolicy Bypass -File install_startup.ps1
    )

    if /i not "!INSTALL_MENU!"=="n" (
        echo [*] Registering Explorer context menus in HKCU...
        powershell -ExecutionPolicy Bypass -File install_context_menu.ps1
    )

    echo.
    echo [*] Starting Download Sorter system tray icon...
    start "" "DownloadSorter.exe" --tray
    echo.
    echo =======================================================
    echo  Setup complete! Look for the folder icon in your tray.
    echo =======================================================
    timeout /t 4 >nul
    exit /b 0
)

:: ---------------------------------------------------------
:: 2. Python Source Mode: Detect or Install Python
:: ---------------------------------------------------------
set "PY_CMD="

:: Check standard python
where python >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    goto :PYTHON_FOUND
)

:: Check official Windows Python Launcher py
where py >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=py -3"
    goto :PYTHON_FOUND
)

:: Check python3
where python3 >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=python3"
    goto :PYTHON_FOUND
)

:: If Python is not on PATH, check if winget is available
where winget >nul 2>nul
if %errorlevel% equ 0 (
    echo [NOTICE] Python 3.10+ is required but was not found on your system.
    echo Windows Package Manager (winget) was detected.
    echo.
    if "%SILENT%"=="1" (
        set "WINGET_INSTALL=y"
    ) else (
        set /p WINGET_INSTALL="Would you like setup to automatically install Python 3.12 via winget? [Y/n]: "
    )
    if /i not "!WINGET_INSTALL!"=="n" (
        echo [*] Installing Python 3.12 via winget...
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        echo.
        echo [OK] Python installation completed. Please run setup.bat again to continue.
        if "%SILENT%"=="0" pause
        exit /b 0
    )
)

echo [ERROR] Python 3.10+ was not found on your system.
echo Please install Python 3.10+ from https://www.python.org/
echo Make sure to check "Add python.exe to PATH" during installation.
if "%SILENT%"=="0" pause
exit /b 1

:PYTHON_FOUND
echo [OK] Python interpreter detected: !PY_CMD!

if not exist ".venv\Scripts\python.exe" (
    echo [*] Creating virtual environment (.venv)...
    !PY_CMD! -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        if "%SILENT%"=="0" pause
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
    if "%SILENT%"=="0" pause
    exit /b 1
)
echo [OK] Dependencies installed successfully.
echo.

if "%SILENT%"=="1" (
    set "INSTALL_STARTUP=y"
    set "INSTALL_MENU=y"
) else (
    set /p INSTALL_STARTUP="Do you want Download Sorter to run automatically at Windows login? [Y/n]: "
    set /p INSTALL_MENU="Do you want Explorer right-click context menus (Sort Folder, 3D file peek)? [Y/n]: "
)

if /i not "!INSTALL_STARTUP!"=="n" (
    echo [*] Registering Windows Scheduled Task...
    powershell -ExecutionPolicy Bypass -File install_startup.ps1
)

echo.
if /i not "!INSTALL_MENU!"=="n" (
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
