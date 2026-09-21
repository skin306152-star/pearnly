# pearnly

Pearnly 统一开户与隔离平台的开发仓库。**当前处于业务迁移阶段：按 2026-09-19 用户确认，已跑通的是 DMS；其他产品不能据此标为完成。** 实现、历史验证与待验范围见 [开发现状](docs/development/current-state.md)。

开发从 [CONTRIBUTING.md](CONTRIBUTING.md) 开始；开发 Agent 遵守 [AGENTS.md](AGENTS.md)。这些约定仅服务于开发，不是产品功能。

## 这个仓库是什么

承载新平台的代码：以 Frappe 原生 Site 作为产品租户载体，公司 × 已开通产品各建独立 Site。目标与合同见计划包，本仓库不复制计划正文。

计划包在仓库之外，不纳入本仓库版本控制：

- 实施交接：`/Users/skin/Developer/Pearnly/pearnly-platform-plan-v2-20260917/hardening/10-IMPLEMENTATION-HANDOFF.md`
- 部署决定：同目录 `08-DEPLOYMENT-DECISION.md`
- 施工计划：同目录 `implementation/platform.md`
- 验收矩阵：同目录 `04-acceptance.md`

## 业务范围

本仓承载全部业务代码，是唯一权威位置。四块内容：

| 块 | 内容 |
|---|---|
| Cowork | Cowork 产品：录入、识别记录、推送日志、对账、盘点、主数据与小助手；团队协作看板不再迁移（后期自行开发） |
| ERP | ERP 门户产品：采购、销售、库存、外部 ERP 推送 |
| DMS | DMS 产品 |
| Frappe 层 | Frappe 原生框架之上的自有层：Site 运行身份、授权上下文、任务与审计 |

**管理后台**：现在的 `/admin` 是功能参考与迁移来源；新后台用 Frappe 实现，挂在控制 Site 上，按 `pearnly_control` 单独装，不是把旧后台原样搬过去，也不装进任何租户 Site。

**管理后台的运行位置**：控制 Site 只在开发机（本机）运行，不进云服务器，也不计入服务器资源预算。它只承担开户与授权管理，仅本人使用。云上只跑三产品 Site 与 Keycloak。

## 与其他仓库的关系

下面这些仓**都不是业务**，只作为来源或组件：

| 仓库 | 角色 |
|---|---|
| `~/Developer/Pearnly/pearnly-app` | 现有业务实现，当前功能基线与前端、算法、适配器来源 |
| `~/pearnly-erp` | Frappe 建站资产与 App 目录来源 |
| `~/Developer/Pearnly/pearnly-companion` | 配对与任务协议 |

同一份业务逻辑不在两个仓库同时维护。归属与复用方式见 `docs/adr/`。

## 目录

- `apps/` Frappe Apps
- `infrastructure/` 本地 Compose、Keycloak、部署定义
- `scripts/` 开通、升级、备份恢复脚本
- `docs/adr/` 架构决策记录
- `docs/development/` 开发流程、验证、Agent 和记录规范
- `docs/work/` 按工作包保存的开发与交接记录

## 状态

代码和阶段记录已有实质内容，不能再按空骨架处理。历史部署决定和阶段记录不代表当前生产状态，也不构成本次采购、生产切换、数据删除或外部写入授权；执行前核对实际环境与当前授权。
