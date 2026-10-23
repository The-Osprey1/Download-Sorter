@echo off
setlocal
cd /d "%~dp0"

echo =======================================================
echo          Download Sorter - One-Click Updater
echo =======================================================
echo.

where git >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Git is not found on your PATH.
    echo Please install Git from https://git-scm.com/
    pause
    exit /b 1
)

echo [*] Stopping running Download Sorter instances...
schtasks /End /TN "DownloadSorter" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*tray.py*' -or $_.CommandLine -like '*watch.py*' -or $_.CommandLine -like '*quick_switcher.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
timeout /t 1 >nul

echo [*] Fetching and pulling latest code from git...
git pull
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Git pull encountered conflicts or errors.
    echo If you made local code edits, please check 'git status'.
    pause
    exit /b 1
)
echo [OK] Code updated successfully.
echo.

if exist ".venv\Scripts\pip.exe" (
    echo [*] Checking and updating Python dependencies...
    ".venv\Scripts\pip.exe" install -r requirements.txt --quiet
    if %errorlevel% neq 0 (
        echo [WARNING] Dependency update encountered issues. Continuing...
    ) else (
        echo [OK] Dependencies are up to date.
    )
) else (
    echo [*] Virtual environment not found. Running setup.bat...
    call setup.bat
    exit /b 0
)

echo.
echo [*] Restarting Download Sorter system tray icon...
start "" ".venv\Scripts\pythonw.exe" tray.py

echo.
echo =======================================================
echo  Update complete! Download Sorter is running.
echo =======================================================
timeout /t 3 >nul
