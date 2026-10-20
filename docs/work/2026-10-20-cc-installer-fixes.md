# CC 桌面版安装包修复（教程问题第 1、6、7、8 条）

- 来源：`~/Downloads/pearnly-cc-video/功能分集/问题清单-给修复窗口.md`（2026-10-20 教程录制）；同日开发窗口转达用户意见
  「谁家产品安装的时候把 logo 放右上角？」→ 安装改成主流桌面产品的一键安装（见第 7 条）。
- 分支 `pearnly/cc-installer-fixes`，基线 `origin/main` = `241b9ef8`；工作树 `.claude/worktrees/agent-a31fdf25b29680b9d`。
- 范围状态：本次范围完成，**未合入 main、未发布、未部署**（开发窗口统一升版本、合并、发布）。`version.py` 未改（测试包版本号仍是 1.9.1）。
- 不做：签名证书（用户 2026-10-08 定不买）；注册商店账号、提交商店（用户自己的操作）；改任何电脑的智能应用控制 / SmartScreen / 安全设置。

## 第 6 条：卸载后开机自启项与 `%LOCALAPPDATA%\Pearnly` 残留

- 现象核实：`installer.iss` 只有 `[InstallDelete]`，没有 `[UninstallDelete]`、卸载阶段的 `[Code]`。开机自启项由程序写
  （`autostart.py`：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 的 `PearnlyCompanion`，值 `"<companion.exe>" --background`），
  `%LOCALAPPDATA%\Pearnly\write-backups` 由 `dbf_writer._local_backup_root()` 每次写账套时建（快照写完即删，目录留着）。
- 根因：这两样都是程序运行时建的，不在安装包的卸载记录里，卸载程序不知道要删。
- 修法（`installer.iss`）：卸载开始时先结束在跑的 CC（否则 companion.exe 删不掉）；卸完删 `PearnlyCompanion`——它指向本次卸掉的目录、旧目录
  `Pearnly Companion`，或指向的程序已不存在（1.9.1 及以前卸载留下的）才删，指向别处还在的程序不动。`[UninstallDelete]` 只删**空的**
  `{localappdata}\Pearnly\write-backups` 与 `{localappdata}\Pearnly`。
- 不扩大：`%APPDATA%\Pearnly`（配置、配对、日志）、`HKCU\Software\Pearnly`（设备密钥备份 `CC Desktop`、窗口位置 `Tomo`）照旧保留（原口径；设备密钥备份本来就是为卸载重装后恢复用的）。
- 测试机实证（`2afceaa6` 与 `964454d3`）：卸载后 Run 项没了、卸载项没了、程序目录没了、`write-backups` 没了；`%LOCALAPPDATA%\Pearnly` 因为里面有
  别的任务放的 `TestLedgers`（不是 CC 的），按设计保留；`%APPDATA%\Pearnly` 与 `HKCU\Software\Pearnly` 保留。

## 第 7 条：安装界面没有本地化、配图是默认图 → 一键安装

