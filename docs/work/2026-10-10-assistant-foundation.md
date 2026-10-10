# 2026-10-10 Pearnly 智能体底座（第一批：骨架）

- 决策：[ADR-019](../adr/0019-assistant-foundation.md)
- 状态：**进行中**（第一批骨架本地完成；未推送、未合并、未部署、未在真实站点跑通）
- 仓库 / 分支：本仓 `pearnly/agent-foundation`，基线 `c3a7622c12e8a13895a8498f254faffa64a31ddb`（当时的 `origin/main`）
- 工作树：`/Users/skin/pearnly-agent-foundation`

## 目标与完成条件

三产品共用的智能体：网页（PWA）/ 桌面 / 手机打开同一段对话，模型与执行用 Claude Managed Agents，登录与租户、权限、套账、数据隔离沿用现有体系。

第一批只搭骨架：共用层后端、对话页前端、站点入口、CI 与镜像依赖、架构文档。完成条件：离线测试、类型检查、前端构建、架构检查通过；在一个真实产品站点上用测试密钥完成「新建会话 → 发消息 → 收到回复 → 调用 `pearnly_whoami` 工具」（**尚未做**，见下）。

本批明确不做：控制面密钥录入与推送、附件、Service Worker / 推送通知、桌面端内嵌、LINE 桥接、产品业务工具、文案三语化。

## 落位

| 部分 | 位置 |
| --- | --- |
| 纯约定 | `apps/pearnly_core/pearnly_core/assistant/contracts.py` |
| 配置（产品、密钥） | `assistant/config.py`；站点键 `pearnly_assistant_api_key` 已登记在 `architecture-policy.json` |
| 智能体定义 / 按密钥自动建 | `assistant/definition.py`、`assistant/provision.py` |
| 工具声明与派发 | `assistant/tools.py`；产品经 `hooks.py` 的 `pearnly_assistant_tools` 声明 |
| 会话归属与读写、接口 | `assistant/sessions.py`、`assistant/api.py` |
| 表 | `Pearnly Assistant Session`、`Pearnly Assistant Tool Run`（Core，仅 System Manager 可读） |
| 页面 | `apps/pearnly_core/pearnly_core/www/assistant.{html,py}` → `/assistant` |
| 前端 | `frontend/assistant/`（产物 `pearnly_core/public/assistant/`，不入库） |
| 依赖 | `frappe.Dockerfile` 新增 `anthropic==1.8.0`；版本矩阵已登记 |
| CI | `development.yml`：`tests/test_assistant.py`、前端类型检查与构建；`build-production.yml`：构建前端、镜像内核对产物与 SDK 版本 |
| 架构守卫 | `check-architecture.py`：React 只允许在 `frontend/assistant` |

## 验证

| 版本 | 环境 | 检查 | 结果 |
| --- | --- | --- | --- |
| 工作树（未提交时） | 本机 macOS，Python 3 | `python3 tests/test_assistant.py`（13 项：约定、工具声明与定义同源、业务拒绝转错误结果、按密钥建 / 复用 / 更新 / 换密钥不串号、页面 Jinja、表权限） | 通过 |
| 同上 | 本机 | `python3 scripts/check-architecture.py`；`python3 tests/test_architecture_guard.py` | 通过 |
| 同上 | 本机 Node 22.23 | `frontend/assistant`：`tsc --noEmit`、`vite build` | 通过（主包约 1 MB，后续拆分） |
| 同上 | 本机 | `check-frontend-i18n --strict`、`i18n-build --check` 通过；`check-i18n --strict` 为只报告，新增英文提示缺译文（见下「待批文案」） | 见说明 |
| — | 真实产品站点 | 会话创建、收发、工具调用 | **未做**：需要测试用 Anthropic 密钥，以及运行环境决定（见下一步） |
| — | 生产镜像 | 共用层候选离线检查 | **未做**（合并前必须做） |

## 待用户批准的新文案

服务端提示（英文原文，待批后补 zh / th / ja）：`The assistant is only available on product sites.`、`The assistant is not set up for this site yet.`、`Not signed in`、`Conversation not found`、`Tool call not found`、`This tool is not available on this site.`、`The request was refused.`。

对话页外壳（`frontend/assistant/src/i18n.ts`，已拟三语）：标题「Pearnly 助手 / ผู้ช่วย Pearnly」、欢迎语「说说你要办什么。」、「临时出错，正在重试：」、「正在重新连接…」。assistant-ui 组件内仍有英文按钮提示（如输入框占位、复制、重试），后续批次统一三语化。

## 已知限制

- 实时进度是轮询，回复整段到达；改站点实时通道转发需先做并发验证（ADR-019）。
- 工具由浏览器提醒服务端执行，页面关闭则不执行；做「下班用手机派活」之前要改成 Anthropic 回调触发。
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
