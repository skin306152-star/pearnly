# 2026-10-10 Pearnly 智能体底座（第一批：骨架）

- 决策：[ADR-019](../adr/0019-assistant-foundation.md)
- 状态：**进行中**（第一批骨架本地完成；未推送、未合并、未部署、未在真实站点跑通）
- 仓库 / 分支：本仓 `pearnly/agent-foundation`，基线 `c3a7622c12e8a13895a8498f254faffa64a31ddb`（当时的 `origin/main`）
- 工作树：`/Users/skin/pearnly-agent-foundation`

## 目标与完成条件

三产品共用的智能体：网页（PWA）/ 桌面 / 手机打开同一段对话，模型与执行用 Claude Managed Agents，登录与租户、权限、套账、数据隔离沿用现有体系。

第一批只搭骨架：共用层后端、对话页前端、站点入口、CI 与镜像依赖、架构文档。完成条件：离线测试、类型检查、前端构建、架构检查通过；在一个真实产品站点上用测试密钥完成「新建会话 → 发消息 → 收到回复 → 调用 `pearnly_whoami` 工具」（**尚未做**，见下）。

本批明确不做：控制面密钥录入与推送、Service Worker / 推送通知、桌面端内嵌、LINE 桥接、产品业务工具、逐字流式。

## 落位

| 部分 | 位置 |
| --- | --- |
| 纯约定 | `apps/pearnly_core/pearnly_core/assistant/contracts.py` |
| 引擎目录与适配器 | `assistant/engines/__init__.py`（唯一登记处）、`engines/base.py`（接口）、`engines/anthropic_managed_agents.py`（首家） |
| 配置（产品、引擎、模型、密钥） | `assistant/config.py`；站点键 `pearnly_assistant_engine / _model / _api_key` 已登记在 `architecture-policy.json` |
| 智能体说明 / 按设置自动备好远端 | `assistant/definition.py`（与厂商无关）、`assistant/engine.py`（取适配器 + 缓存） |
| 工具声明与派发 | `assistant/tools.py`；产品经 `hooks.py` 的 `pearnly_assistant_tools` 声明 |
| 会话归属与读写、接口 | `assistant/sessions.py`、`assistant/api.py` |
| 表 | `Pearnly Assistant Session`、`Pearnly Assistant Tool Run`（Core，仅 System Manager 可读） |
| 页面 | `apps/pearnly_core/pearnly_core/www/assistant.{html,py}` → `/assistant` |
| 前端 | `frontend/assistant/`（产物 `pearnly_core/public/assistant/`，不入库） |
| 依赖 | `frappe.Dockerfile` 新增 `anthropic==1.8.0`（引擎目录登记的首家 SDK）；版本矩阵已登记 |
| CI | `development.yml`：`tests/test_assistant.py`、前端类型检查与构建；`build-production.yml`：构建前端、镜像内核对产物与 SDK 版本 |
| 架构守卫 | `check-architecture.py`：React 只允许在 `frontend/assistant` |

## 验证

| 版本 | 环境 | 检查 | 结果 |
| --- | --- | --- | --- |
| 工作树（未提交时） | 本机 macOS，Python 3 | `python3 tests/test_assistant.py`（13 项：约定、工具声明与定义同源、业务拒绝转错误结果、按密钥建 / 复用 / 更新 / 换密钥不串号、页面 Jinja、表权限） | 通过 |
| 同上 | 本机 | `python3 scripts/check-architecture.py`；`python3 tests/test_architecture_guard.py` | 通过 |
| 同上 | 本机 Node 22.23 | `frontend/assistant`：`tsc --noEmit`、`vite build` | 通过（主包约 0.9 MB，后续拆分） |
| 同上 | 本机浏览器，界面预览（真实前端 + 假后端，不连站点 / Anthropic，不进仓库） | 会话列表、历史回放、工具调用折叠、命令卡片「允许」后出结果与回复、发送新消息、手机尺寸 | 通过；首次装依赖时 assistant-ui 内部包漂到新版（tap 0.9.22）导致渲染死循环，改为以官方示例锁文件为基准安装（tap 0.9.4，间接依赖与官方一致）后正常 |
| 同上 | 本机 | `check-frontend-i18n --strict`、`i18n-build --check` 通过；`check-i18n --strict` 为只报告，新增英文提示缺译文（见下「待批文案」） | 见说明 |
| — | 真实产品站点 | 会话创建、收发、工具调用 | **未做**：需要测试用 Anthropic 密钥，以及运行环境决定（见下一步） |
| — | 生产镜像 | 共用层候选离线检查 | **未做**（合并前必须做） |

