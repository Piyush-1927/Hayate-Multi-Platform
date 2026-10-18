#define AppName "Hayate"
#define AppVersion "0.1.3"

[Setup]
AppId={{B5C1E8A2-6F4D-4C7A-9E33-2D1A7C0F5B91}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=..\..\dist
OutputBaseFilename=Hayate-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Hayate.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\..\dist\Hayate\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Hayate"; Filename: "{app}\Hayate.exe"
Name: "{autodesktop}\Hayate"; Filename: "{app}\Hayate.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Hayate.exe"; Description: "Launch Hayate"; Flags: nowait postinstall skipifsilent
