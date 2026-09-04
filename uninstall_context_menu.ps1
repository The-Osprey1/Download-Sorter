$ErrorActionPreference = "SilentlyContinue"

Write-Host "Removing DownloadSorter Explorer context menus from HKCU..."

Remove-Item -Path "HKCU:\Software\Classes\Directory\shell\DownloadSorter" -Recurse -Force
Remove-Item -Path "HKCU:\Software\Classes\Directory\Background\shell\DownloadSorter" -Recurse -Force
Remove-Item -Path "HKCU:\Software\Classes\Directory\Background\shell\DownloadSorterUndo" -Recurse -Force

foreach ($ext in @(".3mf", ".gcode", ".bgcode")) {
    Remove-Item -Path "HKCU:\Software\Classes\SystemFileAssociations\$ext\shell\GCodePeek" -Recurse -Force
}

Remove-Item -Path "HKCU:\Software\Classes\SystemFileAssociations\.zip\shell\Unzip3D" -Recurse -Force
Remove-Item -Path "HKCU:\Software\Classes\SystemFileAssociations\.zip\shell\AutoUnzip" -Recurse -Force

Write-Host "Context menus removed successfully."
