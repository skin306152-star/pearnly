# CC 桌面版：同站点的新窗口在 CC 子窗口里开（方案 B，2026-11-04）

- 窗口：cc-entry-fixes（主会话 local_3d36d798 派），分支 `pearnly/cc-desktop-site-popup`，基线 main `8b2291de`。
- 评估与审定：`docs/work/2026-11-04-cc-entry-fixes.md` §2.2、§8 第 2 条（B 由本窗口做；合入后不发版，随 CC 1.12.14 由主会话排
  staging、测试机实测和正式发布）。
- 版本号：本分支不改 `companion/src/companion/version.py`（main 上 1.12.13），发版时由主会话统一改。

## 缺陷与根因

CC 对话页里有三个入口用 `window.open` / `target=_blank` 打开站点自己的页面：

| 入口 | 产品 | 地址 |
|---|---|---|
| 公司页「···」→「在浏览器中打开」 | ERP | `/desk/pearnly-company/<编号>` |
| 待处理一行（ERP 的单据） | ERP | `/desk/pearnly-purchase/<单号>`、出入库单、收银日汇总等 |
| 定时任务「···」→「查看需要处理的」 | Cowork | `/desk/pearnly-intake?push_state=failed` |

CC 桌面版 `gui_tomo._Popup` 以前把所有新窗口交给系统浏览器（`QDesktopServices.openUrl`），系统浏览器里没有站点会话 →
站点 `begin_login` → Keycloak 登录页。网页版、手机版不受影响（新标签页带同一份 Cookie）。

## 改动（只在 `companion/`）

- `tomo_links.popup_route(url, opener)`（纯函数）：与打开它的页面**同一站点**（同主机同端口）的 https 页面 → `window`；
  别的 Pearnly 页面（其他公司站点、fatcc.com、pearnly.com 的关于 / 隐私 / 条款）与外部网址 → `external`（系统浏览器，同以前）；
  本地文件、data: → `block`。
- `gui_tomo._Popup` 记下打开它的页面，第一次导航时按 `popup_route` 分：`window` 开 `_SiteWindow`。
- `_SiteWindow`：同一个 `QWebEngineProfile`（同一份登录），系统标题栏，标题跟页面，关了释放网页内核（`WA_DeleteOnClose` +
  `setPage(None)`），页面里的链接与再开的新窗口照主窗口 `TomoPage` 的规则；父窗口是 CC 主窗口。只在点开时才有，不常驻
  （「CC 桌面版要轻量」）。
- 不改载荷、服务端契约（`tests/test_companion_contract.py` 不涉及），不改文案。

## 验证

- 单测 `companion/tests/test_tomo_links.py`：`popup_route` 判定（同站 → 子窗口；别的站点、入口、公开页、外部、http、不同端口 →
  系统浏览器；本地文件、data: → 不开；打开它的页面为空 → 系统浏览器）；`gui_tomo` 源码里 `_Popup` 用它、`_SiteWindow` 用同一
  profile 与 `TomoPage`、关了释放。两条新用例在旧代码上分别 AttributeError / AssertionError（反例成立）；本机（macOS，无 pytest）
  逐个调用该文件 14 条用例全过；Windows 全量由分支的 Companion checks（测试机 runner）跑。
- Qt 窗口本身只能在 Windows 真机上验（实验栈不跑 CC 桌面版）：见下面清单。

## 测试机真机清单（发 staging 后由主会话安排，逐条记「通过 / 不通过」）

前提：测试机 CC 桌面版装上含本改动的版本；同时连 testerp 与 testcowork 两个站点；ERP 账号是管理员（有两家公司）。
系统浏览器里**不要**登录这两个站点（用来确认没有借系统浏览器的会话）。

| # | 入口 | 怎么点 | 看到什么算对 |
|---|---|---|---|
| 1 | ERP 公司页「在浏览器中打开」 | testerp 的 CC → 左侧「公司」→ 点一家 → 标题旁「···」→「在浏览器中打开」 | 弹出一个 CC 自己的窗口（不是 Edge / Chrome），直接显示这家公司的 ERP 公司页，不出登录页；窗口标题是页面标题；关掉子窗口后 CC 主窗口照常 |
| 2 | ERP 待处理的单据 | testerp 的 CC → 左侧「待处理」→ 点一张采购单（或出入库单） | 同上：CC 子窗口直接打开那张单据页，不要登录 |
| 3 | Cowork 定时任务「查看需要处理的」 | testcowork 的 CC → 「定时任务」→ 有需要处理结果的规则「···」→「查看需要处理的」 | CC 子窗口直接打开录入页（筛着推送失败的），不要登录 |
| 4 | 子窗口里再开新窗口 | 在 1 的子窗口里点一个会开新标签的链接（例如打印） | 同站点的再开一个 CC 子窗口；外部链接交给系统浏览器 |
| 5 | 别的站点、公开页照旧 | CC 左下角菜单「关于 / 隐私 / 条款」 | 交给系统浏览器打开 pearnly.com（同以前） |
| 6 | 下载 | 在子窗口里导出一个文件 | 存进「下载」文件夹并弹通知（同主窗口） |
| 7 | 退出 / 轻量 | 关掉 CC 主窗口 | 子窗口一起关；任务管理器里网页内核进程随之退出 |

## 合入

- 分支 CI：Development checks + Companion checks（测试机 runner）。
- 合入 main 后**不发版**；发 1.12.14 的 staging、测试机实测与正式发布由主会话排。