- 现象核实：没有 `[Languages]`（只有英文）、没有配图指令；测试机截图（`3ac30557` 版）证实标准向导右上角是小图槽、进度页逐个列文件路径。
- 修法（用户同日定：照 Claude 桌面版 / Chrome / VS Code 用户版这类）：
  - **一键安装**：欢迎、选目录、开始菜单、附加任务、准备安装、完成页一律不出（`Disable*Page=yes`，附加任务页 `ShouldSkipPage` 跳过、沿用上次选择）。
    所有页都跳过时 Inno 仍会停在一页空白的「安装」上，且窗口没显示时不接受「下一步」（Inno 源码 `NextButtonClick`：`wpReady` 且窗口不可见就返回），
    所以在 `CurPageChanged(wpReady)` 里起一个 100 ms 计时器，窗口出来后替人点一下（静默安装不走这条）。装完直接打开 CC（`[Run]` 非 postinstall，不带 `--background`）。
  - **安装中只有一个小白窗**：CC 图标居中（`assets/cc.png` 256px，用 Inno 的高质量重采样缩到 64 逻辑像素，只缩小不放大）、下面一条进度条、右下角取消；
    页头（右上角小图）、分隔线、整个页面区（含「正在提取文件」和文件路径）都藏起来，窗体缩到内容大小（`CompactLayout`）。
    取消按钮在官方窗体上锚定右下角，缩窗体前改成按左上定位，否则被挤出可见区（测试机截图踩过）。万一要出「准备安装」页（有程序占着文件要人选），先恢复官方布局。
  - **文案**：窗口标题只写「CC」（`SetupWindowTitle=%1`、`UninstallAppFullTitle=%1`、`AppVerName=CC`），其余看得到的字只有取消、确认、出错提示，
    全是 Inno 译文自带的。跟 Windows 显示语言：english（第一个 = 兜底）、thai、chinesesimplified；`ShowLanguageDialog=no`、`LanguageDetectionMethod=uilanguage`。
    **没有新增自写文案**（原来任务页那两句英文现在不显示，泰 / 中译文也删了）。`check-copy.py --report`：新增 0、改动 0、拦截 0（闸门不扫 `.iss`，如实说明）。
  - 译文来源：jrsoftware/issrc 标签 `is-6_7_1`（提交 `cfdf48923178df4b4f040e038b423aa555a61ffc`）——`Files/Languages/Thai.isl`（6.7.1 起官方）、
    `Files/Languages/Unofficial/ChineseSimplified.isl`（非官方）。原样拷到 `companion/packaging/languages/`，`.gitattributes` 关掉换行转换，
    sha256 写进 `tests/test_installer_contract.py`。编译器要 6.7.1 起（脚本里 `#if Ver < 6.7.1 #error`；CI 的 windows-latest 自带 6.7.1）。
  - **卸载同样精简**：从 Windows「设置 → 应用」卸载时系统先弹自己的确认，再运行卸载项的 UninstallString——安装时给它加 `/SILENT`，卸载程序不再问第二遍、
    也不弹「已删除」框，只出同样的小窗口（图标 + 进度条；卸载不能取消，不显示那个一直灰着的取消按钮）。开始菜单「Uninstall CC」快捷方式不带参数：
    卸载程序自己的确认框 + 小窗口进度 + 「已删除」框（这条路径没有系统确认；「已删除」框在非静默卸载里是 Inno 固定行为，去不掉）。
  - 静默升级（自动更新的 `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS`）不变：不出窗口，装完 `--background` 重启。

## 第 8 条：安装进度页露出「Pearnly Companion」

- 现象核实：`DefaultDirName={autopf}\Pearnly Companion`（为原地升级沿用旧名），进度页显示解压路径（测试机截图 `shots/2-silent-progress-006.png`：已是 `…\Programs\CC\…`）。
- 修法：`DefaultDirName={autopf}\CC`（`%LOCALAPPDATA%\Programs\CC`），AppId 不变。老用户迁移在 `[Code]` 的 `ssPostInstall`：
  1. `RepointAutostart`：开机自启项存在才改成 `"<新目录>\companion.exe" --background`（与 `autostart.default_command()` 同一写法），没开不替人打开；
  2. `RemoveLegacyDir`：任务栏固定（`User Pinned\TaskBar`）里指向旧 `companion.exe` 的快捷方式改指新位置；再删旧目录 `%LOCALAPPDATA%\Programs\Pearnly Companion`
     （删不干净隔一秒重试 5 次，剩下的下次更新再删；新旧同一目录时不删）；
  3. 卸载项：同一 AppId，安装时 Inno 把卸载项改指新目录的卸载程序；开始菜单 `CC` 组与桌面快捷方式由 `[Icons]` 重建到新目录。
  - 自动更新：`updater.run_installer_silent` 不带 `/DIR`，`UsePreviousAppDir=no`，所以装进新目录（契约测试守住）。
  - `KillRunning` 不加 `/T`：自动更新时安装包是 companion.exe 拉起的子进程，按进程树结束会把安装包自己杀掉（契约测试守住）。
  - 更早的全机安装 `C:\Program Files\Pearnly Companion`：当前用户删不了，只改开机自启。测试机上这个目录是空的、没有 HKLM 卸载项，不受影响。
  - 写死旧名的地方：改了 `installer.iss` 默认目录、测试里的示例路径（`test_autostart` / `test_company_folders` / `test_desktop_settings` → `Programs\CC`）、
    `test_installer_contract` 与 `tests/test_cc_desktop_name.py` 的断言。保留（内部标识，用户看不到，改了反而断老用户）：Run 值名 `PearnlyCompanion`、
    单实例名、AppUserModelID `Pearnly.Companion`、DPAPI 描述、argparse 描述、`release.ps1` 日志行、`[InstallDelete]` 里要删的旧快捷方式名。
    按任务书保留：下载兼容地址 `PearnlyCompanion-Setup.exe`。不动：`scripts/cowork-windows-test-client.py`（绑定 2026-09-21 固定旧包与 exe 哈希的历史工具）。
    服务端与 `tests/test_companion_contract.py` 里没有安装路径断言。

