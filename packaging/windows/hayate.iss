#define AppName "Hayate"
#define AppVersion "0.3.0"
#define WebUrl "https://github.com/Piyush-1927/Hayate-Multi-Platform/releases/download/webengine-addon/Hayate-WebEngine-addon.zip"
#include "webengine-hash.iss"

[Setup]
AppId={{B5C1E8A2-6F4D-4C7A-9E33-2D1A7C0F5B91}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=..\..\dist
OutputBaseFilename=Hayate-Setup
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Hayate.exe

[Tasks]
Name: "webengine"; Description: "Web support: comments and web providers (downloads about 125 MB, needs internet)"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\..\dist\Hayate\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Hayate"; Filename: "{app}\Hayate.exe"
Name: "{autodesktop}\Hayate"; Filename: "{app}\Hayate.exe"; Tasks: desktopicon

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Run]
Filename: "{app}\Hayate.exe"; Description: "Launch Hayate"; Flags: nowait postinstall skipifsilent

[Code]
var
  DownloadPage: TDownloadWizardPage;
  WebOK: Boolean;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), 'Downloading web support...', nil);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpReady) and WizardIsTaskSelected('webengine') then begin
    WebOK := False;
    DownloadPage.Clear;
    DownloadPage.Add('{#WebUrl}', 'webengine.zip', '{#WebSha}');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
        WebOK := True;
      except
        SuppressibleMsgBox('Web support could not be downloaded (' + GetExceptionMessage + '). Hayate will be installed without it.', mbError, MB_OK, IDOK);
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  RC: Integer;
begin
  if (CurStep = ssPostInstall) and WebOK then begin
    WizardForm.StatusLabel.Caption := 'Extracting web support...';
    if (not Exec(ExpandConstant('{sys}\tar.exe'), '-xf "' + ExpandConstant('{tmp}\webengine.zip') + '" -C "' + ExpandConstant('{app}') + '"', '', SW_HIDE, ewWaitUntilTerminated, RC)) or (RC <> 0) then
      SuppressibleMsgBox('Web support could not be extracted. Hayate was installed without it.', mbError, MB_OK, IDOK);
  end;
end;


