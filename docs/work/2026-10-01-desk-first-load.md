# 网页首次打开慢（2026-10-01）

压测窗口转来用户同意的四项（DMS / ERP / Cowork），生产实测数据见压测窗口的消息（main `2bbcb4ee` 部署后、只读测量）。
分支 `codex/desk-first-load`，基线 main `2bbcb4ee`。用户另要求"看官方怎么做，官方好还是我们的好"，逐项对照写在每项里。

## 1. 每个产品的 Desk 脚本 / 样式打包（做）

- 现状：`app_include_js` / `app_include_css` 是一个文件一个请求（ERP 68 个、Cowork 35 个、DMS 23 个），带 `?v=内容摘要`。
- 官方做法：App 在 `public/` 下放 `*.bundle.js` / `*.bundle.css` 入口，`bench build --production` 用 esbuild 打包、压缩、文件名带哈希，
  登记进 `sites/assets/assets.json`；hooks 写包名，渲染时 `bundled_asset` 查到真实地址。**官方的好**，照官方做。
- 实现：
  - hooks 里把清单写成 `DESK_JS` / `DESK_CSS`（顺序、注释不变），`app_include_* = desk_bundle(app, 包名, 清单)`（`pearnly_core/desk/assets.py`）。
  - 镜像构建时 `scripts/build-desk-bundles.py` 按清单生成官方入口（`public/js/<app>_desk.bundle.js` 按原顺序 import；
    `public/scss/<app>_desk.bundle.scss` 用 `@import "<app>/public/desk/x"`——入口会被拷到临时目录、且带 `.css` 的 import 会被 Sass 原样留下，
    所以按 Frappe 搜索路径、不带扩展名），并记下每个源文件的摘要（`public/dist/desk-sources.json`）；随后 `bench build --app <app> --production`
    （`infrastructure/images/frappe.Dockerfile`，构建时临时把 `sites/assets` 链到镜像里的 `assets`，与入口脚本运行时的链接一致）。
  - 比官方多的一处：`desk_bundle` 只在"包确实由当前源码打出"（摘要对得上、`assets.json` 有登记、包文件在）时才交包名，否则退回逐个加载。
    原因是测试机挂实时源码时，镜像里的包是旧代码，官方开发模式靠 `bench watch` 重打，我们的测试环境不跑它。
  - 生成物都在 `.gitignore`，不进仓库。
- 本地一次性容器实测（包大小）：core JS 51 KB / CSS 10.6 KB；Cowork JS 175 KB（gzip 53 KB，原样拼接 325 KB / 99 KB）/ CSS 28.8 KB；
  DMS JS 12 KB；ERP JS 318 KB（gzip 88 KB）/ CSS 47 KB。`assets.json` 44 → 51 条，框架自己的条目不变。

## 2. 启动信息里的翻译词典（不做，说明原因）

- 官方做法：`desk.html` 把 `frappe.boot` 和 `frappe._messages = boot.__messages` 内联在页面里、在框架脚本之前；没有官方开关把它拆成独立可缓存资源。
- 要拆就得覆盖框架核心模板（违背 ADR-005/007 原生 Desk），且框架脚本一加载就要用 `__()`，异步取会漏翻 / 闪英文。
- 收益约每次刷新少传 250–280 KB（gzip），办公室网络约 0.2–0.3 秒。**这项保持官方做法**，不改。

## 3. 部署后冷缓存（做）

- 官方做法：没有预热，`migrate` 清缓存后第一个请求现场算（生产实测启动信息冷 4.6–6.6 秒、热 0.3–0.7 秒）。
- 实现：`pearnly_core/desk/warmup.py`，激活脚本在**切回正常服务、写好版本记录之后**跑一次
  `bench --site all execute pearnly_core.desk.warmup.run`（`scripts/production/activate-product.sh`），失败只告警、不算激活失败。
  - 表结构：本站 pearnly App 的 DocType + User / File，走 `getdoctype` 用的 `frappe.desk.form.meta.get_meta`（全站共用）。
  - 启动信息：最近 30 天活跃的系统用户（最多 200，最近的在前），每人一个全新站点上下文，`frappe.boot.get_bootinfo()` 写进
    `bootinfo` 哈希——与 `frappe.sessions.get` 第一次登录时写的是同一份；已有缓存的不动。所有站点合计 180 秒上限。
  - `bench execute` 遇到异常会把方法再 eval 一遍，所以 `run` 绝不往外抛。
  - 不是定时任务，只在激活时跑一次。
- 本机样板实测：清缓存后不预热，第一次打开启动信息 0.44 秒、Cowork Bank Reconciliation 表结构 0.118 秒；预热后第一次打开 0.16 秒 / 0.001 秒
  （与热态相同）。生产上的效果要部署后由压测窗口复测。

## 4. 页面首开请求链（做一部分）

- 官方做法：Frappe 自己的界面文案走 `__()`（启动信息里的词典），不额外请求；列表页 `with_doctype`（只在内存里缓存）→ 再取列表，也是先后两步。
- 我们的页面文案是自己的接口 `browser_text`（四语一次给全），每进一页都请求、同一前缀一页里还取两三次，而且排在表结构之前。
- 实现（`pearnly_core/public/desk/product.js` 的 `p.text_pack` / `p.texts`）：
  - 同一会话每个前缀只取一次（并发的合成一次，失败不留缓存）；
  - 启动信息带语言包指纹 `pearnly_text_version`（`browser_text.text_version`，官方 `extend_bootinfo` 钩子给，不进按用户缓存的那份），
    有指纹就把整包存进浏览器，刷新、下次登录不再取；指纹一变旧的随即清掉；浏览器存储不可用时照旧去取。
- 不做的：
  - 列表数据和表结构并行：各页 `load` 读页面上的筛选 / 控件，提前发会拿错参数，三十多个页面逐页验证的代价大于省下的一次来回；官方列表也是先后两步。
  - 缩小 User（245 KB）/ Purchase Document（148 KB）表结构：是官方 `getdoctype` 的整包（含子表），每个会话只拉一次（gzip 后小得多），
    改它要绕开官方 `with_doctype`。
  - 首屏轮询改实时事件：次要项，和并发目标一起另做。

## 验证

- `tests/test_desk_first_load.py`（镜像里跑，已加进 `ci-candidate-checks.sh` 离线道）：打包入口与运行时判断一致、各种不一致时退回逐个加载、
  真实清单文件都在且无重复；语言包指纹；预热每人新上下文、单人失败不影响其他人、不往外抛、跨站点共享时长上限、已有缓存不动。
- `tests/test-desk-texts.cjs`（已加进 Development checks）：文案缓存四种情况。
- `scripts/production/test-activate-product.py` 新增：预热在切回正常服务之后、失败不影响激活与版本记录。
- `python3 scripts/check-architecture.py` 通过；Development checks 同名命令在镜像（Python 3.14）里全过，容量检查本机过。
- 还缺：分支 CI、生产镜像上的候选离线道（改了 `pearnly_core`）、测试机浏览器实测（用测试机前先问 LINE 窗口和压测窗口）、部署后压测窗口复测。