## 文案

按用户 2026-10-10 口径：官方、简短、能沿用官方就沿用，不加说明性文案。对话页外壳只有标题与「正在重新连接…」；标题先用 Pearnly，产品名等用户看到界面后再定。服务端提示为英文原文的简短陈述，译文随后补齐。assistant-ui 组件自带的英文提示（输入框占位、复制、重试等）后续统一三语化。

## 第一批之后补齐的界面（2026-10-10，同一分支）

按用户「界面要像 claude.ai、能沿用官方就沿用」逐项补齐，全部以 assistant-ui 官方组件与官方 Claude / Artifacts 示例为底：

| 项 | 做法 | 后端 |
| --- | --- | --- |
| 布局与观感 | 官方 Claude 示例样式：衬线正文、细边输入框、浅灰用户气泡、悬停才出现的操作；纯白底、浅灰选中；导航栏图标 + Pearnly；问候语 + 本站产品形象（宣传页 `scenes.badge` 生成，眼睛跟随与眨眼照搬 `site.js` / `style.css`，`scripts/build-assistant-brand.py`） | `pearnly_core.shell.describe` 取用户与产品 |
| 附件 | 「+」、拖拽、粘贴；气泡里显示文件卡片，不显示给智能体看的沙箱说明 | `assistant/files.py`：校验类型（照片 / PDF / 表格 / 文档）与 30 MB 上限、HEIC 转 JPEG（复用 `pearnly_core.desk.heic`）、存 Anthropic 文件库并只读挂进会话 |
| 结果面板 | 智能体写到 `/mnt/session/outputs/` 的文件在右侧预览 / 下载；网页放无同源权限的沙箱 iframe，图片、PDF 内联，文本表格原文，其余下载；新结果自动打开 | 下载前核对会话归属与文件归属；只有图片、PDF 可在本站内联，网页 / SVG 一律按下载给出 |
| 侧栏 | 加星标（「已加星标」分组）、行内重命名、删除；新对话首条消息后立即显示标题；底部用户菜单「退出登录」走原生统一登出；手机宽度选完对话自动收起 | `Pearnly Assistant Session` 新增 `starred`（需 `bench migrate`） |
| 思考与工具步骤 | 「正在思考…」、「N 个步骤」、读写 / 搜索等用平常说法；Pearnly 工具显示自己的名称 | 工具声明新增 `label`（英文原文进声明它的 App 的术语表），`tool_labels` 按用户语言给出 |

逐字流式仍按 ADR-019 放到真实环境接通、实时推送验证之后。
界面均在本机「界面预览」（真实前端 + 假后端，不进仓库）里点过：拖入附件、发送、右侧面板自动打开并预览网页 / 表格、加星标、重命名、用户菜单、手机宽度。

## 引擎与密钥框架（2026-10-10，同一分支）

用户：「请你先把密钥框架搭建好，任何可扩展可维护的框架不要写死」。原先管理后台字段叫「Anthropic API Key」、开通步骤写死 `sk-ant-` 前缀、站点直接调 Anthropic SDK，改为：

| 层 | 现在 |
| --- | --- |
| 引擎目录 | `pearnly_core.assistant.engines.ENGINES`：代码、显示名、适配器、密钥格式、默认模型、镜像依赖。全平台唯一登记处，纯数据 |
| 站点 | 会话、文件、工具结果只经适配器接口；会话表记 `engine_code`，换了引擎只列当前引擎的会话；对话页只认统一对话事件 |
| 管理后台 | 开户单与租户表单：「Assistant Engine」（下拉，选项取自引擎目录）、「Assistant Model」（留空用默认，输入框提示默认模型）、「Assistant API Key」；保存时按目录校验；同长度换密钥也能识别为变更并重排推送 |
| 开通步骤 | `set_assistant` 载荷带引擎、模型、密钥；站点写配置前按同一目录再校验，只写配置不重启 |
| 守卫 | 离线测试逐家检查适配器接口完整、SDK 已在镜像锁定；共用层、控制面、开通步骤、表单定义里出现厂商名或密钥前缀即失败 |

新接一家：`engines/` 加适配器模块 + 目录一行 + 镜像锁定 SDK；后台、站点、开通步骤无需改动。

## 测试机试用后的修正（2026-10-10，同一分支）

