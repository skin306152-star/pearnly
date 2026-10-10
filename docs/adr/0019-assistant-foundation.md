# ADR-019：Pearnly 智能体底座（Claude Managed Agents + assistant-ui，三产品共用）

- 状态：已采纳（2026-10-10，第一版骨架在分支 `pearnly/agent-foundation`，未合并、未部署）
- 决策依据（用户 2026-10-10，本会话）：
  - 「做一套属于 pearnly 的智能体，模型用 Claude Managed Agents」；「网页端用户 PWA 这样用，然后桌面端也要有」，前期不做应用商店 App；LINE 是锦上添花。
  - 前端框架在 dsh / LobeHub / LibreChat / Ant Design X / Lobe UI 等对比后，「那就 assistant」。
  - 「这个产品不是专门给 Cowork 用的，ERP 和 DMS 也要用；登录全部采用后台开户的账号密码登录，沿用我们的租户隔离、用户权限、套账隔离、数据隔离等大框架」。
  - 「API 密钥在租户管理后台填入，和开户时填，可修改更换，不用用户填」。
  - 「小助手到时候也要融进框架的 App 里面」：桌面端由现有 Windows 小助手升级承载（后续批次）。
- 关联工作包：[2026-10-10 智能体底座](../work/2026-10-10-assistant-foundation.md)
- 与 [ADR-005](0005-frontend-architecture.md) / [ADR-007](0007-frontend-skin.md) 的关系：本 ADR 为「智能体对话页」开一个**限定范围的例外**；其余产品 Web 仍是原生 Desk。

## 问题与约束

- 要一个像 claude.ai 的对话入口：手机（PWA，不上架）、电脑浏览器、桌面端打开的是同一段对话，并能调用 Pearnly 的业务（识别、推送、对账、查询等）。
- 三个产品都用；登录、租户、用户权限、套账与数据范围必须沿用现有体系，不另造账号或权限。
- 原生 Desk 没有流式对话、工具卡片、批准按钮、断线续接这类对话控件，在 Desk 里自建等于重做通用 UI（ADR-007 明确不允许）。
- 不新增常驻 Node 服务，不新增固定月费；空闲零花费。

## 选项

1. **LobeHub / LibreChat / Open WebUI 整套产品**：自带智能体运行方式，与 Managed Agents 二选一；自有账号体系与数据库；LobeHub 的衍生作品需商业授权。排除。
2. **dsh（DeepSeek Harness）的壳**：单用户本机设计（无服务器级认证、数据在本机目录、桌面端本地运行），官方声明为未经安全审计的预览版。多租户、PWA、同步都要大改。排除。
3. **Desk 内自建对话页**：违反 ADR-007 的通用 UI 规则，手机体验差。排除。
4. **assistant-ui（MIT）+ Anthropic 官方 Managed Agents 示例（MIT）+ Frappe 后端**（采用）：对话控件现成；会话本身存在 Managed Agents，天然多端同步；后端留在 Frappe，权限与隔离不变。

## 决定

1. **落位：共用层 `pearnly_core.assistant`**（与身份、OCR 同类的跨产品底座）。产品只**声明**自己的工具：`hooks.py` 里 `pearnly_assistant_tools = ["<app>.<module>.TOOLS"]`，每条声明含 `name / description / input_schema / handler`（ADR-009 的「产品自己声明」）。
2. **入口**：每个产品的租户站点提供 `/assistant`（`pearnly_core/www/assistant`），登录就是本站会话（后台开户账号经 Keycloak 登录）；没有 `pearnly_product` 的站点（本机管理后台）拒绝。
3. **隔离**
   - 租户：站点即租户。会话归属记在本站表 `Pearnly Assistant Session`，接口先按「本站 + 当前用户」核对归属再碰 Anthropic；会话列表只读本站表。Anthropic 侧会话带 `pearnly_site / pearnly_product / pearnly_user` 元数据，仅作追溯。
   - 用户权限、套账、数据范围：工具在服务端以当前登录用户身份执行，只调用产品已有服务（沿用其校验），业务拒绝作为 `is_error` 结果交回模型。工具名和参数由服务端从会话里读回，不信浏览器。
   - 幂等与审计：每次工具调用按唯一 `tool_use_id` 记一条 `Pearnly Assistant Tool Run`，同一调用只执行一遍。
4. **密钥与智能体配置**：`pearnly_assistant_api_key` 由**租户管理后台**在开户时写入、之后可改可换，经开通执行器推到站点（与 LINE 凭据同一条链路，后续批次实现）；用户不接触。智能体与运行环境**不需要人工配置**：站点按「密钥指纹 + 定义指纹」在该密钥所属账号中查找、缺则建、定义变了则更新（`provision`），换密钥 / 换账号无需手工步骤，同一账号同一产品只有一个智能体。
5. **前端**：`frontend/assistant/`（Vite + React 19 + assistant-ui，Tailwind 4），构建为静态文件放 `pearnly_core/public/assistant/`，由站点提供；**不新增 Node 服务**。PWA 清单随产物发布。会话逻辑与工具卡片改自 Anthropic 官方示例（`NOTICE.md`）。
6. **例外范围**：React 只允许在 `frontend/assistant/`，只服务 `/assistant` 这一个入口；不得扩展成第二套普通产品 Web。
7. **模型**：`claude-opus-5-5`，effort `medium`；内置沙箱工具开启（自动批准策略），网页搜索 / 抓取关闭，业务只经 Pearnly 工具。

## 后果与验证

- 第一版实时进度用**轮询**（会话运行中约 1.2 秒一次，空闲 6 秒，页面在后台暂停）：回复整段到达，不逐字出现。改为站点实时通道转发事件流是待验证项，验证通过前不在 gunicorn 里挂长连接。
- 浏览器只负责提醒服务端「有待执行的工具」；页面关闭时工具不会被执行。改为 Anthropic 回调（`session.status_idled` webhook）触发服务端执行，是做「下班用手机派活、做完通知」的前置项。
- 依赖：镜像新增 `anthropic==1.8.0`（Python）；前端依赖逐个锁定版本（`frontend/assistant/package-lock.json`）。改了共用层，合并前按「共用层候选检查」在生产镜像跑完整离线检查。
- 成本：模型 token 按量 + 会话运行时间 0.08 美元/小时，空闲不计费；无固定月费。
- 待办（后续批次，各自记录）：控制面密钥录入与推送；附件（拍照 / 文件）接本站文件存储；对话页文案与组件内英文提示的三语化（新文案先经用户批准）；Service Worker 与推送通知；桌面端由小助手内嵌；LINE 桥接；各产品第一批业务工具。
- 回退：本功能只有新入口与新表，不改现有业务；撤掉入口与 hooks 即停用，不影响其他页面。
