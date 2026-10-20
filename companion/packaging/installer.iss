; CC 桌面端安装包(Inno Setup;原 Pearnly 小助手,2026-10-10 并入 CC 桌面端)· 把 64 位主程序文件夹(companion.exe + _internal)+ 预编译 cdx_reindex_runner.exe
; 装到当前用户的程序目录(%LOCALAPPDATA%\Programs\CC),建快捷方式,装完启动(首次弹配对窗)。没有代码签名(用户 2026-10-08 定不买证书)。
; 按用户安装(2026-10-08):不要管理员权限,自动更新静默覆盖安装时不弹「用户账户控制」授权窗口
; (原来装在 Program Files,每次自动更新都弹授权,打断正在用电脑的人)。配置、配对在 %APPDATA% 里,不受影响。
; 编译:ISCC.exe packaging\installer.iss → dist\CC-Setup.exe（2026-10-12 起；旧名 PearnlyCompanion-Setup.exe 由下载站同时保留）
; 2026-10-20(教程问题第 6、7、8 条):目录从「Pearnly Companion」改成「CC」并迁移老用户;界面跟系统语言(泰文、简体中文,
; 其余英文);卸载时清掉开机自启项与 %LOCALAPPDATA%\Pearnly 空目录。
; 同日用户定:照主流桌面产品一键安装——双击即装、不出任何向导页,装的时候只有一个小窗口(CC 图标居中 + 进度条 + 取消),
; 不显示文件路径;装完直接打开 CC。卸载同样精简(确认 + 小窗口进度)。自动更新的 /VERYSILENT 行为不变。
; 记录见 docs/work/2026-10-20-cc-installer-fixes.md。
#define AppName "CC"
; 版本号单一真相源 = src/companion/version.py。release.ps1 用 /DAppVersion=<ver> 注入;
; 直接 ISCC 编译时回落下方默认(手动保持与 version.py 一致)。
#ifndef AppVersion
  #define AppVersion "1.1.19"
#endif
; 泰文是官方语言(6.7.1 起):低于 6.7.1 的编译器不编,免得打出缺语言的包
#if Ver < EncodeVer(6,7,1)
  #error Inno Setup 6.7.1 or later is required
#endif