| 问题 | 证据 | 处理 |
| --- | --- | --- |
| 回复「巨慢」 | 引擎侧「我是谁」3 秒、看图 6 秒答完；「列出事件」单次偶发 33–44 秒（测试机、本机都复现），而页面每 1.2 秒靠它取回复 | 改为事件流推送 + 逐字预览（见 ADR-019 第 8 条）；「列出事件」单次 6 秒超时自动重发；客户端按进程复用；首条消息不再改远端标题。测试机服务端实测：发送返回约 1 秒，回复约 2 秒推到 |
| 字体奇怪 | 正文用衬线体，中文落到宋体、数字为老式数字 | 正文与问候改用系统无衬线（同输入框） |
| 右侧没结果 | 「生成一个表格」时智能体直接在对话里回 Markdown 表格，没有产出文件 | Claude 适配器给智能体加产出约定：表格、报表、文档写成文件放结果目录，对话只给简述 |
| 模型手填易错 | — | 引擎目录登记可选模型（建智能体实测接受：opus-5-5 / fable-5-1 / sonnet-5-5 / haiku-5-5），后台改下拉 |
| Haiku 5.5 长上下文计价 | 官方价格页：单次输入超过 100,000 token 整次 5 倍；引擎自带压缩约 1M 才触发、不可配置 | 站点自己接续（见 ADR-019 第 9 条）；测试机实测：接续后新上下文 4,811 token，仍记得前文 |

## 对话页按 claude.ai 重做（2026-10-10，同一分支）

用户：「对话前端体验真的很不满意，整体需要重构」。截图问题：每个工具调用一张大卡片（bash 铺开整段脚本、黑底终端），结果文件只在右侧且 Excel「不支持预览」。

| 项 | 现在 |
| --- | --- |
| 步骤 | 思考与工具调用连续的一段收成一行（进行中显示当前动作，完成显示「N 个步骤」），点开一行一步，再点某一步才看到命令 / 内容与输出（浅色小框）。所有工具共用 `tool-uis/steps.tsx`；等批准的那一步不收进组 |
| 文件 | 回复期间产出的文件在该条回复末尾显示卡片（按文件时间落在本条与下一条消息之间），点开在右侧 |
| 右侧预览 | Excel 由服务端（镜像自带 openpyxl）读成表格显示，多张表可切换；其余不能预览的给下载 |
| 附件 | 本页上传的图片在气泡里显示缩略图 |
| 指令 | 表格写算好的值，用户要求时才用公式 |
| 思考强度 | 所有模型 `low`（四个可选模型建智能体实测接受） |
| 清理 | 删除不再使用的 assistant-ui 示例组件（工具卡片、工具组、思考块、diff、代码高亮等） |

测试机同步注意：带本地构建的前端必须用 `PEARNLY_TESTENV_LOCAL_SPA=1`。本分支此前两次误写成 `LOCAL_SPA=1`，前端没有同步过去（字体、推送、逐字显示当时都未生效），已改正并回读测试机实际引用的打包文件。

## 智能体入口 ai.pearnly.com（2026-10-10，同一分支）

用户口径：不是新产品、不单独开户，用开户发的同一套账号；登录后跳到所属公司的实际域名 `/assistant`；两步登录（用户名或邮箱 → 密码）。

| 部分 | 位置 / 做法 |
| --- | --- |
| 登记 | 计费服务新表 `Pearnly Tenant Site`（站点、实际账号池签发方、开户账号名 / 邮箱 / 身份），开户计划的 `register` 每个部署写一次（可重复）；加的成员沿用 `Pearnly Member Identity` |
| 查询 | `assistant_entry.billing.resolve`：计费站点上唯一新增的放行地址，专用令牌（根密钥派生），只答「账号在哪个池」（登录前，不透露公司）与「已验证身份能进哪些站点」（登录后） |
| 入口站点 | 计费服务那套程序上的独立站点（`pearnly_assistant_entry`），只开放入口页与 start / callback / choose 三个接口，其余 403；页面由 `scripts/build-ai-entry.py` 生成 |
| 登录 | 两个账号池各一个公开客户端 `pearnly-ai`（PKCE，回调只允许 ai / testai），ai 专用主题 `pearnly-ai`（密码页、找回密码等沿用 pearnly 主题模板换外壳）；回调后交给站点自己的统一登录口，账号池里已有登录状态不再要密码 |
| 设计 | 宣传页底色点阵与记号笔；宣传页小人 + Tomo 云朵（原薄荷绿对话泡已换掉）；联系方式与条款隐私两步都有 |

