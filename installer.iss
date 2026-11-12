; Download Sorter Inno Setup Script
; Local-first automated file organizer for Windows

#define MyAppName "Download Sorter"
#define MyAppVersion "2.6.0"
#define MyAppPublisher "Devyn Fidel"
#define MyAppURL "https://github.com/The-Osprey1/Download-Sorter"
#define MyAppExeName "DownloadSorter.exe"

[Setup]
AppId={{E14A89E2-7634-4C3D-8824-A821946DF944}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\DownloadSorter
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=DownloadSorter-Setup-v{#MyAppVersion}-windows-x64
SetupIconFile=DownloadSorter.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\DownloadSorter\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\DownloadSorter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--welcome"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Stop the tray app and remove the startup entries and Explorer right-click menus
; before the files are deleted. Personal rules in %LOCALAPPDATA%\download-sorter are kept.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\uninstall.ps1"" -Silent -KeepUserData"; Flags: runhidden waituntilterminated; RunOnceId: "DownloadSorterCleanup"
