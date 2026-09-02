$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$sortOnce = Join-Path $root "sort_once.py"
$gcodePeek = Join-Path $root "gcode_peek.py"
$unzip3D = Join-Path $root "unzip_3d.py"
$undoSort = Join-Path $root "sort_undo.py"

function Set-RegKey {
    param(
        [string]$Path,
        [string]$Name = "",
        [string]$Value
    )
    if (-not (Test-Path $Path)) {
        New-Item -Path $Path -Force | Out-Null
    }
    if ($Name -eq "") {
        Set-Item -Path $Path -Value $Value | Out-Null
    } else {
        Set-ItemProperty -Path $Path -Name $Name -Value $Value | Out-Null
    }
}

Write-Host "Registering Explorer context menus in HKCU..."

# 1. Folder Right-Click: "Sort with DownloadSorter"
$folderKey = "HKCU:\Software\Classes\Directory\shell\DownloadSorter"
Set-RegKey -Path $folderKey -Value "Sort with DownloadSorter"
Set-RegKey -Path $folderKey -Name "Icon" -Value "shell32.dll,43"
Set-RegKey -Path "$folderKey\command" -Value "`"$python`" `"$sortOnce`" --target `"%1`""

# 2. Folder Background Right-Click: "Sort This Folder" & "Undo Last Sort"
$bgSortKey = "HKCU:\Software\Classes\Directory\Background\shell\DownloadSorter"
Set-RegKey -Path $bgSortKey -Value "Sort this folder with DownloadSorter"
Set-RegKey -Path $bgSortKey -Name "Icon" -Value "shell32.dll,43"
Set-RegKey -Path "$bgSortKey\command" -Value "`"$python`" `"$sortOnce`" --target `"%V`""

$bgUndoKey = "HKCU:\Software\Classes\Directory\Background\shell\DownloadSorterUndo"
Set-RegKey -Path $bgUndoKey -Value "Undo Last Sort"
Set-RegKey -Path $bgUndoKey -Name "Icon" -Value "shell32.dll,238"
Set-RegKey -Path "$bgUndoKey\command" -Value "powershell.exe -NoExit -Command & '`"$python`"' '`"$undoSort`"'"

# 3. .3mf / .gcode / .bgcode: "Inspect 3D Print Info"
foreach ($ext in @(".3mf", ".gcode", ".bgcode")) {
    $peekKey = "HKCU:\Software\Classes\SystemFileAssociations\$ext\shell\GCodePeek"
    Set-RegKey -Path $peekKey -Value "Inspect 3D Print Info (gcode-peek)"
    Set-RegKey -Path $peekKey -Name "Icon" -Value "imageres.dll,67"
    Set-RegKey -Path "$peekKey\command" -Value "powershell.exe -NoExit -Command & '`"$python`"' '`"$gcodePeek`"' '`"%1`"'"
}

# 4. .zip: "Extract 3D Models"
$zipKey = "HKCU:\Software\Classes\SystemFileAssociations\.zip\shell\Unzip3D"
Set-RegKey -Path $zipKey -Value "Extract 3D Models to Downloads"
Set-RegKey -Path $zipKey -Name "Icon" -Value "imageres.dll,67"
Set-RegKey -Path "$zipKey\command" -Value "powershell.exe -NoExit -Command & '`"$python`"' '`"$unzip3D`"' '`"%1`"'"

Write-Host "`nSuccessfully installed Explorer context menu shortcuts!"
Write-Host "You can now right-click any folder, 3MF/GCODE file, or 3D ZIP directly in Windows Explorer."
