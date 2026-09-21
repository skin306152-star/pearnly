# P0.01 源基线核对

日期：2026-09-17。方式：只读。没有 fetch、没有建工作树、没有改任何来源仓库、没有连现网。

## 四仓基线

| 仓库 | 路径 | 分支 | HEAD | 与远端 | 脏文件 | 工作树 |
|---|---|---|---|---|---|---|
| pearnly-app | `~/Developer/Pearnly/pearnly-app` | master | `e4505459` | **落后 117 提交** | 19 | 2 |
| pearnly-app 调查树 | `~/Developer/Pearnly/pearnly-tenant-isolation` | codex/tenant-database-isolation | `ecdacdba` | 与远端 master 相同 | 0 | 同上 |
| pearnly-erp | `~/pearnly-erp` | main | `067596c2` | 一致 | 3 | 1 |
| WeKan | `~/wekan` | main | `6722a11d` | 见下 | 0 | 1 |
| Companion | `~/Developer/Pearnly/pearnly-companion` | master | `ca7f2679` | 一致 | 0 | 4 |

四个 HEAD 与计划包 `10-IMPLEMENTATION-HANDOFF.md` 登记的 SHA 全部一致。

## 漂移

- **pearnly-app**：远端 `refs/heads/master` = `ecdacdba`，与本地 `origin/master` 和调查树 HEAD 相同，**远端没有移动**。日常目录 master `e4505459` 是它的祖先，落后 117 提交，且有其他任务的改动，不在其上施工。
- **WeKan**：远端 `refs/heads/main` 已到 `d23e666a`，本地 pinned 仍是 `6722a11d`，本地 remote-tracking ref 是旧的（`rev-list HEAD...origin/main` 显示 0/0，说明未 fetch）。上游前进属于预期，计划本就固定版本，**维持 pin，不跟随上游**。**2026-09-21 更新**：团队协作看板已决定不迁移，此来源仓库不再是迁移对象，上面的测量只作历史记录。
- **pearnly-erp**：远端 `067596c2`，一致。
- **Companion**：远端 `ca7f2679`，一致。

结论：本次不需要 fetch 就能确认基线；除 WeKan 上游外没有漂移。任何后续工作树都从 `ecdacdba`（app 当前 master）建立。

## 工作树与归属

- app 的 `pearnly-tenant-isolation` 是 `pearnly-app` 的**链接工作树**（`gitdir` 指向 `pearnly-app/.git/worktrees/…`），两者共享对象库与 refs，在任一处 fetch 都影响另一方。
- Companion 登记 4 个工作树，其中 3 个标记 `prunable`（目录已不存在）：`pearnly-companion-dynamic-targets`、`pearnly-companion-line-target-switch`、`pearnly-companion-profile-identity-sync`。**不属于本次任务，不清理**。

## 其他任务的痕迹（保留不动）

- `pearnly-app` 有 19 个脏文件：ERP/Cowork 文档、e2e 截图产物，以及若干疑似误操作产生的怪名文件（`\031`、`\035-`、`\0350`、`5`、`Im\027`、`stream`、`stream\r`）。
- `pearnly-erp` 有 3 个脏文件：`README.md` 被删除、子模块 `upstream/frappe_docker` 被修改、一个超长空格文件名。

这两处说明历史上发生过 shell 误写。本次任何脚本都不使用通配符匹配仓库路径。

## 本机运行与数据归属

- **无用户态服务进程**：没有 uvicorn、gunicorn、node/vite、meteor、bench、frappe 在跑。
- **Docker**：Colima default profile = 4 CPU / 6 GiB / 60 GiB。容器只剩 `pearnly-line-owner-inspect`（状态 `Created`，未运行，镜像 `fe78f03cea42`），**归属不明，不动**。
- **卷 10 个**：2 个属已清理的 `pearnly-ci-business-sample`（`_database` 377MB、`_sites` 1.87MB，内含 Frappe 16.33 站点 `business.localhost`），8 个无归属标签，都**保留**。
- **镜像 29 个 / 28.56GB**（可回收 6.77GB），其中 `pearnly-ci` 系列 4.83GB×多份，另有 `asia-southeast1-docker.pkg.dev/pearnly/pearnly-app/wekan` 三个 tag。构建缓存 11.32GB。

## 工具可用性

- `gcloud` 已安装并已登录（`skinzihao@gmail.com`，SDK 583.0.0），现网只读盘点具备条件。
- 目标机是本机之外的 Contabo VPS，尚未采购，不存在可盘点的目标机。

## 未做与待办

- 未 fetch；未创建任何工作树。
- 未做现网（Cloud Run / Cloudflare / 队列 / 数据库 / 存储 / 外部连接）只读盘点。
- 未开始 735 条装配路由、176 表、外部连接的归属表。
