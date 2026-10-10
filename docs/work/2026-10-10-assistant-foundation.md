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

- 工作树 `/Users/skin/pearnly-agent-foundation`、本地分支 `pearnly/agent-foundation`：**交接保留**，所属本任务，用途为继续第二批；解除条件：合入 `main` 后按收工清理删除。远端分支：不存在（未推送）。
- 本机 `ant` 命令行（Homebrew）：为本任务装，后续配置智能体可能用到，保留；不需要时 `brew uninstall ant`。
- LobeHub 评估环境（容器、卷、镜像、`~/lobehub-eval`）与官方示例演示目录：已删除。
