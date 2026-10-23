; Orbit installer (Inno Setup 6). Built by build.ps1; run PyInstaller first.
#define AppName "Orbit"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppExe "Orbit.exe"

[Setup]
AppId={{9B3F2D71-6C4A-4E8B-A1D2-5F7E0C3B8A64}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=ditriz31
AppPublisherURL=https://github.com/ditriz31/Orbit
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; per-user install: no administrator rights needed
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Orbit-Setup-{#AppVersion}
SetupIconFile=..\assets\orbit.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "autostart"; Description: "Start Orbit automatically when I sign in to Windows"; GroupDescription: "Startup:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\Orbit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
; same name and arguments as the shortcut the app itself creates from Setting
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--startup"; WorkingDir: "{app}"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch Orbit now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "StopOrbit"

[UninstallDelete]
; the autostart shortcut can also be created from Setting, so remove it even if the task was not selected
Type: files; Name: "{userstartup}\{#AppName}.lnk"

[Code]
// close a running Orbit so its files can be replaced during an update
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