## 第 1 条：智能应用控制拦截（Microsoft Store 路线）

### 联网核实（2026-10-20）

| 问题 | 结论 | 依据 |
| --- | --- | --- |
| 商店开发者账号是否免费 | 是。新流程（从 storedeveloper.microsoft.com 进）个人、公司账号都**不收注册费**。个人账号限「与业务、职业无关」；CC 是公司产品，应开**公司账号**（MR. ERP COMPANY LIMITED）：要 D-U-N-S 号或公司登记文件，验证邮箱要用公司域名（不收 Gmail），人工审核 2–5 个工作日 | [Steps to open a developer account](https://learn.microsoft.com/en-us/windows/apps/publish/partner-center/open-a-developer-account)；[Windows 开发者博客 2025-09-10](https://blogs.windows.com/windowsdeveloper/2025/09/10/free-developer-registration-for-individual-developers-on-microsoft-store/) |
| 商店是否收 MSIX 并由商店签名 | 是。MSIX 提交不需要自己的证书，审核通过后微软重新签名，免费 | [Get started with the Microsoft Store](https://learn.microsoft.com/en-us/windows/apps/publish/faq/get-started-with-the-microsoft-store) FAQ 11；[Code signing options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options) |
| Win32 安装包（EXE/MSI）上架是否要自己签名 | 要。商店不重签 EXE/MSI，安装包与其中 PE 文件须由受信任根证书计划内的 CA 签名，自签不收 | 同上两页 |
| 商店签名的 MSIX 能否过智能应用控制 | **推断能过，官方没有逐字写**。官方：智能应用控制对无法判断信誉的程序，只要「由受信任根证书计划内 CA 颁发的证书签名」就放行；MSIX 包内所有文件共用包的目录签名；商店用微软证书重签 | [Smart App Control 概述](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/overview)；[Manage packaged apps with App Control](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/design/manage-packaged-apps-with-appcontrol) |

结论：路走得通，没有硬伤（不收钱、不需证书）。唯一没有官方逐字证据的是「商店签名的 MSIX 过智能应用控制」，要在上架后用商店的私密发布装到录制笔记本（开着智能应用控制）上实测。

### 已做

- `companion/packaging/msix/AppxManifest.xml`：全信任桌面程序（`runFullTrust`、`Windows.FullTrustApplication`），`desktop:StartupTask`（TaskId `CCStartup`，
  `uap10:Parameters="--background"`，所以最低 Windows 10 2004 / 19041），x64，语言 en-us / th-th / zh-cn，显示名「CC」，图标 `Assets\*.png`
  （`packaging/make_images.py` 由 `assets/cc.png` 生成并提交）。
- `companion/packaging/msix/build_msix.py`：`release.ps1 -BuildOnly` 之后把程序文件夹 + `cdx_reindex_runner.exe` + 图标摆成包目录、填清单，用 Windows SDK
  的 MakeAppx 打 `dist\CC.msix`（不签名）。包版本 = `version.py` 前三段 + `.0`（商店要求第四段为 0）。包标识取环境变量
  `MSIX_IDENTITY_NAME` / `MSIX_PUBLISHER` / `MSIX_PUBLISHER_DISPLAY_NAME`，没设时用占位值（能打包、不能上传）。
- `.github/workflows/companion-build.yml`：每次都打 MSIX（MakeAppx 校验清单）；勾选输入 `msix` 才上传 `CC.msix`（保留 1 天）；包标识取仓库变量 `vars.MSIX_*`。
- 运行时识别包身份（`src/companion/package_identity.py`，`GetCurrentPackageFullName`，没有包身份回 15700）：
  - 更新交给商店：`gui_tray` 收到新版提示、手动「检查更新」都不走自己的安装包。
  - 开机自启：`autostart.py` 在包身份下改用 WinRT `Windows.ApplicationModel.StartupTask`（新依赖 `winrt-Windows.ApplicationModel==3.2.1`，只依赖已锁的
    winrt-runtime；`companion.spec` 加 hiddenimport）；`repoint()` 不碰 Run。用户在任务管理器关掉的（DisabledByUser）程序开不了，如实显示「关」。
  - 通知：包身份下不再设 `Pearnly.Companion`（包里没有对应快捷方式，通知会被丢），`toast.app_id()` 用包的 AppUserModelID。
  - 数据路径（按 [Understanding how packaged desktop apps run](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes)）：
    `%APPDATA%\Pearnly`、`%LOCALAPPDATA%\Pearnly` 下**已有**的文件直接读写原位置（从 Inno 版换到商店版时配置能接上）；**新建**的文件落到包的私有目录、
    对本程序看起来仍在原路径、卸载包时删。HKCU 写入（设备密钥备份、窗口位置）写进包的私有副本，包内照常可用，卸载包时删——商店版的设备密钥备份
    不能跨「卸载再装」保留（Inno 版不受影响）。账套、网络共享、Express 目录不在 AppData，不受虚拟化影响。
- Inno 安装包仍是主发布方式，`release.ps1`、`companion-release.yml` 未改。

### 没做、没能验证

- 商店版运行时（包身份下的自启、通知、单实例、DPAPI、写账套）**没有真机验证**：不签名的 MSIX 装不上，要么开开发者模式、要么把自签证书加进受信任根——
  都是改电脑安全设置，任务书严禁；只能上架后用商店私密发布装到测试机验证。
- Inno 版与商店版同时装在一台电脑上的迁移（检测已装的 Inno 版、提示卸载）没做；上架前要定口径（[从网页版迁到商店版](https://learn.microsoft.com/en-us/windows/apps/distribute-through-store/how-to-transition-users-from-your-web-unpackaged-app-to-store-packaged-app)）。两份同时开时单实例名相同，只会跑一份。
- 官网、网页的「下载 CC 桌面版」入口没改（未上架前不该改）。

### 需要用户做的事（按顺序）

1. 在 https://storedeveloper.microsoft.com 用公司身份开**公司**开发者账号（免费）：MR. ERP COMPANY LIMITED（0315551000297），准备 D-U-N-S 号或公司登记文件，
   验证邮箱用公司域名邮箱；等验证通过（人工审核 2–5 个工作日）。
2. Partner Center → 新建 MSIX 应用、保留名称（「CC」可能已被占用，要换一个能用的名字）；在「产品标识」页抄下 Package/Identity/Name、Publisher、PublisherDisplayName，
   填进 GitHub 仓库变量 `MSIX_IDENTITY_NAME`、`MSIX_PUBLISHER`、`MSIX_PUBLISHER_DISPLAY_NAME`。
3. 告诉开发窗口：用 `gh workflow run companion-build.yml --ref <分支> -f msix=true` 打带正式标识的 `CC.msix`，下载后在 Partner Center 上传；
   提交时「受限功能」说明 runFullTrust 的用途（读写本机 Express 账套的桌面程序）。
4. 先用「私密受众 / package flight」只发给自己，装到录制笔记本（开着智能应用控制）与测试机上验收，再决定是否公开、官网是否换下载入口。

## 验证

| 层级 | 代码 | 场景 | 结果 |
| --- | --- | --- | --- |
| 本地测试 | 工作树 | `companion` 全量 `pytest`（macOS，PySide6 6.11.1） | 通过（Windows 专属用例跳过） |
| 本地闸门 | `08e373ad` | `~/pearnly-devtools/gate.sh`（development.yml 全部 run 步骤，含 check-architecture、check-copy） | 110 步全过；之后各提交跑 check-architecture / check-copy / 契约测试 |
| CI | `2afceaa6` | Companion checks（Windows 全量测试 + 打包 + 启动冒烟）、Development checks | 均通过 |
| Windows 构建 | `2afceaa6` | Companion test build（运行 39458921531，`-f msix=true`）：ISCC 6.7.1、PyInstaller、冒烟、MakeAppx | 通过；`CC-Setup.exe` sha256 `72faa0ff…`，`CC.msix` 约 223 MB（占位标识） |
| 测试机实装 | `964454d3` | 正式版 1.9.1 装在旧目录 + 开机自启指旧目录 + 任务栏固定（测试用 .lnk）→ 以自动更新同一组参数 `/VERYSILENT…` 装测试包 | Run 改指 `Programs\CC`、旧目录删除、固定快捷方式改指新位置、卸载项与开始菜单 / 桌面快捷方式指新目录、卸载项带 `/SILENT`、CC 以 `--background` 从新目录起来（不出窗口）；`logs/15-*` |
| 测试机实装 | `2afceaa6` | 双击安装（中文系统自动中文；`/LANG=thai` 泰文） | 不出向导页、只有小窗口（图标 + 进度条 + 取消/ยกเลิก），约 1 分钟装完，CC 窗口自动打开；`final/10-*`、`final/11-*` |
| 测试机实装 | `2afceaa6` | 开始菜单快捷方式路径卸载（不带参数） | 中文确认框 → 小窗口进度（无取消）→「已删除」框；Run 项、卸载项、程序目录、`write-backups` 清掉；`final/13-*` |
| 测试机实装 | `2afceaa6` | 设置路径卸载（UninstallString 原样，带 `/SILENT`） | 只有小窗口进度，约 4 秒删完；`final/17-*` |
| 测试机实装 | `964454d3` | `/LANG=english` | 标题「CC」、按钮 Cancel（截图未留，日志 `logs/12-*`） |

截图与日志：`~/pearnly-devtools/cc-installer-fixes/`（`shots/` 是改一键安装前 `3ac30557` 版的进度页，`final/` 是最终版，`logs/` 是各步状态与安装 / 卸载日志）。
测试机的 Windows 显示语言是简体中文（`GetUserDefaultUILanguage` = 0x0804），所以自动选中文；泰文用 `/LANG=thai` 验（没改系统语言）。

CI 与测试机中途踩过的（都已修、加了契约测试）：`ChineseSimplified.isl` 在 Windows 检出时被转 CRLF、哈希不符；`[Code]` 花括号注释里写 `{app}` 提前结束注释；
所有页跳过后停在空白「安装」页；`InitializeBitmapImageFromIcon` 读 PNG 压缩的 cc.ico 失败（改用 cc.png）；取消按钮被锚定右下角挤出可见区。

### 测试机使用与恢复（2026-10-20 22:06 – 10-21 00:10，锁 `~/pearnly-devtools/locks/testpc.lock` 已释放）

- 做法：在 skin3 上「装测试包 → 验升级 / 卸载 → 重装正式版并还原配置」。全程用 skin3 一次性计划任务（交互、普通权限、允许电池），任务用完即删。
- 测试前备份：`C:\PearnlyStress\_备份\cc-installer-fixes-2026-10-20\`（`companion.json`、`update_state.json`、开机自启值、卸载项注册表导出），保留作记录。
- 恢复后（`logs/7-after-restore.txt`）：正式版 1.9.1（安装包 sha256 `2923082a…` = latest.json）装回 `Programs\Pearnly Companion`，配置还原、开机自启值与测试前一致、
  卸载项与快捷方式与测试前一致，CC 以 `--background` 运行，心跳 101 / 101 / 133（与测试前相同）。测试用任务栏 .lnk、计划任务、临时卸载目录都已删。
- 遗留：`C:\Users\Public\pearnly-test\cc-installer\` 空目录（恢复时由计划任务拉起的 CC 以它为工作目录，删不掉），CC 下次重启后可删；不影响使用。
- 顺带看到（不属本任务）：CC 窗口打开的 `testai.pearnly.com` 返回 502（Cloudflare → 源站错误），测试期间一直这样，是测试环境那一侧。

## 收尾

- 分支 `pearnly/cc-installer-fixes` 已推送，未合入 main；合并、升版本、发布由开发窗口统一做。工作树与分支待合入后按收工清理删除。