[Setup]
AppId={{8F3C2A91-4E7B-4D2A-9C11-PEARNLYCOMP01}
AppName={#AppName}
AppVersion={#AppVersion}
; 窗口标题只写产品名(SetupWindowTitle=%1,%1 即 AppVerName),不写「安装 - CC 版本 x」
AppVerName={#AppName}
AppPublisher=Pearnly
PrivilegesRequired=lowest
; 非管理员模式下 {autopf} = 当前用户的 %LOCALAPPDATA%\Programs。
; 2026-10-20 起目录叫 CC;以前叫「Pearnly Companion」,老用户升级时由 [Code] 迁过来
; (开机自启、任务栏固定的快捷方式改指新位置,旧目录删掉)。自动更新不带 /DIR,照样装到这里。
DefaultDirName={autopf}\CC
; 不沿用上一次的目录:更早的全机安装在 Program Files(当前用户写不进去),2026-10-20 前的按用户安装在「Pearnly Companion」
UsePreviousAppDir=no
; 一键安装:欢迎、选目录、开始菜单、准备安装、完成页都不出(选附加任务页在 [Code] ShouldSkipPage 里跳过),
; 所有页都跳过时 Setup 直接开始安装
DisableWelcomePage=yes
DisableDirPage=yes
DefaultGroupName=CC
UsePreviousGroup=no
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
OutputDir=..\dist
OutputBaseFilename=CC-Setup
VersionInfoProductName=CC
VersionInfoDescription=CC Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
; 语言跟 Windows 显示语言:泰文、简体中文;其余(含没认出来)用第一个,英文。不弹选语言的对话框。
; 一键安装后用户看得到的字只有:窗口标题「CC」、取消按钮、取消确认与出错提示、卸载确认,都是 Inno 译文自带的。
ShowLanguageDialog=no
LanguageDetectionMethod=uilanguage
UninstallDisplayName=CC
SetupIconFile=..\assets\cc.ico
UninstallDisplayIcon={app}\companion.exe
; 覆盖安装:旧版常驻托盘会锁住 companion.exe → 装前强制关掉,否则文件替换失败。
CloseApplications=force
RestartApplications=no

[Languages]
; 泰文:Inno Setup 6.7.1 官方译文;简体中文:官方仓库里的非官方译文(jrsoftware.org/files/istrans 指向同一份)。
; 两份都固定在 jrsoftware/issrc 标签 is-6_7_1(提交 cfdf48923178df4b4f040e038b423aa555a61ffc)原样拷进来,不改;
; sha256 由 tests/test_installer_contract.py 守住,升级时整份换并更新那里的值。
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "thai"; MessagesFile: "languages\Thai.isl"
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"

[Messages]
SetupWindowTitle=%1

[InstallDelete]
; 上一版的程序文件夹整个换掉(不留旧版本的库);旧名字的快捷方式删掉,换成 CC
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{autoprograms}\Pearnly Companion\Pearnly Companion.lnk"
Type: files; Name: "{autoprograms}\Pearnly Companion\Uninstall Pearnly Companion.lnk"
Type: dirifempty; Name: "{autoprograms}\Pearnly Companion"
Type: files; Name: "{autodesktop}\Pearnly Companion.lnk"
; 测试版 1.4.0 曾用名 Tomo
Type: filesandordirs; Name: "{autoprograms}\Tomo"
Type: files; Name: "{autodesktop}\Tomo.lnk"
Type: files; Name: "{app}\tomo.ico"

[Files]
; cc.png 放第一个:安装窗口一打开就要用它(ExtractTemporaryFile),整包是固实压缩,排在后面要先解压前面一百多 MB。
; 装进程序目录是给卸载窗口用的。
Source: "..\assets\cc.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\cc.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\companion\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Harbour DBFCDX 后台索引重建器(预编译原生 exe · 与主 exe 同目录 · cdx_reindex.py 定位)
Source: "..\dist\cdx_reindex_runner.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; AppUserModelID 必须与进程 SetCurrentProcessExplicitAppUserModelID 一致,否则 Win10/11
; 托盘 Toast(录入完成气泡)被当无主通知丢弃。值见 src/companion/main.py APP_USER_MODEL_ID。
Name: "{group}\CC"; Filename: "{app}\companion.exe"; IconFilename: "{app}\cc.ico"; AppUserModelID: "Pearnly.Companion"
Name: "{group}\Uninstall CC"; Filename: "{uninstallexe}"
Name: "{autodesktop}\CC"; Filename: "{app}\companion.exe"; IconFilename: "{app}\cc.ico"; AppUserModelID: "Pearnly.Companion"; Tasks: desktopicon

[Tasks]
; 选附加任务页不出(ShouldSkipPage):新装默认建桌面快捷方式,升级沿用上次的选择(UsePreviousTasks)
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
; 自动更新(静默安装)装完在后台重启,不在别人干活时弹 CC 窗口(这一行不变);
; 手动安装没有完成页,装完直接打开 CC(不是 postinstall:没有完成页时不等勾选)
Filename: "{app}\companion.exe"; Parameters: "--background"; Flags: nowait postinstall runasoriginaluser; Check: WizardSilent
Filename: "{app}\companion.exe"; Flags: nowait runasoriginaluser skipifsilent

[UninstallDelete]
; 写账套前的本机快照目录(src/companion/dbf_writer.py:写完即删);只删空目录,万一留着快照不动
Type: dirifempty; Name: "{localappdata}\Pearnly\write-backups"
Type: dirifempty; Name: "{localappdata}\Pearnly"

[Code]
{ 开机自启项:src/companion/autostart.py 写在 HKCU\...\Run 的 PearnlyCompanion,值是 "<companion.exe 全路径>" --background。
  这一项由程序写、不由安装包写,所以卸载程序以前不知道它(教程问题第 6 条:卸载后还指向已删的 companion.exe)。 }
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  RunValue = 'PearnlyCompanion';
  LegacyDirName = 'Pearnly Companion';

{ 托盘程序无顶层窗口,CloseApplications 的重启管理器可能关不掉它 → 真正开始安装时强制 taskkill,
  保证覆盖安装能替换正在运行的 companion.exe / cdx_reindex_runner.exe。不加 /T:自动更新时安装包本身就是 companion.exe
  拉起的子进程,连进程树一起结束会把安装包也杀掉;CC 窗口的网页内核子进程在 companion.exe 退出后自己退出(旧目录删除另有重试)。
  不在向导一打开(InitializeSetup)就关:点「取消」时小助手已被关掉又不会重启(2026-09-28 录制实测)。 }
procedure KillRunning;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM companion.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM cdx_reindex_runner.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  { 给网页内核子进程一秒钟跟着退出,免得重启管理器又把它们当成占用文件的程序、弹出「准备安装」页 }
  Sleep(1000);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillRunning;
  Result := '';
end;

{ 2026-10-20 前的按用户安装目录 %LOCALAPPDATA%\Programs\Pearnly Companion。
  更早的全机安装在 C:\Program Files\Pearnly Companion:当前用户没有权限删,只把开机自启改指过来(见 RepointAutostart)。 }
function LegacyDir: String;
begin
  Result := ExpandConstant('{userpf}\') + LegacyDirName;
end;

function AutostartCommand: String;
begin
  Result := '"' + ExpandConstant('{app}\companion.exe') + '" --background';
end;

{ 开机自启开着(有这一项)才改指新位置;没开不替人打开。值与 autostart.default_command() 同一写法。 }
procedure RepointAutostart;
var
  Current: String;
begin
  if RegQueryStringValue(HKCU, RunKey, RunValue, Current) and (CompareText(Current, AutostartCommand) <> 0) then
  begin
    RegWriteStringValue(HKCU, RunKey, RunValue, AutostartCommand);
    Log('Autostart repointed: ' + Current + ' -> ' + AutostartCommand);
  end;
end;

{ 任务栏上固定的 CC:固定时系统复制了一份快捷方式,指着旧目录的 companion.exe;旧目录删掉后它就打不开。改指新位置。 }
procedure RepointPinnedShortcuts(const OldExe: String);
var
  Folder, Path, Target: String;
  FindRec: TFindRec;
  Shell, Link: Variant;
begin
  Folder := ExpandConstant('{userappdata}\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar');
  if not FindFirst(Folder + '\*.lnk', FindRec) then
    Exit;
  try
    Shell := CreateOleObject('WScript.Shell');
    repeat
      Path := Folder + '\' + FindRec.Name;
      try
        Link := Shell.CreateShortcut(Path);
        Target := Link.TargetPath;
        if CompareText(Target, OldExe) = 0 then
        begin
          Link.TargetPath := ExpandConstant('{app}\companion.exe');
          Link.WorkingDirectory := ExpandConstant('{app}');
          Link.IconLocation := ExpandConstant('{app}\cc.ico') + ',0';
          Link.Save;
          Log('Pinned shortcut repointed: ' + Path);
        end;
      except
        Log('Pinned shortcut skipped: ' + Path + ' ' + GetExceptionMessage);
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

{ 老用户升级:新版装进新目录之后,把旧目录里的整份程序删掉(卸载项随同一个 AppId 已改指新目录里的卸载程序)。
  删不干净(文件还被占着)就隔一秒再试几次;剩下的下一次更新再删。 }
procedure RemoveLegacyDir;
var
  Attempt: Integer;
begin
  if (CompareText(LegacyDir, ExpandConstant('{app}')) = 0) or not DirExists(LegacyDir) then
    Exit;
  RepointPinnedShortcuts(LegacyDir + '\companion.exe');
  for Attempt := 1 to 5 do
  begin
    DelTree(LegacyDir, True, True, True);
    if not DirExists(LegacyDir) then
      Break;
    Sleep(1000);
  end;
  if DirExists(LegacyDir) then
    Log('Legacy folder not fully removed: ' + LegacyDir)
  else
    Log('Legacy folder removed: ' + LegacyDir);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    RepointAutostart;
    RemoveLegacyDir;
  end;
end;

{ 开机自启项里的程序路径(去掉引号与参数)。 }
function AutostartTarget(const Command: String): String;
var
  Rest: String;
  Quote: Integer;
begin
  Result := Trim(Command);
  if (Length(Result) > 0) and (Result[1] = '"') then
  begin
    Rest := Copy(Result, 2, Length(Result) - 1);
    Quote := Pos('"', Rest);
    if Quote > 0 then
      Result := Copy(Rest, 1, Quote - 1);
  end
  else if Pos(' ', Result) > 0 then
    Result := Copy(Result, 1, Pos(' ', Result) - 1);
end;

{ 卸载:先关掉在跑的 CC(否则 companion.exe 删不掉);卸完删开机自启项 —— 指向本次卸掉的目录、旧目录,
  或者指向的程序已经不在了(1.9.1 及以前卸载留下的)。指向别处还在的程序不动。 }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Current, Target: String;
begin
  if CurUninstallStep = usUninstall then
    KillRunning;
  if (CurUninstallStep = usPostUninstall) and RegQueryStringValue(HKCU, RunKey, RunValue, Current) then
  begin
    Target := AutostartTarget(Current);
    if (CompareText(ExtractFileDir(Target), ExpandConstant('{app}')) = 0)
       or (CompareText(ExtractFileDir(Target), LegacyDir) = 0)
       or not FileExists(Target) then
      RegDeleteValue(HKCU, RunKey, RunValue);
  end;
end;

{ ── 一键安装的小窗口(2026-10-20 用户定,照主流桌面产品) ──
  安装时只有一个小白窗:CC 图标居中、下面一条进度条、右下角取消。不显示页标题、右上角小图、文件名与「正在提取文件」。
  做法:官方向导的进度条与取消按钮留在窗体上,其余(页头、两条分隔线、整个页面区)藏起来,窗体缩到内容大小。
  图标用 256x256 的 cc.png,按 DPI 缩到 64 逻辑像素(只缩小、用 Inno 的高质量重采样,不放大),白底。卸载的进度窗口同一做法。
  所有向导页都跳过时 Inno 仍停在一页空白的「安装」按钮上(2026-10-20 测试机):那一页一出现就替人点下去。
  万一要显示「准备安装」页(有程序占着文件、要人选是否关掉),先恢复官方布局,不让人看不见要点的按钮。 }
var
  CompactIcon: TBitmapImage;
  FullClientWidth, FullClientHeight, FullCancelLeft, FullCancelTop: Integer;

function CompactLayout(const Form: TSetupForm; const Bar: TNewProgressBar; const Cancel: TNewButton;
  const IconFile: String; Icon: TBitmapImage): TBitmapImage;
var
  Margin: Integer;
begin
  Margin := ScaleX(32);
  Form.Color := clWhite;
  Form.ClientWidth := ScaleX(360);
  if Icon = nil then
  begin
    Icon := TBitmapImage.Create(Form);
    Icon.Parent := Form;
    Icon.BackColor := clWhite;
    Icon.Width := ScaleX(64);
    Icon.Height := ScaleY(64);
    Icon.Stretch := True;
    try
      Icon.PngImage.LoadFromFile(IconFile);
    except
      Log('CC icon not loaded: ' + IconFile + ' ' + GetExceptionMessage);
    end;
  end;
  Icon.Visible := True;
  Icon.Left := (Form.ClientWidth - Icon.Width) div 2;
  Icon.Top := Margin;
  Bar.Parent := Form;
  Bar.Visible := True;
  Bar.Left := Margin;
  Bar.Width := Form.ClientWidth - 2 * Margin;
  Bar.Height := ScaleY(8);
  Bar.Top := Icon.Top + Icon.Height + ScaleY(28);
  { 取消按钮原来在底部按钮栏里(窗体缩小后那一栏在可见区域外,2026-10-20 测试机截图看不到取消):挪到窗体上 }
  Cancel.Parent := Form;
  Cancel.Top := Bar.Top + Bar.Height + ScaleY(24);
  Cancel.Left := Form.ClientWidth - Margin - Cancel.Width;
  Form.ClientHeight := Cancel.Top + Cancel.Height + ScaleY(16);
  Result := Icon;
end;

procedure WizardCompact;
begin
  WizardForm.MainPanel.Visible := False;
  WizardForm.Bevel1.Visible := False;
  WizardForm.Bevel.Visible := False;
  WizardForm.OuterNotebook.Visible := False;
  WizardForm.NextButton.Visible := False;
  WizardForm.BackButton.Visible := False;
  CompactIcon := CompactLayout(WizardForm, WizardForm.ProgressGauge, WizardForm.CancelButton,
    ExpandConstant('{tmp}\cc.png'), CompactIcon);
end;

procedure InitializeWizard;
begin
  FullClientWidth := WizardForm.ClientWidth;
  FullClientHeight := WizardForm.ClientHeight;
  FullCancelLeft := WizardForm.CancelButton.Left;
  FullCancelTop := WizardForm.CancelButton.Top;
  ExtractTemporaryFile('cc.png');
  WizardCompact;
  WizardForm.CenterOnShow := True;
end;

function SetTimer(hWnd, nIDEvent, uElapse, lpTimerFunc: LongWord): LongWord;
  external 'SetTimer@user32.dll stdcall';
function KillTimer(hWnd, uIDEvent: LongWord): BOOL;
  external 'KillTimer@user32.dll stdcall';

var
  StartTimer: LongWord;

procedure StartInstall(Wnd, Msg, IDEvent, Time: LongWord);
begin
  KillTimer(0, StartTimer);
  StartTimer := 0;
  if WizardForm.CurPageID = wpReady then
    WizardForm.NextButton.OnClick(WizardForm.NextButton);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := PageID = wpSelectTasks;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpPreparing then
  begin
    WizardForm.ClientWidth := FullClientWidth;
    WizardForm.ClientHeight := FullClientHeight;
    WizardForm.CancelButton.Left := FullCancelLeft;
    WizardForm.CancelButton.Top := FullCancelTop;
    WizardForm.ProgressGauge.Visible := False;
    if CompactIcon <> nil then
      CompactIcon.Visible := False;
    WizardForm.MainPanel.Visible := True;
    WizardForm.Bevel1.Visible := True;
    WizardForm.Bevel.Visible := True;
    WizardForm.OuterNotebook.Visible := True;
    WizardForm.NextButton.Visible := True;
  end
  else if CurPageID = wpInstalling then
    WizardCompact
  else if (CurPageID = wpReady) and not WizardSilent then
    { 这时窗口还没显示,Inno 不接受「下一步」(没显示的 Ready 页点了也不动):等窗口出来再点 }
    StartTimer := SetTimer(0, 0, 100, CreateCallback(@StartInstall));
end;

procedure InitializeUninstallProgressForm;
begin
  UninstallProgressForm.MainPanel.Visible := False;
  UninstallProgressForm.Bevel1.Visible := False;
  UninstallProgressForm.Bevel.Visible := False;
  UninstallProgressForm.OuterNotebook.Visible := False;
  CompactIcon := CompactLayout(UninstallProgressForm, UninstallProgressForm.ProgressBar,
    UninstallProgressForm.CancelButton, ExpandConstant('{app}\cc.png'), nil);
  UninstallProgressForm.CenterOnShow := True;
end;
