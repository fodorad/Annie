; Annie for Windows: per-user installer (Inno Setup 6).
;
; Installs to %LOCALAPPDATA%\Programs\Annie without admin rights:
;   uv.exe, the bundled FFmpeg (ffmpeg\), the launcher (Annie.cmd) and the icon.
; The launcher downloads Python + Annie into the same folder on first start and
; updates Annie from PyPI on every start (see Annie.cmd).
;
; Footprint, on purpose minimal and fully removed by the uninstaller:
;   * the install folder, including everything uv downloaded into it later;
;   * Desktop and Start-menu shortcuts (files, not registry);
;   * ONE registry key, HKCU\...\Uninstall\{AppId}_is1, which Inno writes so Annie
;     appears in Settings > Apps for a normal uninstall. No [Registry] section, no PATH
;     change, no file associations, no services.
; The user's work (%USERPROFILE%\Annie) is kept unless they choose to delete it.
;
; Build (see .github/workflows/windows-installer.yml):
;   installer\windows\fetch-deps.ps1 -OutDir installer\windows\build
;   iscc /DAppVersion=1.2.3 installer\windows\annie.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef DepsDir
  #define DepsDir "build"
#endif
#ifndef OutputDir
  #define OutputDir "Output"
#endif

[Setup]
; Never change AppId: it is how a newer installer finds and replaces this install.
AppId={{E674FD3A-8545-44F6-875F-ED877FE764E3}
AppName=Annie
AppVersion={#AppVersion}
; Annie updates itself, so a version in the Apps list would soon be stale.
AppVerName=Annie
AppPublisher=fodorad
AppPublisherURL=https://github.com/fodorad/Annie
AppSupportURL=https://github.com/fodorad/Annie/issues
DefaultDirName={userpf}\Annie
DefaultGroupName=Annie
PrivilegesRequired=lowest
DisableDirPage=yes
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
SetupIconFile=annie.ico
UninstallDisplayIcon={app}\annie.ico
UninstallDisplayName=Annie
OutputDir={#OutputDir}
OutputBaseFilename=Annie-Setup
Compression=lzma2/max
SolidCompression=yes

[Files]
Source: "{#DepsDir}\uv.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#DepsDir}\ffmpeg\*"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion recursesubdirs
Source: "Annie.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "annie.ico"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; Running the installer again doubles as "repair": stale FFmpeg DLLs and the Annie
; environment are wiped so the next start rebuilds them cleanly. The downloaded Python
; is kept, and user data is never touched.
Type: filesandordirs; Name: "{app}\ffmpeg"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\bin"

[Icons]
Name: "{autoprograms}\Annie"; Filename: "{app}\Annie.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\annie.ico"; Comment: "Start Annie"
Name: "{autodesktop}\Annie"; Filename: "{app}\Annie.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\annie.ico"; Comment: "Start Annie"

[Run]
Filename: "{app}\Annie.cmd"; Description: "Start Annie now"; Flags: postinstall nowait skipifsilent shellexec

[UninstallDelete]
; Inno only removes files it installed; this also removes what uv downloaded later.
Type: filesandordirs; Name: "{app}"

[Code]
// Annie holds files in the install folder open while it runs, so stop it before they are
// replaced (reinstall) or removed (uninstall). /T also ends its Python child process.
procedure StopAnnie();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM annie.exe', '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopAnnie();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopAnnie();
  Result := True;
end;

// Offer to delete the user's work too. Default is No, and silent uninstalls always keep it.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  DataDir := ExpandConstant('{%USERPROFILE}\Annie');
  if UninstallSilent() or not DirExists(DataDir) then
    Exit;
  if MsgBox('Also delete your Annie work?' + #13#10#13#10 +
      'Your reviews, saved configs and logs are in:' + #13#10 + DataDir + #13#10#13#10 +
      'Choose No to keep them, for example if you will reinstall Annie.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DelTree(DataDir, True, True, True);
end;
