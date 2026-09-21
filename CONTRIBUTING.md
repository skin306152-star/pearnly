# 开发入口

这套约定服务于 Pearnly 的开发、迁移、验证和交接。参考 ERPNext/Frappe 的工程方法；产品需求仍由 Pearnly 自己决定。官方规范的采用边界见 [参考来源](docs/development/references.md)。

## 每个工作包怎么做

1. **确认范围**：一句话说明要改变的行为和完成条件。迁移时先核验旧项目当前线上版本，再登记对应源码 SHA、路径和新落点；交付前再次核验。小修不要求写完整方案。
2. **隔离工作**：核对当前改动和基线，创建独立分支、工作树。涉及运行环境时再核对实际挂载与 Site；新工作树不会自动获得独立容器。
3. **实现**：先按 [架构要求](docs/development/architecture.md) 确定业务域、入口与依赖，遵守 App 边界、版本锁和服务端权限，保持迁移保护。顺手重构限于本次必要范围。
4. **验证**：按改动选检查，记录版本、环境、预期和结果。真连外部系统另看授权，不能批量运行全部 sample 脚本。
5. **审查与交付**：检查完整影响链，更新对应文档和任务记录。只暂存自己的路径，分别报告实现、本地检查、CI、部署和验收。
6. **收工或交接**：按现有授权完成合并、推送和远端回读；本地或交接任务不因此强制推送、部署。逐项处理本任务工作树、本地/远端分支及临时资源，分别记录“完成清理”或“交接保留”；删工作树不等于删分支。最终交代提交 SHA、远端 refs、工作树/资源和余项四类结果，保留项写明负责人、用途、原因及复核或解除条件。具体标准见 [流程](docs/development/workflow.md)；小修可在提交或交付说明中记录，不另建状态卡。

## 按需要查

| 要做什么 | 入口 |
| --- | --- |
| 看现状、已知文档漂移、当前工具缺口 | [现状与边界](docs/development/current-state.md) |
| 开工、迁移、审查、合并、发布 | [标准流程](docs/development/workflow.md) |
| 防止迁入旧代码、核对生产与源码 | [迁移线上基准](docs/development/migration-baseline.md) |
| 给开发 Agent 下任务或交接 | [Agent 规则](AGENTS.md)、[角色与任务约定](docs/development/agents.md) |
| 选择检查、判断一条证据够不够 | [验证矩阵与命令](docs/development/verification.md) |
| 工作包、决策、验收、发布如何留记录 | [记录规范](docs/development/records.md) |
| 查框架/依赖版本 | [版本矩阵](docs/p1/01-version-matrix.md)、[镜像定义](infrastructure/images/frappe.Dockerfile)、各锁文件 |
| 查产品和技术决定 | [ADR](docs/adr/README.md)、对应模块文档 |
| 查脚本用途和副作用 | [脚本入口](scripts/README.md) |

新克隆先在长期主目录运行 `sh scripts/install-dev-hooks.sh`，安装本仓各工作树共享的暂存快照架构检查。已有钩子不会被覆盖。

## 当前可用的检查入口

在本任务工作树中执行；应用依赖使用锁定的 Python/Node 环境。以下按需选，不是每次都运行的清单。

```bash
git status --short --branch
git diff --check
git diff --cached --check

# 改动涉及翻译时
python3 scripts/i18n-build.py --check
python3 scripts/check-i18n.py --strict
python3 scripts/check-frontend-i18n.py --strict

# 改动涉及前端时；依赖首次安装或锁文件变化后才需要 npm ci
npm --prefix frontend ci
npm --prefix frontend run build
```

`build` 已包含类型检查，不必再重复 `typecheck`。文档改动无需安装业务依赖。Frappe Site 和 DMS 的定向检查见 [验证规范](docs/development/verification.md)。

**当前执行边界**：本仓已整合生产构建/投递与 `Development checks`。代码变更先运行 `python3 scripts/check-architecture.py`，DMS 变更再运行 `python3 scripts/check-dms-offline.py`。CI 存在不等于已启用 GitHub 强制分支保护；实现、检查、部署与业务验收仍分别报告。
