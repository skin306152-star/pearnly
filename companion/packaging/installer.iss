; CC 桌面端安装包(Inno Setup;原 Pearnly 小助手,2026-10-10 并入 CC 桌面端)· 把 64 位主程序文件夹(companion.exe + _internal)+ 预编译 cdx_reindex_runner.exe
; 装到当前用户的程序目录(%LOCALAPPDATA%\Programs),建快捷方式,装完启动(首次弹配对窗)。未签名(内部测试 · 签名记 backlog)。
; 按用户安装(2026-10-08):不要管理员权限,自动更新静默覆盖安装时不弹「用户账户控制」授权窗口
; (原来装在 Program Files,每次自动更新都弹授权,打断正在用电脑的人)。配置、配对在 %APPDATA% 里,不受影响。
; 编译:ISCC.exe packaging\installer.iss → dist\CC-Setup.exe（2026-10-12 起；旧名 PearnlyCompanion-Setup.exe 由下载站同时保留）
#define AppName "CC"
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
; 目录名沿用旧名:已装的电脑原地升级,开机自启、配置都不用搬
DefaultDirName={autopf}\Pearnly Companion
; 旧版按全机装在 Program Files:不沿用那个目录(当前用户写不进去),装到上面的用户目录
UsePreviousAppDir=no
DefaultGroupName=CC
UsePreviousGroup=no
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=CC-Setup
VersionInfoProductName=CC
VersionInfoDescription=CC Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
UninstallDisplayName=CC
SetupIconFile=..\assets\cc.ico
UninstallDisplayIcon={app}\companion.exe
; 覆盖安装:旧版常驻托盘会锁住 companion.exe → 装前强制关掉,否则文件替换失败。
CloseApplications=force
RestartApplications=no

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
Source: "..\dist\companion\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Harbour DBFCDX 后台索引重建器(预编译原生 exe · 与主 exe 同目录 · cdx_reindex.py 定位)
Source: "..\dist\cdx_reindex_runner.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\cc.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; AppUserModelID 必须与进程 SetCurrentProcessExplicitAppUserModelID 一致,否则 Win10/11
; 托盘 Toast(录入完成气泡)被当无主通知丢弃。值见 src/companion/main.py APP_USER_MODEL_ID。
Name: "{group}\CC"; Filename: "{app}\companion.exe"; IconFilename: "{app}\cc.ico"; AppUserModelID: "Pearnly.Companion"
Name: "{group}\Uninstall CC"; Filename: "{uninstallexe}"
Name: "{autodesktop}\CC"; Filename: "{app}\companion.exe"; IconFilename: "{app}\cc.ico"; AppUserModelID: "Pearnly.Companion"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
; 自动更新(静默安装)装完在后台重启,不在别人干活时弹 CC 窗口;手动安装装完直接打开 CC
Filename: "{app}\companion.exe"; Parameters: "--background"; Flags: nowait postinstall runasoriginaluser; Check: WizardSilent
Filename: "{app}\companion.exe"; Flags: nowait postinstall runasoriginaluser skipifsilent

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
