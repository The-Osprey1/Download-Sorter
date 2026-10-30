$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $root "DownloadSorter.exe"
$isStandalone = Test-Path $exe

$taskName = "DownloadSorter"
if ($isStandalone) {
    $action = New-ScheduledTaskAction -Execute $exe -Argument "--tray" -WorkingDirectory $root
} else {
    $pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
    $entry = Join-Path $root "tray.py"
    if (-not (Test-Path $pythonw)) {
        $sysPythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
        if ($sysPythonw) {
            $pythonw = $sysPythonw
        } else {
            throw "Missing pythonw.exe. Create the venv first (python -m venv .venv) or ensure Python is on PATH."
        }
    }
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$entry`"" -WorkingDirectory $root
}
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Host "Installed and started scheduled task '$taskName' (tray icon)."
Write-Host "It will also start automatically when you log in."
