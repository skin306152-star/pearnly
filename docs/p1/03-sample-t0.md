# P1.03 样板 T0 资源测量

日期：2026-09-17。**范围**：基础层 + 四个 Frappe 运行组 + 七个站点 + Keycloak + 两个 WeKan 租户（**WeKan 部分已按 2026-09-21 决定移除**，见文内说明）。站点仍是裸 Frappe（未装产品 App、无业务数据、无并发负载），所以这是**空载 T0**，不是容量结论。

## 已搭成的内容

- 基础层：MariaDB 11.8 + Redis 6.2 ×2（`infrastructure/compose/00-base.yml`）
- 运行组：cowork / erp / dms / control 四组（`infrastructure/compose/01-bench.yml`）
- 站点 7 个：`cowork-a`、`cowork-b`、`erp-a`、`erp-b`、`dms-a`、`dms-b`（6 个业务，三产品各两租户）+ `control.localhost`
- 基础镜像：`pearnly/frappe-sample:16.34.1`（自建，digest 锁定官方 `frappe/erpnext:v16.34.1`）
- 身份：Keycloak `26.7.4`（`infrastructure/compose/02-keycloak.yml`），独立 MariaDB 库与账号
- 协作：**已按用户 2026-09-21 决定移除**（原先两个租户各一套 WeKan + 独立 MongoDB，样板 `infrastructure/compose/03-wekan.yml` 已删除）。本节记录的是移除前的实测，容器数按此说明扣减自看板那部分。
- 容器总数 **26**，**全部 Up**；管理脚本 `scripts/sample-init-group.sh`（幂等，可重复执行）

## 端点实测

| 目标 | 结果 |
|---|---|
| 七个 Frappe 站点 `GET /api/method/ping`（带对应 Host 头） | 七个全部返回 `{"message":"pong"}` |
| Keycloak 管理端口 `/health/ready` | `200` |
| Keycloak 主端口 `/realms/master` | `200`；`keycloak` 库中已建出 `ADMIN_EVENT_ENTITY`、`AUTHENTICATION_FLOW` 等表 |
| WeKan 租户 A（`127.0.0.1:8106`） | `200` |
| WeKan 租户 B（`127.0.0.1:8107`） | `200` |

## T0 内存实测

| 范围 | 实测内存 |
|---|---|
| WeKan 租户 A（应用 822 + Mongo 100） | 922 MiB |
| WeKan 租户 B（应用 820 + Mongo 100） | 920 MiB |
| **WeKan 层小计** | **1842 MiB** |
| Frappe 产品三组（cowork 383 / erp 302 / dms 291） | 976 MiB |
| Keycloak | 514 MiB |
| MariaDB | 479 MiB |
| Redis ×2 | 15 MiB |
| **目标机合计（不含控制组）** | **约 3.7 GiB** |
| 控制组（仅开发机） | 222 MiB |

虚拟机整体：总量 11935 MiB，已用 4593 MiB，可用 7341 MiB。

磁盘：Docker 数据盘 118 GiB，余量充足。

## 与预算表的差异（必须记下来）

两处与纸面预算差别很大，都是实测才暴露的：

1. **WeKan 单实例上限必须是 2 GiB，不是 512 MiB。** 512 MiB 被内核 OOM 连杀 9 次（`OOMKilled=true`、退出码 137）；提到 1 GiB 改成 V8 堆爆（退出码 133，`Reached heap limit`）。原因写在镜像自带的入口脚本里：堆上限按容器限额的 60% 计算，而 server bundle 在"链接 + 建启动索引"阶段就会越过它，上游注释也标注这是 v10.96+ 的已知问题（#6606）。2 GiB 下未再重启，实际占用约 820 MiB——**上限要留够，实际占用没那么高**。
2. **Frappe 运行组实际占用只有约 0.3 GiB/组**，远低于估算的 1.5 GiB。原因是站点是裸 Frappe，没装 ERPNext、没业务数据。

**不许拿这个数字去承诺容量。** 装上产品 App、导入数据、有人真实操作之后要重测。

## 过程中踩到并已修复的坑

1. **新建的 sites 卷里 `common_site_config.json` 是空的**，realtime 服务会去连容器自己的 `127.0.0.1:6379`，然后无限重启。必须把 `redis_socketio` 等写进全局配置。已固化进 `scripts/sample-init-group.sh`。
2. 卷初始化必须以 root 执行拷贝，否则 frappe 用户无权写入。
3. 创建含 digest 的基础镜像时，本机是 arm64；**目标机是 x86_64，构建时必须显式指定目标平台**。
4. **Keycloak 25 起 `/health/*` 不再挂在主端口**，默认在管理端口 9000。只在 8080 上探健康会得到 404，误判成"没起来"。已把管理端口一并发布。

## 隔离证据（P1 硬要求）

- Frappe 建站时自动为该站点创建独立数据库与独立账号：实测 `GRANT ALL PRIVILEGES ON \`_001bcab0891630c2\`.*`，外加 `GRANT USAGE ON *.*`——**没有任何全局权限，也无权访问其它库**。
- 手工测试同样通过：`user_a` 读 `site_b` 被拒（`ERROR 1142`），建新库被拒（`ERROR 1044`）。

## 这份结论不能说明什么

- 没有 Keycloak（身份服务）与 WeKan ×2，**这两块是资源预算里的大头**。
- 站点是裸 Frappe，没装产品 App，没有真实数据量，没有并发负载。
- 本机是 arm64，目标机 x86_64，资源数字不可互推。
- 因此：**"未发现阻断"不等于"12GB 够用"**。真机结论在 P7。
- WeKan 用的是发布镜像 `v11.83` 占位，与项目固定源码 `v11.58.0` 不是同一版本，资源数字仅供形态参考。
- 所有上限加总约 13.3 GiB，**已超过目标机 12 GiB**。上限不是预留，实测同时运行约 3.7 GiB；但多个组件同时出现启动峰值（尤其 WeKan 需要约 1.2 GiB 堆）是否会撞车，本地未验，留给 P7 混压。
