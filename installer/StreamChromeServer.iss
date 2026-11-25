#ifndef ReleaseVersion
#define ReleaseVersion "1.1.0"
#endif
#ifndef OnlineMode
#define OnlineMode 0
#endif
#ifndef ReleaseRoot
#define ReleaseRoot "..\dist\portable"
#endif

[Setup]
AppId={{F56E3490-42B2-4D08-A409-6216D54D9E36}
AppName=StreamChrome Server
AppVersion={#ReleaseVersion}
AppPublisher=OhCato
DefaultDirName={localappdata}\Programs\StreamChromeServer
DefaultGroupName=StreamChrome Server
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ArchiveExtraction=full
UsePreviousAppDir=no
UsePreviousTasks=no
DisableProgramGroupPage=yes
DisableDirPage=no
Uninstallable=IsStandard
CreateUninstallRegKey=IsStandard
UninstallDisplayIcon={app}\StreamChromeServer.exe
OutputDir=..\dist
OutputBaseFilename=StreamChromeServer-{#ReleaseVersion}-Setup
SetupIconFile=..\assets\app.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Files]
Source: "{#ReleaseRoot}\StreamChromeServer.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ReleaseRoot}\app.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ReleaseRoot}\THIRD_PARTY_LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion
#if OnlineMode
#include DependencyInclude
#else
Source: "{#ReleaseRoot}\app\ffmpeg\*"; DestDir: "{code:EngineDir}\ffmpeg"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ReleaseRoot}\app\yt-dlp\*"; DestDir: "{code:EngineDir}\yt-dlp"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ReleaseRoot}\app\cloudflared\*"; DestDir: "{code:EngineDir}\cloudflared"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ReleaseRoot}\app\jellyfin\system\*"; DestDir: "{code:EngineDir}\jellyfin\system"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif
Source: "{#ReleaseRoot}\app\licenses\*"; DestDir: "{code:EngineDir}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Dirs]
Name: "{app}\app\data"; Check: IsPortable
Name: "{app}\Movies"; Check: IsPortable
Name: "{app}\Series"; Check: IsPortable

[Tasks]
Name: portableautorun; Description: "Start when this drive connects to this computer (installs a per-user local watcher and Windows startup entry)"; Flags: unchecked; Check: IsPortable
Name: driveicon; Description: "Use the StreamChrome icon for this drive (updates only its autorun.inf icon)"; Flags: unchecked; Check: IsPortable
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked; Check: IsStandard

[Icons]
Name: "{autoprograms}\StreamChrome Server"; Filename: "{app}\StreamChromeServer.exe"; Check: IsStandard
Name: "{autodesktop}\StreamChrome Server"; Filename: "{app}\StreamChromeServer.exe"; Tasks: desktopicon; Check: IsStandard

[Run]
Filename: "{app}\StreamChromeServer.exe"; Parameters: "--configure-integration {{\""autorun\"":true}"; Flags: runhidden waituntilterminated; Tasks: portableautorun; Check: IsPortable
Filename: "{app}\StreamChromeServer.exe"; Parameters: "--configure-integration {{\""drive_icon\"":true}"; Flags: runhidden waituntilterminated; Tasks: driveicon; Check: IsPortable
Filename: "{app}\StreamChromeServer.exe"; Description: "Open StreamChrome Server"; Flags: nowait postinstall skipifsilent

[Code]
var
  ModePage: TInputOptionWizardPage;
  PortableMode: Boolean;

function IsPortable: Boolean;
begin
  Result := PortableMode;
end;

function IsStandard: Boolean;
begin
  Result := not PortableMode;
end;

function EngineDir(Param: String): String;
begin
  Result := ExpandConstant('{app}');
  if PortableMode then Result := Result + '\app';
end;

procedure InitializeWizard;
begin
  PortableMode := CompareText(ExpandConstant('{param:MODE|standard}'), 'portable') = 0;
  ModePage := CreateInputOptionPage(wpWelcome, 'Installation mode',
    'Choose a standard or portable installation',
    'Standard: shortcuts and an uninstaller; data is kept in your Windows profile.' + #13#10 +
    'Portable: choose an HD or USB folder; no startup registration unless selected. Automatic launch needs a local watcher on this computer.', True, False);
#if OnlineMode
  ModePage.SubCaptionLabel.Caption := 'Internet access is required. Official components are downloaded and checked before installation.';
#endif
  ModePage.Add('Standard Windows installation');
  ModePage.Add('Portable HD / USB installation');
  if PortableMode then ModePage.SelectedValueIndex := 1
  else ModePage.SelectedValueIndex := 0;
  if PortableMode and (ExpandConstant('{param:DIR|}') = '') then
    WizardForm.DirEdit.Text := ExpandConstant('{sd}\StreamServer');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = ModePage.ID then begin
    PortableMode := ModePage.SelectedValueIndex = 1;
    if ExpandConstant('{param:DIR|}') = '' then begin
      if PortableMode then WizardForm.DirEdit.Text := ExpandConstant('{sd}\StreamServer')
      else WizardForm.DirEdit.Text := ExpandConstant('{localappdata}\Programs\StreamChromeServer');
    end;
  end;
  if (CurPageID = wpSelectDir) and (not PortableMode) and
      DirExists(ExpandConstant('{app}\app')) then begin
    MsgBox('This folder contains a portable app directory. Select Portable mode to preserve its data layout, or choose another folder.', mbError, MB_OK);
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if (not PortableMode) and DirExists(ExpandConstant('{app}\app')) then
    Result := 'A portable app directory already exists here. Use /MODE=portable or choose a different directory.';
end;
