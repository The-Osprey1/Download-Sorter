<#
.SYNOPSIS
    Complete uninstallation script for Download Sorter.
.DESCRIPTION
    1. Stops running Download Sorter processes (tray, watch, quick switcher).
    2. Removes the Windows Scheduled Task (logon startup).
    3. Removes Explorer context menus from HKCU.
    4. Optionally purges user data in %LOCALAPPDATA%\download-sorter.
#>

param(
    [Alias("purge", "p")]
    [switch]$PurgeUserData,

    [Alias("keep", "k")]
    [switch]$KeepUserData,

    [Alias("y", "yes", "s", "quiet")]
    [switch]$Silent
)

$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "       Download Sorter - Clean Uninstaller" -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Stop running processes
Write-Host "[*] Stopping running processes..." -ForegroundColor Yellow
$killed = 0
Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -match "pythonw?\.exe" -and ($_.CommandLine -match "tray\.py" -or $_.CommandLine -match "watch\.py" -or $_.CommandLine -match "download-sorter" -or $_.CommandLine -match "Download-Sorter")) -or
    ($_.Name -eq "DownloadSorter.exe") -or
    ($_.Name -eq "DownloadSorter-cli.exe")
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    $killed++
}
if ($killed -gt 0) {
    Write-Host "[OK] Stopped $killed process(es)." -ForegroundColor Green
} else {
    Write-Host "[OK] No active sorter processes were running." -ForegroundColor Gray
}

# 2. Remove scheduled task
Write-Host "[*] Removing Windows Scheduled Task..." -ForegroundColor Yellow
$startupUninstaller = Join-Path $root "uninstall_startup.ps1"
if (Test-Path $startupUninstaller) {
    & powershell.exe -ExecutionPolicy Bypass -File $startupUninstaller | Out-Null
} else {
    Unregister-ScheduledTask -TaskName "DownloadSorter" -Confirm:$false -ErrorAction SilentlyContinue
}
Write-Host "[OK] Startup task removed." -ForegroundColor Green

# 3. Remove context menus
Write-Host "[*] Removing Explorer context menus..." -ForegroundColor Yellow
$menuUninstaller = Join-Path $root "uninstall_context_menu.ps1"
if (Test-Path $menuUninstaller) {
    & powershell.exe -ExecutionPolicy Bypass -File $menuUninstaller | Out-Null
}
Write-Host "[OK] Context menus removed from HKCU." -ForegroundColor Green

# 4. Handle user AppData
$appDataDir = Join-Path $env:LOCALAPPDATA "download-sorter"
if (Test-Path $appDataDir) {
    $shouldPurge = $false
    if ($PurgeUserData) {
        $shouldPurge = $true
    } elseif (-not $KeepUserData -and -not $Silent) {
        $reply = Read-Host "Do you want to delete personal rule configs and history in $appDataDir? [y/N]"
        if ($reply -match "^[yY]") { $shouldPurge = $true }
    }

    if ($shouldPurge) {
        Write-Host "[*] Removing personal data at $appDataDir..." -ForegroundColor Yellow
        Remove-Item -Path $appDataDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] Personal data deleted." -ForegroundColor Green
    } else {
        Write-Host "[INFO] Preserved personal data at $appDataDir." -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Green
Write-Host "  Download Sorter has been completely uninstalled." -ForegroundColor Green
Write-Host "=======================================================" -ForegroundColor Green
