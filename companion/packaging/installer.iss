; Pearnly Companion 安装包(Inno Setup)· 把 64 位 companion.exe + 预编译 cdx_reindex_runner.exe
; 装到当前用户的程序目录(%LOCALAPPDATA%\Programs),建快捷方式,装完启动(首次弹配对窗)。未签名(内部测试 · 签名记 backlog)。
; 按用户安装(2026-10-08):不要管理员权限,自动更新静默覆盖安装时不弹「用户账户控制」授权窗口
; (原来装在 Program Files,每次自动更新都弹授权,打断正在用电脑的人)。配置、配对在 %APPDATA% 里,不受影响。
; 编译:ISCC.exe packaging\installer.iss → dist\PearnlyCompanion-Setup.exe
#define AppName "Pearnly Companion"
; 版本号单一真相源 = src/companion/version.py。release.ps1 用 /DAppVersion=<ver> 注入;
; 直接 ISCC 编译时回落下方默认(手动保持与 version.py 一致)。
#ifndef AppVersion
  #define AppVersion "1.1.19"
#endif

[Setup]
AppId={{8F3C2A91-4E7B-4D2A-9C11-PEARNLYCOMP01}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Pearnly
PrivilegesRequired=lowest
; 非管理员模式下 {autopf} = 当前用户的 %LOCALAPPDATA%\Programs
DefaultDirName={autopf}\Pearnly Companion
; 旧版按全机装在 Program Files:不沿用那个目录(当前用户写不进去),装到上面的用户目录
UsePreviousAppDir=no
DefaultGroupName=Pearnly Companion
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=PearnlyCompanion-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
UninstallDisplayName=Pearnly Companion
SetupIconFile=..\assets\pearnly.ico
UninstallDisplayIcon={app}\companion.exe
; 覆盖安装:旧版常驻托盘会锁住 companion.exe → 装前强制关掉,否则文件替换失败。
CloseApplications=force
RestartApplications=no

[Files]
Source: "..\dist\companion.exe"; DestDir: "{app}"; Flags: ignoreversion
; Harbour DBFCDX 后台索引重建器(预编译原生 exe · 与主 exe 同目录 · cdx_reindex.py 定位)
Source: "..\dist\cdx_reindex_runner.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\pearnly.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; AppUserModelID 必须与进程 SetCurrentProcessExplicitAppUserModelID 一致,否则 Win10/11
; 托盘 Toast(录入完成气泡)被当无主通知丢弃。值见 src/companion/main.py APP_USER_MODEL_ID。
Name: "{group}\Pearnly Companion"; Filename: "{app}\companion.exe"; IconFilename: "{app}\pearnly.ico"; AppUserModelID: "Pearnly.Companion"
Name: "{group}\Uninstall Pearnly Companion"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Pearnly Companion"; Filename: "{app}\companion.exe"; IconFilename: "{app}\pearnly.ico"; AppUserModelID: "Pearnly.Companion"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
; 装完自动启动 + 弹配对窗(去掉可选勾选,装完直接进配对·避免"默默躲托盘")
Filename: "{app}\companion.exe"; Flags: nowait postinstall runasoriginaluser

[Code]
{ 托盘程序无顶层窗口,CloseApplications 的重启管理器可能关不掉它 → 真正开始安装时强制 taskkill,
  保证覆盖安装能替换正在运行的 companion.exe / cdx_reindex_runner.exe。
  不在向导一打开(InitializeSetup)就关:点「取消」时小助手已被关掉又不会重启(2026-09-28 录制实测)。 }
procedure KillRunning;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM companion.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM cdx_reindex_runner.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillRunning;
  Result := '';
end;
