# Download Sorter
# Made by Devyn Fidel (2026)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $sysPython = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if ($sysPython) {
        $python = $sysPython
    }
}
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

$exe = Join-Path $root "DownloadSorter.exe"
$cliExe = Join-Path $root "DownloadSorter-cli.exe"
if (-not (Test-Path $cliExe)) {
    $cliExe = $exe
}
$isStandalone = (Test-Path $exe) -or (Test-Path $cliExe)

if ($isStandalone) {
    # 1. Any folder: run a one-time sort scoped to exactly that selected folder.
    $folderSortKey = "HKCU:\Software\Classes\Directory\shell\DownloadSorter"
    Set-RegKey -Path $folderSortKey -Value "Sort with DownloadSorter"
    Set-RegKey -Path $folderSortKey -Name "Icon" -Value "imageres.dll,3"
    Set-RegKey -Path "$folderSortKey\command" -Value "`"$exe`" --sort --target `"%1`" --notify"

    # 2. Folder background: restore the most recent recorded batch.
    $undoKey = "HKCU:\Software\Classes\Directory\Background\shell\DownloadSorterUndo"
    Set-RegKey -Path $undoKey -Value "Undo Last DownloadSorter Batch"
    Set-RegKey -Path $undoKey -Name "Icon" -Value "imageres.dll,102"
    Set-RegKey -Path "$undoKey\command" -Value "`"$exe`" --undo --notify"

    # 3. .3mf / .gcode / .bgcode: "Inspect 3D Print Info"
    foreach ($ext in @(".3mf", ".gcode", ".bgcode")) {
        $peekKey = "HKCU:\Software\Classes\SystemFileAssociations\$ext\shell\GCodePeek"
        Set-RegKey -Path $peekKey -Value "Inspect 3D Print Info (gcode-peek)"
        Set-RegKey -Path $peekKey -Name "Icon" -Value "imageres.dll,67"
        Set-RegKey -Path "$peekKey\command" -Value "`"$exe`" --peek `"%1`""
    }

    # 4. .zip: "Extract 3D Models"
    $zipKey = "HKCU:\Software\Classes\SystemFileAssociations\.zip\shell\Unzip3D"
    Set-RegKey -Path $zipKey -Value "Extract 3D Models to Downloads"
    Set-RegKey -Path $zipKey -Name "Icon" -Value "imageres.dll,67"
    Set-RegKey -Path "$zipKey\command" -Value "`"$exe`" --unzip-3d `"%1`""

    # 5. .zip: "Extract with Auto-Unzipper"
    $autoUnzipKey = "HKCU:\Software\Classes\SystemFileAssociations\.zip\shell\AutoUnzip"
    Set-RegKey -Path $autoUnzipKey -Value "Extract with Auto-Unzipper"
    Set-RegKey -Path $autoUnzipKey -Name "Icon" -Value "imageres.dll,165"
    Set-RegKey -Path "$autoUnzipKey\command" -Value "`"$exe`" --unzip `"%1`""
} else {
    $pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
    if (-not (Test-Path $pythonw)) {
        $sysPythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
        if ($sysPythonw) {
            $pythonw = $sysPythonw
        } else {
            $pythonw = $python
        }
    }
    $mainPy = Join-Path $root "main.py"

    # 1. Any folder: run a one-time sort scoped to exactly that selected folder.
    $folderSortKey = "HKCU:\Software\Classes\Directory\shell\DownloadSorter"
    Set-RegKey -Path $folderSortKey -Value "Sort with DownloadSorter"
    Set-RegKey -Path $folderSortKey -Name "Icon" -Value "imageres.dll,3"
    Set-RegKey -Path "$folderSortKey\command" -Value "`"$pythonw`" `"$mainPy`" --sort --target `"%1`" --notify"

    # 2. Folder background: restore the most recent recorded batch.
    $undoKey = "HKCU:\Software\Classes\Directory\Background\shell\DownloadSorterUndo"
    Set-RegKey -Path $undoKey -Value "Undo Last DownloadSorter Batch"
    Set-RegKey -Path $undoKey -Name "Icon" -Value "imageres.dll,102"
    Set-RegKey -Path "$undoKey\command" -Value "`"$pythonw`" `"$mainPy`" --undo --notify"

    # 3. .3mf / .gcode / .bgcode: "Inspect 3D Print Info"
    foreach ($ext in @(".3mf", ".gcode", ".bgcode")) {
        $peekKey = "HKCU:\Software\Classes\SystemFileAssociations\$ext\shell\GCodePeek"
        Set-RegKey -Path $peekKey -Value "Inspect 3D Print Info (gcode-peek)"
        Set-RegKey -Path $peekKey -Name "Icon" -Value "imageres.dll,67"
        Set-RegKey -Path "$peekKey\command" -Value "`"$pythonw`" `"$mainPy`" --peek `"%1`""
    }

    # 4. .zip: "Extract 3D Models"
    $zipKey = "HKCU:\Software\Classes\SystemFileAssociations\.zip\shell\Unzip3D"
    Set-RegKey -Path $zipKey -Value "Extract 3D Models to Downloads"
    Set-RegKey -Path $zipKey -Name "Icon" -Value "imageres.dll,67"
    Set-RegKey -Path "$zipKey\command" -Value "`"$pythonw`" `"$mainPy`" --unzip-3d `"%1`""

    # 5. .zip: "Extract with Auto-Unzipper"
    $autoUnzipKey = "HKCU:\Software\Classes\SystemFileAssociations\.zip\shell\AutoUnzip"
    Set-RegKey -Path $autoUnzipKey -Value "Extract with Auto-Unzipper"
    Set-RegKey -Path $autoUnzipKey -Name "Icon" -Value "imageres.dll,165"
    Set-RegKey -Path "$autoUnzipKey\command" -Value "`"$pythonw`" `"$mainPy`" --unzip `"%1`""
}

Write-Host "`nSuccessfully installed Explorer context menu shortcuts!"
Write-Host "You can now right-click any folder, folder background, 3MF/GCODE file, or ZIP directly in Windows Explorer."
