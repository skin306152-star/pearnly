# ERP 本批集成分支（2026-10-24）

- 用户口径（主会话转）：「全局由你指挥定夺……所有功能必须验证合格且不会影响其它功能的正常使用才能上线，之前发生过改库存盘点的扫码器，
  把其它功能的扫码器改坏了，没任何窗口发现」。本分支只做集成与检查；**不合入 main、不部署**，合入与上线由主会话在回归全部通过后定。
- 分支 / 工作树：`pearnly/erp-batch-integration` / `.claude/worktrees/erp-integration`，基线 `origin/main` = `cecc475e`。

## 合进来的三个分支

| 分支 | 远端 SHA | 内容 | 记录 |
|---|---|---|---|
| `pearnly/erp-express-sync-background` | `3485dd15` | ERP 按 CC 报来的 Express 账套自动建公司、后台主档同步、主档按公司隔离、年度与关账、提交时就问、LINE 超 12 项打字找、等结果三个点、CC「Express 位置」、问题 1–22 | `docs/work/2026-10-23-erp-express-auto-companies.md`、`2026-10-23-erp-express-sync-background.md` |
| `pearnly/cool-bouman-26647e` | `ae175a46` | ERP 任务管理（`work/` 域、15 张 `ERP Work *` 表、`pearnly-tasks` 页）、共用侧栏平铺项、下载文件名 RFC 5987 | `docs/work/2026-10-23-erp-tasks.md` |
| `pearnly/work-followups-d3d5bf` | `bd78c5cb`（修复 `ca079f1d`） | 任务管理遗留四处：Cowork 卡片关注 / 删封面后保存、默认列表跟站点语言、共用页无权限显示原生页、中文 / 泰文下载名 | `docs/work/2026-10-24-tasks-followups.md` |

- 第一个快进；第二个合并时冲突按任务管理记录的办法解：`hooks.py` 的 `scheduler_events["cron"]` 两边都留（本批的 `push.poll` +
  `masters.advance` 每分钟、`masters.check_marks` 每 10 分钟，加任务管理的 `work.reminders.run` 每 5 分钟）；`patches.txt` 两条都留
  （`v1_12_product_code_key`、`v1_12_tasks_feature`）；ERP 术语表两段都留（键不重复），翻译包 `scripts/i18n-build.py` 重新生成；
  其余（CI、架构文档、权限、菜单）自动合并。第三个无冲突。集成提交 `403dcc07`。

## 检查（都对 `403dcc07`）

| 检查 | 结果 |
|---|---|
| 本地全闸门（`development.yml` 全部 117 步，含 `frontend/assistant` 安装与构建、多行步骤） | 通过 |
| `scripts/check-architecture.py` | 通过（0 违规） |
| `scripts/check-copy.py` | 0 拦截；新增 302、改动 0（已登记的 5 句在分支内）、标黄 232；清单见 `2026-10-24-erp-batch-integration-copy-report.md` |
| 生产镜像 `29404c6a` 上完整 `ci-candidate-checks offline`（候选源码只读挂进镜像） | 通过（CAND EXIT 0） |
| GitHub Development checks / Companion checks | 见推送后回读 |
