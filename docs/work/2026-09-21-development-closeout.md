# 已合并分支清理与收工规范

## 范围、基线与授权

用户在核查分支残留后明确要求“清理一下，并纳入开发规范，每次收工清理干净”。本包负责核查清单内的旧 Git 残留和 auth 工作树，以及开发规范；不改业务代码，不合并 Cowork，不部署，不停止服务或删除数据卷。

- 开工主线及 GitHub 实际 `main`：`1a0585e6d53a995aa59a6045fe027246d18471f9`，主目录干净。
- 本包：`codex/development-closeout`，工作树 `/Users/skin/.codex/worktrees/development-closeout/pearnly`，集成目标 `main`。
- 规范与本记录在独立工作树修改。最终版本以包含记录的主线提交及交付时的实际远端回读定位，不填自引用 SHA。
- 收工条件：规范合回主线并推送、回读一致后，删除本包临时工作树和本地分支；本包不另建远端任务分支。执行结果在最终交付说明回读，不能因为写了此条件就算已完成。

## 核查与已经执行的清理

本地 11 个旧任务分支中，下面 10 个 tip 都是主线祖先，独有提交为 0；远端实际查询有 5 个同名分支，SHA 与本地一致。删除前再次核对全部 SHA、主线、工作树和运行占用，保留 Cowork。

| 已删除的本地分支（`codex/` 前缀） | 最后提交 | 远端任务分支 |
| --- | --- | --- |
| auth-flow-fixes | `14f0bb600cdc70edcbcb6a283ce19dc9e7ec41d4` | 原本不存在 |
| cloud-provisioning | `201cc4b263330f68ca2349809a66d8c7c2b2740d` | 已删除 |
| development-workflow | `0411949b7b27d5d13ea946694a11b50a5e4e03ec` | 已删除 |
| dms-line-diagnosis | `7415c1e35f1f35babdda77a5077d6891efa614a4` | 已删除 |
| dms-parity | `57bee0001e9c79dc258d641e24015b52d6ccf3cb` | 已删除 |
| mail-template-compat | `f72c679384438903da6af8885fe71fac23f6ab22` | 原本不存在 |
| production-contabo | `d0df6b84733deb122efee5760e0824336b0b4562` | 原本不存在 |
| release-domain-architecture | `909df6d475281c124e5c51eadf9e8de269ab9cb5` | 原本不存在 |
| ssdnodes-migration | `12176e9e09d579e71935ef456900870e5c85d1f1` | 已删除 |
| welcome-email-card | `e97328fcad5c56b908cf2a3d5faf3b7f99d65eb6` | 原本不存在 |

- auth 工作树无已跟踪改动或未跟踪文件，忽略内容只有 4 处 Python 缓存、前端产物、两个自动生成类型文件及 node_modules 链接。其预览容器已经不存在；全部现有容器无 auth 路径挂载，`lsof +D` 无占用输出。
- 已用非 force 的 `git worktree remove` 删除 auth 工作树，再移除确认为空的父目录。node_modules 是指向 main 的符号链接，删除后主目录依赖目录仍存在且 inode 未变，未递归删除共享目标。
- 远端 5 个分支使用逐 ref 的已核实 SHA lease 加 atomic push 删除；若远端已变化，整组拒绝，未强制覆盖新提交。删除后 `git ls-remote --heads origin` 只返回 `main`。
- 本地 10 个分支均通过 `git branch -d` 删除，未使用 `-D`。旧提交仍由 main 保存，不需要留下任务分支当备份。
- DMS/SSDN、邮件和发布等旧工作树此前已不存在，本次不重复计作删除。原有历史工作包和发布记录全部保留。

## 保留对象与理由

| 对象 | 所属任务 / 用途 | 本次保留原因与复核条件 |
| --- | --- | --- |
| `/Users/skin/pearnly` / `main` | 长期主目录、管理后台 | 正式开发入口；9 个管理后台容器及开户服务仍使用此树，不是任务残留 |
| Cowork 工作树及 `codex/cowork-backend-migration` | “规划 Cowork 迁移方案”，后续按交接续做 | HEAD `b94fe1c4527a012ced4b12cbae8e2bd7e41609f5`，相对开工 main 有 15 条独有提交，迁移未完成，用户要求本地继续；整合与交接结束时重核 |
| `pearnly-cowork-backend-lab` 3 个停止容器及其卷 | Cowork 隔离验证 | 仍挂载 Cowork 树，Site/DB 保留用于续接；由 Cowork 后续工作包结束时核对清理，不因容器停止就删数据 |
| main 的依赖、产物、私有配置与业务数据 | 共享开发和管理后台 | 在用且不属旧任务残留；本包不处理、不全局清理 |

Cowork 当前实验环境是在此前清理之后为迁移重新建立的，不能据其现在存在就判定上次没有删除。其原交接中 auth 存在的陈述是当时快照，现由本记录的实际删除结果更新；不改 Cowork 在做的工作树。

## 规范变化与验证

根 `AGENTS.md` 新增每次必须执行的收工规则；`CONTRIBUTING.md`、标准流程、记录规范和工作包模板统一入口。必须分别处理工作树、本地分支、远端分支和临时资源；保留项有明确所属任务、用途、原因与复核/解除条件。仅本地或交接工作按已有授权保留，不借清理擅自推送部署，不沿符号链接清共享依赖，也不清理未合并成果。

这些是执行规范，不是新增自动删除器或 GitHub 强制闸门。没有修改 GitHub 分支保护、收费计划或自动删除设置；本包无需业务测试、镜像构建、Site 迁移或发布。

本地验证通过：`git diff --check`、38 个本地 Markdown 链接及相关检查入口存在性、`python3 scripts/check-architecture.py`（0 违规）。另由只读审查代理核对规范，发现并修正“仅本地任务却无条件要求远端保存”的歧义，以及删除前核验后远端又变化的竞态；二次审查通过。远端删除现明确绑定刚核验的 SHA，变化即拒绝，不允许降级成无条件删除。

`AGENTS.md` 提交还须通过共享暂存快照钩子，不跳过。交付时回读本地/远端 refs、工作树、主目录和 Cowork 状态、容器与数据卷清单。CI 若因推送自动触发，单列其实际结果，不与本地静态检查或部署混用。
