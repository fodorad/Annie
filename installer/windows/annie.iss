; Annie for Windows: per-user installer (Inno Setup 6). No admin rights needed.
;
; One page asks for the two locations (both editable, set once, kept on reinstall):
;   Install folder, default %USERPROFILE%\Annie\App: uv.exe, the bundled Python (python\),
;     the "annie" Python environment built on it (env\), FFmpeg (ffmpeg\), the launcher
;     (launcher.pyw), the icon and annie.ini, which records the chosen data folder.
;   Data folder, default %USERPROFILE%\Annie\Data: the user's reviews, configs and logs.
; plus an optional desktop shortcut and the optional example datasets (First Impression
; V2 Mini and CMU-MOSEI Mini, about 50 MB, copied to <data>\examples). Annie is always
; in the Start menu, so Start search finds it.
;
; Setup downloads Annie and its dependencies into env\ (launcher.pyw --install), so the
; first start is instant; the launcher then keeps Annie up to date on every start.
;
; Footprint, on purpose minimal and fully removed by the uninstaller:
;   * the install folder, including everything downloaded into it later;
;   * the Start-menu and (optional) desktop shortcuts (files, not registry);
;   * ONE registry key, HKCU\...\Uninstall\{AppId}_is1, which Inno writes so Annie
;     appears in Settings > Apps for a normal uninstall. No [Registry] section, no PATH
;     change, no file associations, no services, nothing in AppData or %TEMP%.
; The data folder is kept unless the user chooses to delete it when uninstalling.
;
; Build (see .github/workflows/windows-installer.yml):
;   installer\windows\fetch-deps.ps1 -OutDir installer\windows\build
;   iscc /DAppVersion=1.2.3 installer\windows\annie.iss
;
; Command-line options (on top of Inno's /SILENT, /VERYSILENT, /DIR=...):
;   /DATADIR="D:\Annie work"   data folder
;   /NODESKTOP                 no desktop shortcut
;   /NOEXAMPLES                no example datasets

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
; Never change AppId: it is how a newer installer finds and replaces this install.
AppId={{E674FD3A-8545-44F6-875F-ED877FE764E3}
AppName=Annie
AppVersion={#AppVersion}
; Annie updates itself, so a version in the Apps list would soon be stale.
AppVerName=Annie
AppPublisher=fodorad
AppPublisherURL=https://github.com/fodorad/Annie
AppSupportURL=https://github.com/fodorad/Annie/issues
; The previous install folder, or %USERPROFILE%\Annie\App (see GetDefaultDir).
DefaultDirName={code:GetDefaultDir}
UsePreviousAppDir=no
AppendDefaultDirName=no
DirExistsWarning=no
DefaultGroupName=Annie
PrivilegesRequired=lowest
DisableDirPage=no
DisableProgramGroupPage=yes
DisableWelcomePage=yes
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
Source: "{#DepsDir}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#DepsDir}\ffmpeg\*"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion recursesubdirs
Source: "launcher.pyw"; DestDir: "{app}"; Flags: ignoreversion
Source: "annie.ico"; DestDir: "{app}"; Flags: ignoreversion
; The example datasets go to the data folder, so they stay with the reviews made on them
; (uninstall asks about both). The tray downloads them later when this was unticked.
Source: "{#ExamplesDir}\config\*.json"; DestDir: "{code:GetDataDir}\examples\config"; Flags: ignoreversion uninsneveruninstall; Check: WantExamples
Source: "{#ExamplesDir}\example\*"; DestDir: "{code:GetDataDir}\examples\example"; Flags: ignoreversion recursesubdirs createallsubdirs uninsneveruninstall; Check: WantExamples

[INI]
; Written once here; the launcher, the tray and the uninstaller read the paths from it.
Filename: "{app}\annie.ini"; Section: "paths"; Key: "data"; String: "{code:GetDataDir}"
Filename: "{app}\annie.ini"; Section: "launcher"; Key: "version"; String: "2"

[InstallDelete]
; Running the installer again doubles as "repair": the bundled binaries and the Annie
; environment are replaced so they rebuild cleanly. User data is never touched. The rest
; removes what the first installer (Annie.cmd + uv-managed Python) left in this folder,
; and a desktop shortcut the user now unticked.
Type: filesandordirs; Name: "{app}\env"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\ffmpeg"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\bin"
Type: files; Name: "{app}\Annie.cmd"
Type: files; Name: "{autodesktop}\Annie.lnk"

[Icons]
Name: "{autoprograms}\Annie"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\annie.ico"; Comment: "Start Annie"; AppUserModelID: "fodorad.Annie"
Name: "{autodesktop}\Annie"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\annie.ico"; Comment: "Start Annie"; AppUserModelID: "fodorad.Annie"; Check: WantDesktopIcon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; Description: "Start Annie now"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Inno only removes files it installed; this also removes what was downloaded later.
Type: filesandordirs; Name: "{app}"

[Code]
var
  DataLabel: TNewStaticText;
  DataEdit: TNewEdit;
  DataBrowseButton: TNewButton;
  DesktopCheck: TNewCheckBox;
  ExamplesCheck: TNewCheckBox;
  StartMenuNote: TNewStaticText;
  UninstallDataDir: String;

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

// The value of a /NAME=value command-line option, or Default when it is not given.
function ParamValue(const Name, Default: String): String;
var
  I: Integer;
  Prefix: String;
begin
  Result := Default;
  Prefix := '/' + Uppercase(Name) + '=';
  for I := 1 to ParamCount do
    if Pos(Prefix, Uppercase(ParamStr(I))) = 1 then
      Result := RemoveQuotes(Copy(ParamStr(I), Length(Prefix) + 1, Length(ParamStr(I))));
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

// Reinstall: the folder Annie is installed in now. First install, or an upgrade from the
// first installer (hidden AppData folder): %USERPROFILE%\Annie\App. /DIR= still wins.
function GetDefaultDir(Param: String): String;
var
  Previous: String;
begin
  Result := ExpandConstant('{%USERPROFILE}\Annie\App');
  if RegQueryStringValue(HKCU,
      'Software\Microsoft\Windows\CurrentVersion\Uninstall\{E674FD3A-8545-44F6-875F-ED877FE764E3}_is1',
      'Inno Setup: App Path', Previous) and (Previous <> '') and
      not IsInside(Previous, LegacyAppDir()) then
    Result := Previous;
end;

function GetDataDir(Param: String): String;
begin
  Result := Trim(DataEdit.Text);
end;

function WantDesktopIcon(): Boolean;
begin
  Result := DesktopCheck.Checked;
end;

function WantExamples(): Boolean;
begin
  Result := ExamplesCheck.Checked;
end;

// --- stopping Annie ---

// Annie holds files in the install folder open while it runs, so stop it before they
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

// --- the Locations page (Inno's folder page, extended) ---

procedure DataBrowseClick(Sender: TObject);
var
  Dir: String;
begin
  Dir := Trim(DataEdit.Text);
  if BrowseForFolder('Select the folder for your Annie work:', Dir, True) then
    DataEdit.Text := Dir;
end;

function DefaultDataDir(): String;
var
  Previous: String;
begin
  // Reinstall: keep the folder chosen the first time.
  Previous := GetIniString('paths', 'data', '', AddBackslash(WizardDirValue()) + 'annie.ini');
  if Previous <> '' then
    Result := Previous
  else
    Result := ExpandConstant('{%USERPROFILE}\Annie\Data');
  Result := ParamValue('DATADIR', Result);
end;

// Ticked on a first install; on a reinstall, ticked only if the shortcut is there now.
function DefaultDesktopIcon(): Boolean;
begin
  if HasSwitch('NODESKTOP') then
    Result := False
  else if FileExists(AddBackslash(WizardDirValue()) + 'annie.ini') then
    Result := FileExists(ExpandConstant('{autodesktop}\Annie.lnk'))
  else
    Result := True;
end;

// Ticked on a first install; on a reinstall, ticked only if the examples are there now
// (unticking never deletes them: they sit in the data folder with your reviews).
function DefaultExamples(): Boolean;
begin
  if HasSwitch('NOEXAMPLES') then
    Result := False
  else if FileExists(AddBackslash(WizardDirValue()) + 'annie.ini') then
    Result := DirExists(AddBackslash(DefaultDataDir()) + 'examples\config')
  else
    Result := True;
end;

procedure InitializeWizard();
var
  Page: TWizardPage;
  Top: Integer;
begin
  Page := PageFromID(wpSelectDir);
  Page.Caption := 'Choose locations';
  Page.Description := 'Where should Annie and your work be kept?';
  WizardForm.SelectDirLabel.Caption :=
    'Annie program folder. It also holds Annie''s own Python, the "annie" Python ' +
    'environment with all dependencies, and FFmpeg. Uninstalling removes this folder ' +
    'completely.';
  WizardForm.SelectDirBrowseLabel.Visible := False;
  WizardForm.DirEdit.Top := WizardForm.SelectDirLabel.Top + WizardForm.SelectDirLabel.Height + ScaleY(8);
  WizardForm.DirBrowseButton.Top := WizardForm.DirEdit.Top - ScaleY(1);
  WizardForm.DiskSpaceLabel.Caption :=
    'Setup downloads a few hundred MB. Annie needs about 1 GB of disk space in total.';

  Top := WizardForm.DirEdit.Top + WizardForm.DirEdit.Height + ScaleY(20);
  DataLabel := TNewStaticText.Create(WizardForm);
  DataLabel.Parent := Page.Surface;
  DataLabel.Top := Top;
  DataLabel.Width := WizardForm.SelectDirLabel.Width;
  DataLabel.WordWrap := True;
  DataLabel.AutoSize := True;
  DataLabel.Caption :=
    'Data folder for your work: reviews, saved configs and logs. It is kept when you ' +
    'uninstall, unless you choose to delete it.';

  DataEdit := TNewEdit.Create(WizardForm);
  DataEdit.Parent := Page.Surface;
  DataEdit.Top := DataLabel.Top + DataLabel.Height + ScaleY(8);
  DataEdit.Left := WizardForm.DirEdit.Left;
  DataEdit.Width := WizardForm.DirEdit.Width;
  DataEdit.Text := DefaultDataDir();

  DataBrowseButton := TNewButton.Create(WizardForm);
  DataBrowseButton.Parent := Page.Surface;
  DataBrowseButton.Top := DataEdit.Top - ScaleY(1);
  DataBrowseButton.Left := WizardForm.DirBrowseButton.Left;
  DataBrowseButton.Width := WizardForm.DirBrowseButton.Width;
  DataBrowseButton.Height := WizardForm.DirBrowseButton.Height;
  DataBrowseButton.Caption := WizardForm.DirBrowseButton.Caption;
  DataBrowseButton.OnClick := @DataBrowseClick;

  DesktopCheck := TNewCheckBox.Create(WizardForm);
  DesktopCheck.Parent := Page.Surface;
  DesktopCheck.Top := DataEdit.Top + DataEdit.Height + ScaleY(20);
  DesktopCheck.Width := WizardForm.SelectDirLabel.Width;
  DesktopCheck.Caption := 'Create a desktop shortcut';
  DesktopCheck.Checked := DefaultDesktopIcon();

  ExamplesCheck := TNewCheckBox.Create(WizardForm);
  ExamplesCheck.Parent := Page.Surface;
  ExamplesCheck.Top := DesktopCheck.Top + DesktopCheck.Height + ScaleY(6);
  ExamplesCheck.Width := WizardForm.SelectDirLabel.Width;
  ExamplesCheck.Caption :=
    'Install the example datasets (First Impression V2 Mini, CMU-MOSEI Mini; 50 MB)';
  ExamplesCheck.Checked := DefaultExamples();

  StartMenuNote := TNewStaticText.Create(WizardForm);
  StartMenuNote.Parent := Page.Surface;
  StartMenuNote.Top := ExamplesCheck.Top + ExamplesCheck.Height + ScaleY(10);
  StartMenuNote.Width := WizardForm.SelectDirLabel.Width;
  StartMenuNote.WordWrap := True;
  StartMenuNote.AutoSize := True;
  StartMenuNote.Caption :=
    'Annie is always added to the Start menu, so you can also find it by typing "Annie" ' +
    'in Windows search. The examples can also be downloaded later from the Annie icon''s ' +
    'menu.';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Data: String;
begin
  Result := True;
  if CurPageID <> wpSelectDir then
    Exit;
  Data := GetDataDir('');
  if (Data = '') or (Pos(':', Data) = 0) and (Copy(Data, 1, 2) <> '\\') then
  begin
    MsgBox('Please choose a full path for the data folder, for example ' +
      ExpandConstant('{%USERPROFILE}\Annie\Data') + '.', mbError, MB_OK);
    Result := False;
  end
  else if IsInside(Data, WizardDirValue()) then
  begin
    MsgBox('The data folder must not be inside the program folder: uninstalling ' +
      'removes the program folder completely.', mbError, MB_OK);
    Result := False;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  Desktop, Examples: String;
begin
  if WantDesktopIcon() then
    Desktop := 'yes'
  else
    Desktop := 'no';
  if WantExamples() then
    Examples := AddBackslash(GetDataDir('')) + 'examples'
  else
    Examples := 'no (download them later from the Annie icon''s menu)';
  Result := MemoDirInfo + NewLine + NewLine +
    'Data folder (your work):' + NewLine + Space + GetDataDir('') + NewLine + NewLine +
    'Example datasets:' + NewLine + Space + Examples + NewLine + NewLine +
    'Shortcuts:' + NewLine + Space + 'Start menu' + NewLine +
    Space + 'Desktop: ' + Desktop + NewLine + NewLine +
    'Setup then downloads Annie and its components. This takes a few minutes.';
end;

// --- install ---

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopAnnieIn(WizardDirValue());
  // Upgrading from the first installer: its folder only held the program (user data
  // was always elsewhere), so it can go; the new install replaces its shortcuts and
  // its Apps entry, which share this AppId.
  if not IsInside(WizardDirValue(), LegacyAppDir()) and DirExists(LegacyAppDir()) then
  begin
    StopAnnieIn(LegacyAppDir());
    DelTree(LegacyAppDir(), True, True, True);
  end;
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Args: String;
  ResultCode: Integer;
begin
  if CurStep <> ssPostInstall then
    Exit;
  WizardForm.StatusLabel.Caption := 'Downloading Annie and its components...';
  WizardForm.FilenameLabel.Caption := 'This takes a few minutes. A small window shows the progress.';
  WizardForm.ProgressGauge.Style := npbstMarquee;
  Args := '"' + ExpandConstant('{app}\launcher.pyw') + '" --install';
  if WizardSilent() then
    Args := Args + ' --quiet';
  if not Exec(ExpandConstant('{app}\python\pythonw.exe'), Args, ExpandConstant('{app}'),
      SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
    if not WizardSilent() then
      MsgBox('Annie''s components could not be downloaded right now.' + #13#10#13#10 +
        'Setup still finishes: Annie downloads them the first time you start it. ' +
        'Details are in:' + #13#10 + ExpandConstant('{app}\install.log'),
        mbInformation, MB_OK);
  WizardForm.ProgressGauge.Style := npbstNormal;
end;

// --- uninstall ---

function InitializeUninstall(): Boolean;
begin
  // Read before the install folder (and annie.ini with it) is deleted.
  UninstallDataDir := GetIniString('paths', 'data', '', ExpandConstant('{app}\annie.ini'));
  StopAnnieIn(ExpandConstant('{app}'));
  Result := True;
end;

// Remove an emptied "Annie" parent folder, so nothing is left behind.
procedure RemoveEmptyAnnieParent(const Dir: String);
var
  Parent: String;
begin
  Parent := ExtractFileDir(RemoveBackslashUnlessRoot(Dir));
  if CompareText(ExtractFileName(Parent), 'Annie') = 0 then
    RemoveDir(Parent);
end;

// Offer to delete the user's work too. Default is No, and silent uninstalls keep it.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  if (UninstallDataDir <> '') and DirExists(UninstallDataDir) and not UninstallSilent() then
    if MsgBox('Also delete your Annie work?' + #13#10#13#10 +
        'Your reviews, saved configs, logs and the example datasets are in:' + #13#10 + UninstallDataDir + #13#10#13#10 +
        'Choose No to keep them, for example if you will reinstall Annie.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(UninstallDataDir, True, True, True);
  RemoveEmptyAnnieParent(ExpandConstant('{app}'));
end;