验证：离线测试 15 项；本机临时 Keycloak 看过密码页、输错提示、找回密码页；测试机 testai.pearnly.com 首页 200、后台 / 登录页 / 通用接口 403、填账号后正确给出 ERP/DMS 池登录地址；testcowork 的测试登记读回正确。
生产 Keycloak（用户已批准）：两个池已加 `pearnly-ai` 客户端并读回；ai 主题临时拷进运行中的容器（未重启，无停机），正式挂载随下次部署（compose 已改）。

限制：账号池拆分前开的测试租户（testcowork）登记要手工写；正式租户的登记在本批部署后重跑开户计划的登记步骤补齐。

## Cowork 第一批工具（2026-10-10，同一分支）

`apps/pearnly_cowork/pearnly_cowork/assistant/tools.py`（hooks `pearnly_assistant_tools`）：小助手状态与账套（总数 + 前 30 个，可按公司名搜）、刷新账套、列单据、推送到 Express（一次最多 20 张，已推过的不再推，逐张提交）、查推送结果、文件夹定时任务 / 立即执行 / 执行记录。都以当前登录用户调现有服务。测试机用真实测试用户跑过只读工具；真推送未在对话里跑过（会写测试账套，留给用户试）。

## 产品名 Tomo 与云朵形象（2026-10-10，同一分支）

用户定名 **Tomo**（Pearnly 是公司名）。形象从三个候选里选了云朵：空手、眼睛跟鼠标、会眨眼、隔几秒眯眼张嘴笑一下，悬停 / 点它也笑；源头 `website/scenes.py`（`TOMO_CLOUD` / `TOMO_SPARK`、`costume("ai")`），登录页、对话页由脚本生成。
应用图标（PWA、桌面端、托盘）用户指定白底黑线云朵脸：`scripts/build-assistant-brand.py` 的 `icon()` 生成 `frontend/assistant/src/brand/icon.svg`；PNG（192 / 256 / 512）与 `companion/assets/tomo.ico` 用 Chrome 无头模式渲染该 SVG、Pillow 合成 ico（一次性产物，改图标时重做）。
登录页 / 对话页左上角、页面标题、PWA 名称都改成 Tomo；登录主题的生产 Keycloak 文件随下次部署更新。

## 桌面端：小助手并入 Tomo（2026-10-10，同一分支）

用户口径：「做成和 Claude 桌面端一样」，小助手以后融入桌面端。做法：在小助手程序（`companion/`，PySide6）里加 Tomo 窗口，不另起程序。

| 部分 | 位置 / 做法 |
| --- | --- |
| 窗口 | `gui_tomo.TomoWindow`：QtWebEngine 打开同一个对话页；登录状态存本机（强制持久 Cookie）；下次回到上次的 `/assistant`；只开 `*.pearnly.com` 的 https 页面，其他链接 / 新窗口交给系统浏览器，本地文件不开（`tomo_links.py`）；右键只留编辑项；关窗口藏到托盘（第一次提示一次） |
| 快捷输入条 | `QuickAsk` + `tomo_hotkey.py`（Windows RegisterHotKey，Ctrl+Alt+Space，被占用就不注册）：回车后窗口开新对话发出去（对话页 `window.tomo.ask`，`frontend/assistant/src/lib/desktop.ts`） |
| 通知 / 下载 | 对话页在窗口不在前台时回复完成发网页通知（桌面端自动授权，浏览器只在已授权时弹），桌面端转成系统通知，点了回到窗口；下载存「下载」文件夹，不覆盖同名，完成后通知、点了在资源管理器选中 |
| 托盘 | 左键 / 「打开 Tomo」打开窗口；原主窗口改名「连接与设置」；没配对 Express 也能启动（托盘显示「未连接 Express」）；再次打开程序经本地管道把 Tomo 拿到前面（`instance_channel.py`） |
| 内存 | 网页内核只在第一次打开 Tomo 时加载，开机自启不加载 |
| 打包 | onefile → 文件夹（带网页内核）；安装包名称、快捷方式改 Tomo（安装目录、AppId、自动更新地址不变，原地升级）；静默自动更新后以 `--background` 重启，不弹窗打断人；版本 1.4.0；分支的 Companion checks 每次真打一次包并核对网页内核在包里，安装包作为运行附件（7 天、不发布） |

验证：小助手全量测试 605 项通过（本机 Python 3.14 + PySide6 6.11.1）；本机离屏打开 Tomo 窗口，读回页面为 testai 的 Tomo 登录页；快捷输入条截图正常。Windows 真机（测试机）试装待做。
限制：与 Express 的自动配对（登录 Tomo 后不再贴配对码）未做；Mac 版未做；网页内核随我们发版更新（只开 Pearnly 自己的页面，降低风险）。

