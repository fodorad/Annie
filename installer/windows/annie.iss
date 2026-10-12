; Annie for Windows: per-user installer (Inno Setup 6) for PCs where only the home folder
; is writable: no admin rights, no registry, no Start menu.
;
; One page asks for the two locations (both editable, set once, kept on reinstall):
;   Install folder, default %USERPROFILE%\Annie\App: uv.exe, the bundled Python (python\),
;     the "annie" Python environment built on it (env\), FFmpeg (ffmpeg\), the launcher
;     (launcher.pyw), the icon and annie.ini, which records the chosen data folder.
;   Data folder, default %USERPROFILE%\Annie\Data: the user's reviews, configs and logs.
; plus an optional desktop shortcut (unticked by default) and the optional example
; datasets (First Impression V2 Mini and CMU-MOSEI Mini, about 50 MB, copied to
; <data>\examples).
;
; Annie is started with the "Annie" shortcut in the Annie folder: the folder holding both
; App and Data (default %USERPROFILE%\Annie; see GetRootDir). "Uninstall Annie" next to it
; removes Annie, since there is no entry in Settings > Apps.
;
; Setup downloads Annie and its dependencies into env\ (launcher.pyw --install), so the
; first start is instant; the launcher then keeps Annie up to date on every start.
;
; Footprint, on purpose minimal and fully removed by the uninstaller:
;   * the install folder, including everything downloaded into it later;
;   * the "Annie" and "Uninstall Annie" shortcuts in the Annie folder, and the optional
;     desktop shortcut (files, which keep their AppUserModelID inside the .lnk).
; Nothing else: no registry entry at all (CreateUninstallRegKey=no, no [Registry]), no
; Start menu, no PATH change, no file associations, no services, nothing in AppData.
; The data folder is kept unless the user chooses to delete it when uninstalling.
;
; Build (see .github/workflows/windows-installer.yml):
;   installer\windows\fetch-deps.ps1 -OutDir installer\windows\build
;   iscc /DAppVersion=1.2.3 installer\windows\annie.iss
;
; Command-line options (on top of Inno's /SILENT, /VERYSILENT, /DIR=...):
;   /DATADIR="D:\Annie work"   data folder
;   /DESKTOP                   also create a desktop shortcut (/NODESKTOP: never)
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
; Never change AppId: it names this install's uninstall log (unins000.dat), and installers
; up to 1.14 used it for their (now removed) Settings > Apps entry.
AppId={{E674FD3A-8545-44F6-875F-ED877FE764E3}
AppName=Annie
AppVersion={#AppVersion}
AppVerName=Annie
AppPublisher=fodorad
AppPublisherURL=https://github.com/fodorad/Annie
AppSupportURL=https://github.com/fodorad/Annie/issues
; The previous install folder, or %USERPROFILE%\Annie\App (see GetDefaultDir).
DefaultDirName={code:GetDefaultDir}
UsePreviousAppDir=no
AppendDefaultDirName=no
DirExistsWarning=no
PrivilegesRequired=lowest
DisableDirPage=no
DisableProgramGroupPage=yes
DisableWelcomePage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
SetupIconFile=annie.ico
; No registry: without this Inno would write HKCU\...\Uninstall\{AppId}_is1 for the
; Settings > Apps entry. Uninstalling is "Uninstall Annie" in the Annie folder instead.
CreateUninstallRegKey=no
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
Source: "annie.png"; DestDir: "{app}"; Flags: ignoreversion
; The example datasets go to the data folder, so they stay with the reviews made on them
; (uninstall asks about both). The tray downloads them later when this was unticked.
Source: "{#ExamplesDir}\config\*.json"; DestDir: "{code:GetDataDir}\examples\config"; Flags: ignoreversion uninsneveruninstall; Check: WantExamples
Source: "{#ExamplesDir}\example\*"; DestDir: "{code:GetDataDir}\examples\example"; Flags: ignoreversion recursesubdirs createallsubdirs uninsneveruninstall; Check: WantExamples

[INI]
; Written once here; the launcher, the tray and the uninstaller read the paths from it.
Filename: "{app}\annie.ini"; Section: "paths"; Key: "data"; String: "{code:GetDataDir}"
Filename: "{app}\annie.ini"; Section: "launcher"; Key: "version"; String: "3"

[InstallDelete]
; Running the installer again doubles as "repair": the bundled binaries and the Annie
; environment are replaced so they rebuild cleanly. User data is never touched. The rest
; removes what the first installer (Annie.cmd + uv-managed Python) left in this folder.
; Shortcuts of older installs are cleaned up in PrepareToInstall (CleanUpOlderInstall).
Type: filesandordirs; Name: "{app}\env"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\ffmpeg"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\bin"
Type: files; Name: "{app}\Annie.cmd"

[Icons]
; Annie starts with a double-click on "Annie" in the Annie folder (no Start menu); the
; uninstall shortcut replaces the Settings > Apps entry, which needs the registry.
Name: "{code:GetRootDir}\Annie"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\annie.ico"; Comment: "Start Annie"; AppUserModelID: "fodorad.Annie"
Name: "{code:GetRootDir}\Uninstall Annie"; Filename: "{uninstallexe}"; WorkingDir: "{app}"; IconFilename: "{app}\annie.ico"; Comment: "Remove Annie from this PC"
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
  FolderNote: TNewStaticText;
  UninstallDataDir: String;

const
  // The Settings > Apps entry that installers up to 1.14 created. This one creates none;
  // it only reads the old entry to find such an install, and removes it when upgrading it.
  OldUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{E674FD3A-8545-44F6-875F-ED877FE764E3}_is1';
  // Longest program folder path. Windows (long paths are off on most PCs) cannot open a
  // file whose full path exceeds 260 characters, and Annie's deepest files are 144
  // characters below the program folder (Python code 114, a PyTorch licence file 144).
  // 120 leaves room for deeper files in later package versions.
  MaxAppDirLength = 120;

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

// %USERPROFILE%\Annie\App, or where an installer up to 1.14 put Annie (except the first
// installer's hidden AppData folder, which is replaced). /DIR= still wins.
function GetDefaultDir(Param: String): String;
var
  Previous: String;
begin
  Result := ExpandConstant('{%USERPROFILE}\Annie\App');
  Previous := OldInstallDir();
  if (Previous <> '') and not IsInside(Previous, LegacyAppDir()) then
    Result := Previous;
end;

function GetDataDir(Param: String): String;
begin
  Result := Trim(DataEdit.Text);
end;

// The Annie folder, where the "Annie" and "Uninstall Annie" shortcuts go: the folder
// holding both the program and the data folder when they are side by side (default
// %USERPROFILE%\Annie), else the program folder itself. The tray's "Open Annie folder"
// uses the same rule (annie.core.runtime.annie_folder).
function GetRootDir(Param: String): String;
var
  AppParent: String;
begin
  AppParent := ExtractFileDir(RemoveBackslashUnlessRoot(ExpandConstant('{app}')));
  if CompareText(AppParent, ExtractFileDir(RemoveBackslashUnlessRoot(GetDataDir('')))) = 0 then
    Result := AppParent
  else
    Result := ExpandConstant('{app}');
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

// Unticked on a first install (the "Annie" shortcut in the Annie folder is always there);
// on a reinstall, ticked only if the desktop shortcut is there now.
function DefaultDesktopIcon(): Boolean;
begin
  if HasSwitch('DESKTOP') then
    Result := True
  else if HasSwitch('NODESKTOP') then
    Result := False
  else if FileExists(AddBackslash(WizardDirValue()) + 'annie.ini') then
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

  FolderNote := TNewStaticText.Create(WizardForm);
  FolderNote.Parent := Page.Surface;
  FolderNote.Top := ExamplesCheck.Top + ExamplesCheck.Height + ScaleY(10);
  FolderNote.Width := WizardForm.SelectDirLabel.Width;
  FolderNote.WordWrap := True;
  FolderNote.AutoSize := True;
  FolderNote.Caption :=
    'Start Annie by double-clicking "Annie" in the Annie folder (the folder holding both ' +
    'of the above); "Uninstall Annie" next to it removes Annie. Nothing is added to the ' +
    'registry or the Start menu. The examples can also be downloaded later from the ' +
    'Annie icon''s menu.';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Data: String;
begin
  Result := True;
  if CurPageID <> wpSelectDir then
    Exit;
  Data := GetDataDir('');
  if Length(RemoveBackslashUnlessRoot(WizardDirValue())) > MaxAppDirLength then
  begin
    MsgBox('Please choose a shorter path for the program folder: at most ' +
      IntToStr(MaxAppDirLength) + ' characters (this one has ' +
      IntToStr(Length(RemoveBackslashUnlessRoot(WizardDirValue()))) + ').' + #13#10#13#10 +
      'Annie''s components contain deeply nested files, and Windows cannot open a file ' +
      'whose full path is longer than 260 characters.', mbError, MB_OK);
    Result := False;
  end
  else if (Data = '') or (Pos(':', Data) = 0) and (Copy(Data, 1, 2) <> '\\') then
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
    'Shortcuts:' + NewLine +
    Space + 'Annie and Uninstall Annie in: ' + GetRootDir('') + NewLine +
    Space + 'Desktop: ' + Desktop + NewLine + NewLine +
    'Setup then downloads Annie and its components. This takes a few minutes.';
end;

// --- install ---

// Reinstalling over this folder: remove what older installs of *this* folder left
// outside it, so nothing outside the two folders remains. Only for this folder, so an
// install elsewhere (e.g. a test) never touches another Annie's shortcuts or entry.
procedure CleanUpOlderInstall();
var
  OldDir: String;
begin
  OldDir := OldInstallDir();
  // The old install is this folder, or the first installer's AppData folder, which
  // PrepareToInstall replaces by this install.
  if (OldDir <> '') and ((CompareText(RemoveBackslashUnlessRoot(OldDir),
      RemoveBackslashUnlessRoot(WizardDirValue())) = 0) or
      IsInside(OldDir, LegacyAppDir())) then
  begin
    // Installers up to 1.14: the Settings > Apps entry and the Start menu shortcut.
    RegDeleteKeyIncludingSubkeys(HKCU, OldUninstallKey);
    DeleteFile(ExpandConstant('{autoprograms}\Annie.lnk'));
  end;
  // A desktop shortcut the user now unticked.
  if FileExists(AddBackslash(WizardDirValue()) + 'annie.ini') and not WantDesktopIcon() then
    DeleteFile(ExpandConstant('{autodesktop}\Annie.lnk'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CleanUpOlderInstall();
  StopAnnieIn(WizardDirValue());
  // Repair: what [InstallDelete] replaces, removed here already because its long-path
  // files (deep inside env\) would otherwise survive and break the rebuild.
  DeleteTreeLong(AddBackslash(WizardDirValue()) + 'env');
  DeleteTreeLong(AddBackslash(WizardDirValue()) + 'python');
  DeleteTreeLong(AddBackslash(WizardDirValue()) + 'ffmpeg');
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
      DeleteTreeLong(UninstallDataDir);
  // [UninstallDelete] leaves files whose path is too long for it; nothing may remain.
  DeleteTreeLong(ExpandConstant('{app}'));
  RemoveEmptyAnnieParent(ExpandConstant('{app}'));
end;
