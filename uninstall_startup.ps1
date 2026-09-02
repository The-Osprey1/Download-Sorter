$taskName = "DownloadSorter"
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match "pythonw?\.exe" -and $_.CommandLine -match "download-sorter\\watch\.py"
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "Removed scheduled task '$taskName' (if it existed)."
