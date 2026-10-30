$taskName = "DownloadSorter"
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -match "pythonw?\.exe" -and ($_.CommandLine -match "tray\.py" -or $_.CommandLine -match "watch\.py" -or $_.CommandLine -match "download-sorter" -or $_.CommandLine -match "Download-Sorter")) -or
    ($_.Name -eq "DownloadSorter.exe") -or
    ($_.Name -eq "DownloadSorter-cli.exe")
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "Removed scheduled task '$taskName' and stopped background processes."
