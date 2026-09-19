# 开发入口

这套约定服务于 Pearnly 的开发、迁移、验证和交接。参考 ERPNext/Frappe 的工程方法；产品需求仍由 Pearnly 自己决定。官方规范的采用边界见 [参考来源](docs/development/references.md)。

## 每个工作包怎么做

1. **确认范围**：一句话说明要改变的行为和完成条件。迁移时先核验旧项目当前线上版本，再登记对应源码 SHA、路径和新落点；交付前再次核验。小修不要求写完整方案。
2. **隔离工作**：核对当前改动和基线，创建独立分支、工作树。涉及运行环境时再核对实际挂载与 Site；新工作树不会自动获得独立容器。
3. **实现**：遵守现有 App 边界、版本锁和服务端权限，保持迁移保护。顺手重构限于本次必要范围。
4. **验证**：按改动选检查，记录版本、环境、预期和结果。真连外部系统另看授权，不能批量运行全部 sample 脚本。
5. **审查与交付**：检查完整影响链，更新对应文档和任务记录。只暂存自己的路径，分别报告实现、本地检查、CI、部署和验收。
6. **发布或交接**：任务包含发布才进入发布步骤；否则交付本地结果和剩余事项。清理只覆盖本任务资源。

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

**当前执行边界**：本工作树的 DMS 基线有 App 级 pre-commit、翻译检查、前端构建及 sample 验证；远端 `main` 和云开通分支另有手动生产镜像构建、投递工作流，尚未整合到此树。已有构建工作流不等于根级统一开发检查或强制合并闸门。各分支、CI 与运行状态见 [现状](docs/development/current-state.md)，不能仅看一个目录断言整个仓库没有流水线。
