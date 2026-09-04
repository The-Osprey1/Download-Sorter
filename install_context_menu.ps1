$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$sortOnce = Join-Path $root "sort_once.py"
$gcodePeek = Join-Path $root "gcode_peek.py"
$unzip3D = Join-Path $root "unzip_3d.py"
$unzipArchive = Join-Path $root "unzip_archive.py"
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

# Context menus are scoped to selected folders, folder backgrounds, and supported
# file types so every action has an explicit user-selected target.

# 1. Any folder: run a one-time sort scoped to exactly that selected folder.
$folderSortKey = "HKCU:\Software\Classes\Directory\shell\DownloadSorter"
Set-RegKey -Path $folderSortKey -Value "Sort with DownloadSorter"
Set-RegKey -Path $folderSortKey -Name "Icon" -Value "imageres.dll,3"
Set-RegKey -Path "$folderSortKey\command" -Value "powershell.exe -NoExit -Command & '`"$python`"' '`"$sortOnce`"' --target '`"%1`"'"

# 2. Folder background: restore the most recent recorded batch.
$undoKey = "HKCU:\Software\Classes\Directory\Background\shell\DownloadSorterUndo"
Set-RegKey -Path $undoKey -Value "Undo Last DownloadSorter Batch"
Set-RegKey -Path $undoKey -Name "Icon" -Value "imageres.dll,102"
Set-RegKey -Path "$undoKey\command" -Value "powershell.exe -NoExit -Command & '`"$python`"' '`"$undoSort`"'"

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

# 5. .zip: "Extract with Auto-Unzipper"
$autoUnzipKey = "HKCU:\Software\Classes\SystemFileAssociations\.zip\shell\AutoUnzip"
Set-RegKey -Path $autoUnzipKey -Value "Extract with Auto-Unzipper"
Set-RegKey -Path $autoUnzipKey -Name "Icon" -Value "imageres.dll,165"
Set-RegKey -Path "$autoUnzipKey\command" -Value "powershell.exe -NoExit -Command & '`"$python`"' '`"$unzipArchive`"' '`"%1`"'"

Write-Host "`nSuccessfully installed Explorer context menu shortcuts!"
Write-Host "You can now right-click any folder, folder background, 3MF/GCODE file, or ZIP directly in Windows Explorer."