## 设置页取代小助手窗口（2026-10-10，同一分支）

用户口径：小助手前端全部重做，放在左下角菜单的「设置」里，参考 Claude。
- 对话页 `components/settings.tsx`：左下角菜单「设置」或 Ctrl+, 打开；左边分栏「常规」（对话文字大小、语言），桌面端多出「此电脑」：「连接」（Express 账套与「更换…」、成员状态 / 更换配对码 / 暂停 / 移除、网页可选的文件夹）和「系统」（开机启动、快捷提问、版本与更新、日志）。移除不再二次确认。
- 桌面端：`tomo_bridge.py`（QWebChannel，对象 tomoDesktop，只在 Tomo 对话页应答；慢操作后台做、成员变化回界面线程重启取任务线程）+ `desktop_settings.py`（本机操作）+ `pairing_flow.py`（原配对窗里的配对逻辑）。原小助手主窗口、配对窗、文件夹框（`gui_profiles` / `gui_pairing` / `gui_folders`）删除，测试迁到 `test_desktop_settings.py` / `test_tomo_bridge.py`；托盘「连接与设置」打开设置的「连接」。
- Tomo 窗口去掉「文件 / 视图」菜单栏，快捷键保留（Ctrl+N / Ctrl+, / Ctrl+R / 缩放）；自动更新只在有人正用 Tomo 窗口（在最前面）时等待。
- 生产登录服务的 Tomo 主题已更新（密码页原来还显示 Pearnly）：主题样式 / 脚本文件名带内容指纹，避免浏览器缓存旧样式；更新时重启登录服务约 49 秒。

验证：小助手测试 599 项（本机）、Windows CI 通过；预览页用假数据点过设置各栏、配对失败提示；测试机装上新包，Ctrl+, 打开设置，「连接」读到测试机真实的成员（已连接）与 Express 账套（17 个年度）。

## 产品名 CC、入口域名 fatcc.com（2026-10-10，同一分支）

- 产品名改为 **CC**（Tomo 作废）；登录页左上角只放 CC 图标（白字带胡须原版），对话页侧栏不放图标；浏览器标签图标 16–48 像素用去胡须简化版。图标源文件 `frontend/assistant/src/brand/cc-icon*.svg`（另一窗口定稿，橙底 #F28C28）。
- 登录页不放猫（2026-10-10 晚用户定：探头的猫、睡觉的猫视频都删，图片 / 视频 / 抠图脚本一并删除，入口站点不再放行静态目录）。
  左上角是 CC 图标（橙底白 cc 带胡须）；CC 的形象「吐司胖橘」（用户在 Vikeno logo design 窗口选定，原稿 `website/static/cc/source/cc-loaf.svg`，
  画法 `website/scenes.py` 的 `cc_loaf`）站在底部那排小人最右边：眼睛跟鼠标、眨眼，隔一会儿 / 悬停 / 点一下眯眼张嘴笑，尾巴摆、铃铛晃、泡泡里的勾反复画出来。
- 网页「下载小助手」按钮改为「下载 CC」（用户 2026-10-10 晚定）。
- 登录框去掉「还没有账号？」「联系我们开通账号或预约演示」两行；设置和对话页的选中 / 悬停统一用中性灰。
- **入口域名 fatcc.com**（用户在 Cloudflare 注册）：
  - Cloudflare：本机令牌 pearnly-testenv-switch 加了 fatcc.com 的 DNS 编辑权限（用户确认后在其 Chrome 里保存）；DNS `fatcc.com A 生产服务器`、`www CNAME fatcc.com`，只解析不代理（同 pearnly.com）。
  - 服务器：`infrastructure/production/fatcc.conf` 装为 `/etc/nginx/conf.d/pearnly-fatcc.conf`；certbot 证书 fatcc.com + www（到 2027-01-08，自动续期）；http→https、www→fatcc.com；网页转计费服务 8184 上的入口站点 fatcc.com，睡觉猫视频目录转 Cowork 前端 8181。
  - 生产登录服务：`configure-ai-entry.py --origin https://fatcc.com --origin https://testai.pearnly.com --tenant-logout` 已执行并读回（入口客户端两个账号池；6 个租户客户端追加退出回 fatcc.com，只追加）。
  - 代码：登出回跳、开户写入的退出地址、桌面端入口与允许打开的域名都改为 fatcc.com；测试入口仍是 testai.pearnly.com（其静态文件经测试隧道转测试栈 Cowork 前端）。
  - **未完成**：入口站点 fatcc.com 还没建（入口程序在任务分支，未进生产镜像），现在访问 fatcc.com 证书正常、页面 404；随本批合入 main 部署时在计费服务上建站点 fatcc.com 并写站点配置、补登记。

