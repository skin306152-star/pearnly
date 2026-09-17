# P1.02 独立环境与共享基础层

日期：2026-09-17。本阶段开始出现真实环境变更，全部限于**新开的独立虚拟机**，不碰其它任务。

## 独立环境

| 项 | 值 |
|---|---|
| Colima profile | `pearnly`（与 `default`、`b3b2a-pg-harness`、`b3b2b1-pg16-harness` 完全隔离） |
| 分配 | 6 CPU / 12 GiB 内存 / 120 GiB 磁盘 |
| VM 内可用内存 | 11.66 GiB（`MemTotal` 11934 MB） |
| Docker 数据盘 | `/dev/vdb1` 118 GiB，可用 111 GiB（`/var/lib/docker` 落在此盘，不在 19 GiB 的系统盘上） |
| 访问方式 | `docker --context colima-pearnly …` |

**已处理的副作用**：创建 profile 时 Colima 把 docker 的默认 context 切成了 `colima-pearnly`，这会影响其它任务的 `docker` 命令。已改回 `colima`，其它任务不受影响；本项目的命令一律显式带 `--context colima-pearnly`。

释放方式：`colima stop -p pearnly`（停掉后 12 GiB 内存全部归还，容器与卷保留）。

## 基础层内容

`infrastructure/compose/00-base.yml`：MariaDB `11.8`、Redis `6.2-alpine`（cache 与 queue 两个实例），按版本矩阵的预算设了限额（2G / 128M / 128M）。

设计取舍：所有 Site 库与 Keycloak 库**落在同一个 MariaDB 实例的不同 database 上，每库一个独立账号**。隔离靠库与账号，不靠多开数据库进程——这与"每部署独立数据库、禁止跨库访问"的要求一致，也符合 12GB 预算。

## 已实测通过：库与账号隔离

在样板里建 `site_a`、`site_b`，各配独立账号 `user_a`、`user_b`，只授予自己那个库的权限。实测结果：

| 测试 | 结果 |
|---|---|
| `user_a` 在自己的库建表、插入、查询 | 通过 |
| `user_a` 读 `site_b.t_b` | **拒绝**：`ERROR 1142 SELECT command denied to user 'user_a'@'localhost' for table 'site_b.t_b'` |
| `user_a` 建新库 `site_c` | **拒绝**：`ERROR 1044 Access denied for user 'user_a'@'%' to database 'site_c'` |
| 重启虚拟机后数据仍在，且反向拒绝仍然生效 | 通过（持久卷有效） |

这条是 P1 的硬要求之一（"按选定 MariaDB 基线测试独立 database 与独立用户，禁止跨库访问"），**已有证据**，不是设计推断。

## 当前资源占用（基础层）

| 容器 | 内存 |
|---|---|
| mariadb | 153.3 MiB / 2 GiB |
| redis-cache | 9.3 MiB / 128 MiB |
| redis-queue | 2.4 MiB / 128 MiB |

基础层只占约 165 MiB，符合预算表里"这一层不是瓶颈"的预估。

## 尚未搭（P1 余下）

| 项 | 状态 |
|---|---|
| Keycloak | 未搭。版本未定，需按实际启动结果锁定 |
| Frappe Bench 与 6 业务 Site + 控制 Site | 未搭。需先决定沿用现有 arm64 样板镜像还是自建基础镜像链 |
| WeKan ×2（含各自 Mongo 库） | 未搭 |
| 产品/身份/连接模型合同（ProductManifest、Principal 等） | 未写 |
| 控制 API v1、事件 envelope、任务 schema、错误码 | 未写 |
| 12GB 资源可行性 T0 报告 | 未出。目前只有基础层数据，不能下结论 |
