# P0 状态

更新时间：2026-09-17。全程只读：没有修改任何来源仓库、没有改线上配置、没有删数据、没有采购、没有部署。

## 已完成

| 编号 | 内容 | 记录 |
|---|---|---|
| P0.01 | 四仓基线与漂移核对 | [01-source-baseline.md](01-source-baseline.md) |
| P0.02 | 现网只读盘点（入口、Cloud Run、任务、存储、VM、边缘） | [02-live-inventory.md](02-live-inventory.md) |
| P0.03 | 路由归属表第一版（732 条按前缀分产品） | [03-route-ownership.md](03-route-ownership.md) |
| P0.04 | 范围确认与不迁移清单（含依赖核对） | [04-scope-and-drop-list.md](04-scope-and-drop-list.md) |
| P0.05 | 数据表归属分类（176 张，逐张对齐校验通过） | [05-table-ownership.md](05-table-ownership.md) |
| P0.06 | 四个入口的代表流程 | [06-representative-flows.md](06-representative-flows.md) |
| — | ADR-001 原生 Frappe Site、ADR-002 部署目标 | [../adr/](../adr/) |

## 关键结论

1. **范围**：只要 Cowork、ERP、DMS 三个产品 + 租户管理。其余九项功能不迁移。
2. **会计整套不要**，采购与销售只记单不做账。切法是四个挂点（`services/accounting/hooks.py`）改为空动作，采购销售主流程不动。
3. **对账独立保留**，不读会计账簿（已核对，非推测）。
4. **收发存报表要留**，但其权限码当前误挂在会计模块下，需改挂库存，否则会被模块闸关掉。
5. **控制面只在本机**（ADR-004），不进服务器资源预算。
6. **静态索引的 guard 列不可信**：335 条"无守卫"是扫描口径问题，真实守卫在函数体内。

## 尚未完成（诚实列出）

| 缺口 | 原因 | 关闭阶段 |
|---|---|---|
| 732 条路由**逐条**的认证主体、账套对象、数据副作用、新旧路径映射 | 需读函数体，按产品分批推进 | P0 余下 / P4 |
| 176 表与**真实 schema** 的差异分类（列、约束、触发器、RLS、扩展） | 本轮无数据库访问权限 | P0 余下 / P1 |
| 功能验收表、环境依赖表 | 依赖上面的逐条结果 | P0 余下 |
| ADR-003 代码权威归属的逐项落点 | 等 P1 建仓结构时定稿 | P1 |
| 四条流程的真机验收（LINE 手机、Windows Companion） | 需真实设备与账号 | P7 |

## 下一步

P1：锁定 Frappe、Keycloak、MariaDB、Redis、WeKan/Meteor/Mongo、Python/Node 与镜像架构；建 6 业务 Site + 控制 Site + Keycloak + 2 WeKan 的本地完整样板；先验 12GB 资源可行性。
