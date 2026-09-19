# 参考来源与采用边界

核对日期：2026-09-19。参考的是工程管理方式，Pearnly 的产品需求与范围继续由本仓需求、产品清单和 ADR 决定。

## 官方来源

| 来源 | 采用的方法 | 本仓适配 |
| --- | --- | --- |
| [ERPNext 贡献指引](https://github.com/frappe/erpnext/blob/b1225b5cc7f144e4253941a5a95cd626f4e1033d/.github/CONTRIBUTING.md) | 一个问题一份记录，提供版本、复现和预期行为 | 小修可用提交/PR；迁移保留工作包 |
| [ERPNext PR 清单](https://github.com/frappe/erpnext/wiki/Pull-Request-Checklist) | Conventional Commits、服务端校验、相关测试、文档和 UI 证据 | 按影响选择，不把所有上游维护流程搬进来 |
| [ERPNext PR 模板](https://github.com/frappe/erpnext/blob/b1225b5cc7f144e4253941a5a95cd626f4e1033d/.github/PULL_REQUEST_TEMPLATE.md) | 说明具体问题和行为变化 | 加上迁移来源、证据分层和授权边界 |
| [ERPNext pre-commit](https://github.com/frappe/erpnext/blob/b1225b5cc7f144e4253941a5a95cd626f4e1033d/.pre-commit-config.yaml) | 格式、语法和冲突检查自动化 | 采纳方法；不复制旧工具版本，也不声称本仓已接根级 hook |
| [ERPNext MariaDB CI](https://github.com/frappe/erpnext/blob/b1225b5cc7f144e4253941a5a95cd626f4e1033d/.github/workflows/server-tests-mariadb.yml) | 框架与数据库配套验证，按文件类型分流 | 不复制专用 runner、内部镜像凭据和整套分片设施 |
| [Frappe 测试](https://docs.frappe.io/framework/user/en/testing) | 在 bench/Site 环境中按 App、模块选择测试 | 自有 App 的集成检查使用对应锁定版本；历史 sample 渐进整理 |
| [Frappe migrate](https://docs.frappe.io/framework/user/en/bench/reference/migrate) | patches、schema、fixtures 等由原生迁移处理 | 加上本仓的数据保留与恢复证据要求 |
| [Codex AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md) | 项目范围内的分层开发指令 | 根文件简短、详细流程按需阅读，不固定模型或强制代理队伍 |

固定 commit 的 ERPNext 文件已通过本地只读子模块检查；Wiki、Frappe 与 Codex 文档通过官方网页核对，可能随上游更新。工具和运行版本仍以本仓锁定值为准。

## 本地工程参考

- `pearnly-erp` 的 `CONTRIBUTING.md`、`docs/development.md`、根 pre-commit 和 CI：借鉴按改动选测试、独立 Site、候选版本证据及数据保护；不沿用其商户业务范围、生产目标或具体运行参数。
- `pearnly-app` 的 `AGENTS.md`、`docs/agent/VERIFICATION.md`、`docs/GATES.md`：借鉴权限、幂等、外部结果回读和多工作树归属；不复制其 32 道历史闸、Cloud Run 发布路径或 `static/dist` 提交策略。
- 本仓已有 ADR、版本矩阵、sample 脚本和阶段记录：作为实际落地依据。来源提交与已知漂移见 [现状](current-state.md)。

## 明确不继承

- ERPNext 的会计、库存、POS、HR 等功能需求和默认模块集合。
- 上游 `develop`/hotfix 分支政策、贡献者审批流程或每天全量测试的调度安排。
- 参考文档中跳过 hook、测试分支绕过业务验证等可选做法。
- 来源仓库的历史授权、自动发布、采购、真实账号测试或清理要求。

工作包、证据分层、数据保护和单 Agent 默认是结合本仓现状制定的规则；不把这些全部称为 ERPNext 官方要求。