## 按账号连接这台电脑，不再手抄配对码（2026-10-10，同一分支）

用户定：电脑端用账号登录就自动连上，电脑上设好的文件夹自动给这个账号的网页端用。

- 服务端 `pearnly_cowork.companion.api.register`（`credentials.register`）：登录的管理员给「这台电脑」申请密钥。
  装了 Express 的连 Express（没有 Express 通道就建一条），没装的连 MR.ERP（只读文件夹），再没有退回 Express；
  无可连的回 `no_connection`。权限、签发人、失权即失效都与原配对码同一判据；这条连接已有电脑连着时回 `in_use`
  不抢，用户点「改用这台电脑」带代数重发，旧电脑立即断开。旧配对码照常有效。
- 对话页（桌面端里、Cowork 站点）打开时自动申请并把密钥直接交给本机桌面端静默配对（`lib/desktop.ts`
  `useAutoConnect` / `connectComputer`），密钥不显示。一台电脑只连一家公司：已连别家时不自动改，设置里点
  「改连 <本站>」才断开原来的全部成员再配对；同一条连接重连时换原成员的密钥，不多出成员。
- 设置「连接」改单页：公司一行（站点、Express / MR.ERP、状态，⋯ 暂停 / 断开连接）、Express 数据文件夹、
  网页可读取的文件夹（记在 Express 成员上，没有才记在 MR.ERP 上）；去掉配对码输入和单独的「文件夹」分栏。
- 网页「连接 Express」/「连接小助手」弹窗：连接状态 +「下载小助手」+「安装后用本账号登录即可连接。」，
  开着每 3 秒问一次在不在线；不再生成或显示配对码（词条 `intake.agent.key/copy/reset/resetConfirm` 已从术语表删除）。
- 测试：`tests/test_cowork_companion_register.py`（已登记候选离线检查）、`companion/tests/test_desktop_settings.py`。

- 浏览器里打开的设置（没有桌面端）在 Cowork 站点多一栏只读「连接」（用户 2026-10-10 晚：客户端才能变更，网页端只能看）：
  连着哪台电脑（电脑名、Express / MR.ERP）、在不在线（Express 看心跳，MR.ERP 看最近同步文件夹）、网页可读取的文件夹；
  没有电脑时给「下载 CC」。数据来自 `pearnly_cowork.folder_intake.api.computers`（与定时任务页同一权限，只读）。

## 已知限制

- 实时进度是轮询，回复整段到达；改站点实时通道转发需先做并发验证（ADR-019）。
- 工具由浏览器提醒服务端执行，页面关闭则不执行；做「下班用手机派活」之前要改成引擎回调触发（作为适配器可选能力）。
- 同一引擎换成另一个账号的密钥时，旧会话在新账号里读不到（换引擎则旧会话不再列出）。
- 工具执行记录若停在 `running`（执行中进程崩溃），当前不会自动重试，后续加超时回收。

## 下一步

1. 本地真实跑通：需要 (a) 一把测试用 Anthropic 密钥（写入测试站点配置，不进仓库）；(b) 运行环境——本机共用样板挂的是主目录源码，从本工作树接管需用户同意；或用 Windows 测试环境 `testenv.sh live`（共用层 + 依赖变化需换镜像）。
2. 控制面：开户单 / 租户后台录入与更换密钥（Password 字段），新增开通步骤推到各产品站点并重启生效；同 LINE 凭据链路。
3. 附件（拍照 / 文件）、回调触发工具执行、Service Worker 与推送通知。
4. 各产品第一批业务工具（只调现有服务）；桌面端由小助手内嵌；LINE 桥接。

## 收尾登记

- 工作树 `/Users/skin/pearnly-agent-foundation`、本地分支 `pearnly/agent-foundation`、远端同名分支：**交接保留**，所属本任务，用途为继续桌面端与试用；解除条件：合入 `main` 后按收工清理删除。
- 本机 `ant` 命令行（Homebrew）：为本任务装，后续配置智能体可能用到，保留；不需要时 `brew uninstall ant`。
- LobeHub 评估环境（容器、卷、镜像、`~/lobehub-eval`）与官方示例演示目录：已删除。
