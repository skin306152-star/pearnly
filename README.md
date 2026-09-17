# pearnly

Pearnly 统一开户与隔离平台的开发仓库。**当前状态：只有骨架，业务代码一行都还没写。**

## 这个仓库是什么

承载新平台的代码：以 Frappe 原生 Site 作为产品租户载体，公司 × 已开通产品各建独立 Site，WeKan 作为 Cowork 内部功能。目标与合同见计划包，本仓库不复制计划正文。

计划包在仓库之外，不纳入本仓库版本控制：

- 实施交接：`/Users/skin/Developer/Pearnly/pearnly-platform-plan-v2-20260917/hardening/10-IMPLEMENTATION-HANDOFF.md`
- 部署决定：同目录 `08-DEPLOYMENT-DECISION.md`
- 施工计划：同目录 `implementation/platform.md`
- 验收矩阵：同目录 `04-acceptance.md`

## 与现有仓库的关系

本仓是**新平台代码的唯一权威位置**。其余仓库在本项目推进期间作为来源参考：

| 仓库 | 角色 |
|---|---|
| `~/Developer/Pearnly/pearnly-app` | 前端、算法、适配器与功能基线来源 |
| `~/pearnly-erp` | ERP 资产与本地 Frappe 样板约定来源 |
| `~/wekan` | Cowork 看板上游源码 |
| `~/Developer/Pearnly/pearnly-companion` | 配对与任务协议 |

同一份业务逻辑不在两个仓库同时维护。归属与复用方式见 `docs/adr/`。

## 目录

- `apps/` Frappe Apps
- `infrastructure/` 本地 Compose、Keycloak、部署定义
- `scripts/` 开通、升级、备份恢复脚本
- `docs/adr/` 架构决策记录

## 状态

未实现、未部署、未采购。没有线上切换，没有数据清理授权。
