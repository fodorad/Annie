; Annie for Windows: per-user installer (Inno Setup 6) for PCs where only the home folder
; is writable: no admin rights, no registry, no Start menu.
;
; Pages: Welcome -> install folder (with two options) -> Installing -> Finish.
;
; One install folder, default %USERPROFILE%\Annie, holds everything:
;   App\   the program: uv.exe, the bundled Python (python\), the "annie" environment
;          built on it (env\), FFmpeg (ffmpeg\), the launcher, annie.ini, the uninstaller;
;   Data\  the user's work: reviews, configs, logs, the optional example datasets;
;   Annie.lnk and "Uninstall Annie.lnk", plus an optional desktop shortcut.
; annie.ini records the data folder; an older install whose data lives elsewhere keeps it.
;
; Installing happens in this one window: the files are copied, then Annie and its packages
; are downloaded into App\env by "launcher.pyw --install --progress", whose output drives
; the progress bar, the status line and a log box (ExecAndLogOutput).
;
; Footprint, on purpose minimal and fully removed by the uninstaller: the install folder's
; App\ and shortcuts, and the optional desktop shortcut. No registry entry at all
; (CreateUninstallRegKey=no), no Start menu, no PATH change, nothing in AppData. Data\ is
; kept unless the user chooses to delete it when uninstalling.
;
; Build (see .github/workflows/windows-installer.yml):
;   installer\windows\fetch-deps.ps1 -OutDir installer\windows\build
;   iscc /DAppVersion=1.2.3 installer\windows\annie.iss
; The wizard images come from make-wizard-images.py.
;
; Command-line options (on top of Inno's /SILENT, /VERYSILENT, /DIR=...):
;   /DESKTOP      also create a desktop shortcut (/NODESKTOP: never)
;   /NOEXAMPLES   no example datasets

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef DepsDir
  #define DepsDir "build"
#endif
#ifndef ExamplesDir
  #define ExamplesDir "..\..\data"
#endif
#ifndef OutputDir
  #define OutputDir "Output"
#endif

[Setup]
; Never change AppId: it names this install's uninstall log (unins000.dat), and installers
; up to 1.14 used it for their (now removed) Settings > Apps entry.
AppId={{E674FD3A-8545-44F6-875F-ED877FE764E3}
AppName=Annie
AppVersion={#AppVersion}
AppVerName=Annie
AppPublisher=fodorad
AppPublisherURL=https://github.com/fodorad/Annie
AppSupportURL=https://github.com/fodorad/Annie/issues
DefaultDirName={code:GetDefaultDir}
UsePreviousAppDir=no
; Browsing to D:\Tools installs into D:\Tools\Annie.
AppendDefaultDirName=yes
DirExistsWarning=no
PrivilegesRequired=lowest
DisableWelcomePage=no
DisableDirPage=no
DisableProgramGroupPage=yes
DisableReadyPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
WizardImageFile=wizard.bmp,wizard-2x.bmp
WizardSmallImageFile=wizard-small.bmp,wizard-small-2x.bmp
SetupIconFile=annie.ico
; The uninstaller lives with the program, not in the install folder's top level.
UninstallFilesDir={app}\App
; No registry: without this Inno would write HKCU\...\Uninstall\{AppId}_is1 for the
; Settings > Apps entry. Uninstalling is "Uninstall Annie" in the install folder instead.
CreateUninstallRegKey=no
OutputDir={#OutputDir}
OutputBaseFilename=Annie-Setup
Compression=lzma2/max
SolidCompression=yes

[Messages]
WelcomeLabel1=Welcome to Annie
WelcomeLabel2=Annie helps you explore, check and annotate video datasets in your browser.%n%nIt is installed for your user account only. No administrator rights are needed.
SelectDirDesc=Where should Annie be installed?
SelectDirLabel3=Annie keeps the program in App and your work in Data, inside this folder.
SelectDirBrowseLabel=Click Install to continue.
WizardInstalling=Installing
InstallingLabel=Annie is being installed. This takes a few minutes.
FinishedHeadingLabel=Annie is installed
FinishedLabel=Start Annie any time with the Annie shortcut in your install folder.
ClickFinish=Click Finish to close Setup.

[Files]
Source: "{#DepsDir}\uv.exe"; DestDir: "{app}\App"; Flags: ignoreversion
Source: "{#DepsDir}\python\*"; DestDir: "{app}\App\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#DepsDir}\ffmpeg\*"; DestDir: "{app}\App\ffmpeg"; Flags: ignoreversion recursesubdirs
Source: "launcher.pyw"; DestDir: "{app}\App"; Flags: ignoreversion
Source: "annie.ico"; DestDir: "{app}\App"; Flags: ignoreversion
Source: "annie.png"; DestDir: "{app}\App"; Flags: ignoreversion
; The example datasets go to the data folder, so they stay with the reviews made on them
; (uninstall asks about both). The tray downloads them later when this was unticked.
Source: "{#ExamplesDir}\config\*.json"; DestDir: "{code:GetDataDir}\examples\config"; Flags: ignoreversion uninsneveruninstall; Check: WantExamples
Source: "{#ExamplesDir}\example\*"; DestDir: "{code:GetDataDir}\examples\example"; Flags: ignoreversion recursesubdirs createallsubdirs uninsneveruninstall; Check: WantExamples

[INI]
; Written once here; the launcher, the tray and the uninstaller read the paths from it.
Filename: "{app}\App\annie.ini"; Section: "paths"; Key: "data"; String: "{code:GetDataDir}"
Filename: "{app}\App\annie.ini"; Section: "launcher"; Key: "version"; String: "3"

[InstallDelete]
; Running the installer again doubles as "repair": the bundled binaries and the Annie
; environment are replaced so they rebuild cleanly (PrepareToInstall already removed them
; with long paths in mind). The rest is what the first installer left. User data is never
; touched.
Type: filesandordirs; Name: "{app}\App\env"
Type: filesandordirs; Name: "{app}\App\python"
Type: filesandordirs; Name: "{app}\App\ffmpeg"
Type: filesandordirs; Name: "{app}\App\tools"
Type: filesandordirs; Name: "{app}\App\bin"
Type: files; Name: "{app}\App\Annie.cmd"

[Icons]
Name: "{app}\Annie"; Filename: "{app}\App\python\pythonw.exe"; Parameters: """{app}\App\launcher.pyw"""; WorkingDir: "{app}\App"; IconFilename: "{app}\App\annie.ico"; Comment: "Start Annie"; AppUserModelID: "fodorad.Annie"
Name: "{app}\Uninstall Annie"; Filename: "{uninstallexe}"; WorkingDir: "{app}\App"; IconFilename: "{app}\App\annie.ico"; Comment: "Remove Annie from this PC"
Name: "{autodesktop}\Annie"; Filename: "{app}\App\python\pythonw.exe"; Parameters: """{app}\App\launcher.pyw"""; WorkingDir: "{app}\App"; IconFilename: "{app}\App\annie.ico"; Comment: "Start Annie"; AppUserModelID: "fodorad.Annie"; Check: WantDesktopIcon

[Run]
Filename: "{app}\App\python\pythonw.exe"; Parameters: """{app}\App\launcher.pyw"""; WorkingDir: "{app}\App"; Description: "Start Annie"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Inno only removes files it installed; this also removes what was downloaded later.
Type: filesandordirs; Name: "{app}\App"

[Code]
var
  DesktopCheck: TNewCheckBox;
  ExamplesCheck: TNewCheckBox;
  LogMemo: TNewMemo;
  ComponentsFailed: Boolean;
  UninstallDataDir: String;

const
  // The Settings > Apps entry that installers up to 1.14 created. This one creates none;
  // it only reads the old entry to find such an install, and removes it when upgrading it.
  OldUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{E674FD3A-8545-44F6-875F-ED877FE764E3}_is1';
  MaxLogLines = 400;
  WM_VSCROLL = $0115;
  SB_BOTTOM = 7;

// Where the first installer put Annie; replaced by this one on upgrade.
function LegacyAppDir(): String;
begin
  Result := ExpandConstant('{localappdata}\Programs\Annie');
end;

function WithSlash(const Path: String): String;
begin
  Result := Lowercase(AddBackslash(Path));
end;

// True when Path is Parent itself or anything inside it.
function IsInside(const Path, Parent: String): Boolean;
begin
  Result := Pos(WithSlash(Parent), WithSlash(Path)) = 1;
end;

function SameDir(const A, B: String): Boolean;
begin
  Result := CompareText(RemoveBackslashUnlessRoot(A), RemoveBackslashUnlessRoot(B)) = 0;
end;

// Whether the bare /NAME switch was given on the command line.
function HasSwitch(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/' + Name) = 0 then
      Result := True;
end;

// The program folder inside the chosen install folder.
function AppDir(): String;
begin
  Result := AddBackslash(WizardDirValue()) + 'App';
end;

// The folder of an install made by an installer up to 1.14 (from its Apps entry, read
// only), or '' when there is none.
function OldInstallDir(): String;
var
  Path: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, OldUninstallKey, 'Inno Setup: App Path', Path) then
    Result := Path;
end;

// %USERPROFILE%\Annie, or the install folder of an Annie that an installer up to 1.14
// put elsewhere (its App folder's parent). /DIR= still wins.
function GetDefaultDir(Param: String): String;
var
  Previous: String;
begin
  Result := ExpandConstant('{%USERPROFILE}\Annie');
  Previous := RemoveBackslashUnlessRoot(OldInstallDir());
  if (Previous <> '') and not IsInside(Previous, LegacyAppDir()) then
    if CompareText(ExtractFileName(Previous), 'App') = 0 then
      Result := ExtractFileDir(Previous)
    else
      Result := Previous;
end;

// <install folder>\Data, or the data folder an existing install already uses.
function GetDataDir(Param: String): String;
begin
  Result := GetIniString('paths', 'data', '', AppDir() + '\annie.ini');
  if Result = '' then
    Result := AddBackslash(WizardDirValue()) + 'Data';
end;

function WantDesktopIcon(): Boolean;
begin
  Result := DesktopCheck.Checked;
end;

function WantExamples(): Boolean;
begin
  Result := ExamplesCheck.Checked;
end;

// Delete Dir with everything in it, also files whose full path is longer than the classic
// 260 characters, which Inno's DelTree cannot remove (Windows long paths are off on most
// PCs). cmd's rd accepts the \\?\ form, which lifts that limit.
procedure DeleteTreeLong(const Dir: String);
var
  Target: String;
  ResultCode: Integer;
begin
  if not DirExists(Dir) then
    Exit;
  Target := RemoveBackslashUnlessRoot(Dir);
  if Copy(Target, 1, 2) = '\\' then
    Target := '\\?\UNC\' + Copy(Target, 3, Length(Target))
  else
    Target := '\\?\' + Target;
  Exec(ExpandConstant('{cmd}'), '/C rd /S /Q "' + Target + '"', '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode);
end;

// --- stopping Annie ---

// Annie holds files in the program folder open while it runs, so stop it before they
// are replaced (reinstall) or removed (uninstall). Only Annie's own processes are
// stopped: those started from Dir, or running a launcher inside it (the first
// installer's Annie.cmd ran in cmd.exe).
procedure StopAnnieIn(const Dir: String);
var
  Quoted, Script: String;
  ResultCode: Integer;
begin
  if not DirExists(Dir) then
    Exit;
  // A PowerShell single-quoted string: double any ' in the path.
  Quoted := AddBackslash(Dir);
  StringChangeEx(Quoted, '''', '''''', True);
  Script :=
    '$d = ''' + Quoted + ''';' +
    '$names = ''python.exe'',''pythonw.exe'',''annie.exe'',''uv.exe'',''cmd.exe'',''ffmpeg.exe'',''ffprobe.exe'';' +
    'Get-CimInstance Win32_Process | Where-Object { ($names -contains $_.Name) -and ' +
    '(($_.ExecutablePath -and $_.ExecutablePath.StartsWith($d, ''OrdinalIgnoreCase'')) -or ' +
    '($_.CommandLine -and $_.CommandLine.IndexOf($d, [StringComparison]::OrdinalIgnoreCase) -ge 0)) } | ' +
    'ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }';
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' + Script + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// --- pages ---

// Unticked on a first install (the "Annie" shortcut in the install folder is always
// there); on a reinstall, ticked only if the desktop shortcut is there now.
function DefaultDesktopIcon(): Boolean;
begin
  if HasSwitch('DESKTOP') then
    Result := True
  else if HasSwitch('NODESKTOP') then
    Result := False
  else if FileExists(AppDir() + '\annie.ini') then
    Result := FileExists(ExpandConstant('{autodesktop}\Annie.lnk'))
  else
    Result := False;
end;

// Ticked on a first install; on a reinstall, ticked only if the examples are there now
// (unticking never deletes them: they sit in the data folder with your reviews).
function DefaultExamples(): Boolean;
begin
  if HasSwitch('NOEXAMPLES') then
    Result := False
  else if FileExists(AppDir() + '\annie.ini') then
    Result := DirExists(AddBackslash(GetDataDir('')) + 'examples\config')
  else
    Result := True;
end;

procedure InitializeWizard();
var
  Page: TWizardPage;
begin
  // Install folder page: the path, then the two options.
  Page := PageFromID(wpSelectDir);
  WizardForm.DiskSpaceLabel.Caption := 'Annie needs about 1 GB of free disk space.';

  DesktopCheck := TNewCheckBox.Create(WizardForm);
  DesktopCheck.Parent := Page.Surface;
  DesktopCheck.Left := WizardForm.DirEdit.Left;
  DesktopCheck.Top := WizardForm.DirEdit.Top + WizardForm.DirEdit.Height + ScaleY(18);
  DesktopCheck.Width := WizardForm.DirEdit.Width;
  DesktopCheck.Caption := 'Create a desktop shortcut';
  DesktopCheck.Checked := DefaultDesktopIcon();

  ExamplesCheck := TNewCheckBox.Create(WizardForm);
  ExamplesCheck.Parent := Page.Surface;
  ExamplesCheck.Left := WizardForm.DirEdit.Left;
  ExamplesCheck.Top := DesktopCheck.Top + DesktopCheck.Height + ScaleY(6);
  ExamplesCheck.Width := WizardForm.DirEdit.Width;
  ExamplesCheck.Caption := 'Install example datasets (50 MB)';
  ExamplesCheck.Checked := DefaultExamples();

  // Installing page: a log box under the progress bar, for the component download.
  LogMemo := TNewMemo.Create(WizardForm);
  LogMemo.Parent := WizardForm.InstallingPage;
  LogMemo.Left := WizardForm.ProgressGauge.Left;
  LogMemo.Top := WizardForm.ProgressGauge.Top + WizardForm.ProgressGauge.Height + ScaleY(14);
  LogMemo.Width := WizardForm.ProgressGauge.Width;
  LogMemo.Height := WizardForm.InstallingPage.ClientHeight - LogMemo.Top - ScaleY(4);
  LogMemo.ReadOnly := True;
  LogMemo.ScrollBars := ssVertical;
  LogMemo.WordWrap := False;
  LogMemo.Font.Name := 'Consolas';
  LogMemo.Font.Size := 8;
  LogMemo.Visible := False;
end;

// --- install ---

// Reinstalling: remove what older installs of *this* folder left outside it, so nothing
// outside the install folder remains. Only for this folder, so an install elsewhere
// (e.g. a test) never touches another Annie's shortcuts or entry.
procedure CleanUpOlderInstall();
var
  OldDir: String;
begin
  OldDir := OldInstallDir();
  // The old install is this program folder (or, for a custom layout, this folder), or
  // the first installer's AppData folder, which PrepareToInstall replaces.
  if (OldDir <> '') and (SameDir(OldDir, AppDir()) or SameDir(OldDir, WizardDirValue()) or
      IsInside(OldDir, LegacyAppDir())) then
  begin
    // Installers up to 1.14: the Settings > Apps entry and the Start menu shortcut.
    RegDeleteKeyIncludingSubkeys(HKCU, OldUninstallKey);
    DeleteFile(ExpandConstant('{autoprograms}\Annie.lnk'));
  end;
  // A desktop shortcut the user now unticked.
  if FileExists(AppDir() + '\annie.ini') and not WantDesktopIcon() then
    DeleteFile(ExpandConstant('{autodesktop}\Annie.lnk'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CleanUpOlderInstall();
  StopAnnieIn(AppDir());
  // Repair: what [InstallDelete] replaces, removed here already because its long-path
  // files (deep inside env\) would otherwise survive and break the rebuild.
  DeleteTreeLong(AppDir() + '\env');
  DeleteTreeLong(AppDir() + '\python');
  DeleteTreeLong(AppDir() + '\ffmpeg');
  // Upgrading from the first installer: its folder only held the program (user data
  // was always elsewhere), so it can go; CleanUpOlderInstall removed its shortcut and
  // its Apps entry.
  if not IsInside(WizardDirValue(), LegacyAppDir()) and DirExists(LegacyAppDir()) then
  begin
    StopAnnieIn(LegacyAppDir());
    DeleteTreeLong(LegacyAppDir());
  end;
  Result := '';
end;

procedure AddLogLine(const Line: String);
begin
  LogMemo.Lines.Add(Line);
  if LogMemo.Lines.Count > MaxLogLines then
    LogMemo.Lines.Delete(0);
  SendMessage(LogMemo.Handle, WM_VSCROLL, SB_BOTTOM, 0);
end;

// One line of "launcher.pyw --install --progress": @status sets the status line,
// @progress (0..1000) the bar, and every other line goes to the log box.
procedure OnLauncherLine(const S: String; const Error, FirstLine: Boolean);
var
  Value: Integer;
begin
  Log('launcher: ' + S);
  if Copy(S, 1, 10) = '@progress ' then
  begin
    Value := StrToIntDef(Trim(Copy(S, 11, Length(S))), -1);
    if (Value >= 0) and (Value <= 1000) then
      WizardForm.ProgressGauge.Position := Value;
  end
  else if Copy(S, 1, 8) = '@status ' then
    WizardForm.StatusLabel.Caption := Copy(S, 9, Length(S))
  else if Trim(S) <> '' then
    AddLogLine(S);
end;

// After the files are copied: download Annie and its packages into App\env, in this
// window (the progress bar restarts for this second, longer step).
procedure DownloadComponents();
var
  ResultCode: Integer;
  Ok: Boolean;
begin
  WizardForm.StatusLabel.Caption := 'Downloading Annie''s components...';
  WizardForm.FilenameLabel.Caption := '';
  WizardForm.ProgressGauge.Style := npbstNormal;
  WizardForm.ProgressGauge.Min := 0;
  WizardForm.ProgressGauge.Max := 1000;
  WizardForm.ProgressGauge.Position := 0;
  LogMemo.Lines.Clear;
  LogMemo.Visible := True;
  try
    Ok := ExecAndLogOutput(ExpandConstant('{app}\App\python\python.exe'),
      '-u "' + ExpandConstant('{app}\App\launcher.pyw') + '" --install --progress',
      ExpandConstant('{app}\App'), SW_HIDE, ewWaitUntilTerminated, ResultCode,
      @OnLauncherLine);
  except
    Log(GetExceptionMessage);
    Ok := False;
  end;
  ComponentsFailed := not Ok or (ResultCode <> 0);
  if ComponentsFailed then
    WizardForm.StatusLabel.Caption :=
      'Annie''s components could not be downloaded. Annie will finish this at its first start.'
  else
  begin
    WizardForm.ProgressGauge.Position := 1000;
    WizardForm.StatusLabel.Caption := 'Annie and its components are installed.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    DownloadComponents();
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and ComponentsFailed then
    WizardForm.FinishedLabel.Caption :=
      'Annie is installed, but its components could not be downloaded right now. ' +
      'Annie will download them the first time you start it.';
end;

// --- uninstall ---

function InitializeUninstall(): Boolean;
begin
  // Read before the program folder (and annie.ini with it) is deleted.
  UninstallDataDir := GetIniString('paths', 'data', '', ExpandConstant('{app}\App\annie.ini'));
  StopAnnieIn(ExpandConstant('{app}\App'));
  Result := True;
end;

// Offer to delete the user's work too. Default is No, and silent uninstalls keep it.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  if (UninstallDataDir <> '') and DirExists(UninstallDataDir) and not UninstallSilent() then
    if MsgBox('Also delete your Annie work?' + #13#10#13#10 +
        'Your reviews, saved configs, logs and the example datasets are in:' + #13#10 +
        UninstallDataDir + #13#10#13#10 +
        'Choose No to keep them, for example if you will reinstall Annie.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DeleteTreeLong(UninstallDataDir);
  // [UninstallDelete] leaves files whose path is too long for it; nothing may remain.
  DeleteTreeLong(ExpandConstant('{app}\App'));
  // The install folder itself goes once it is empty (it keeps Data otherwise).
  RemoveDir(ExpandConstant('{app}'));
end;
