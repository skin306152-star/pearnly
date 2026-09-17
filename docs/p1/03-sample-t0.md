# P1.03 样板 T0 资源测量（阶段性）

日期：2026-09-17。**这份不是最终 T0 报告**：Keycloak 与 WeKan 尚未加入，站点也还没装产品 App。见文末"这份结论不能说明什么"。

## 已搭成的内容

- 基础层：MariaDB 11.8 + Redis 6.2 ×2（`infrastructure/compose/00-base.yml`）
- 运行组：cowork / erp / dms / control 四组（`infrastructure/compose/01-bench.yml`）
- 站点 7 个：`cowork-a`、`cowork-b`、`erp-a`、`erp-b`、`dms-a`、`dms-b`（6 个业务，三产品各两租户）+ `control.localhost`
- 基础镜像：`pearnly/frappe-sample:16.34.1`（自建，digest 锁定官方 `frappe/erpnext:v16.34.1`）
- 容器总数 21，**全部 Up**；管理脚本 `scripts/sample-init-group.sh`（幂等，可重复执行）

## 七个站点全部有响应（实测）

对每个站点发 `GET /api/method/ping`（带对应 Host 头），七个全部返回 `{"message":"pong"}`。

## T0 内存实测

| 范围 | 内存 |
|---|---|
| cowork 组（web/worker/scheduler/socketio/bench） | 322 MiB |
| erp 组 | 312 MiB |
| dms 组 | 303 MiB |
| **产品三组小计**（目标机口径） | **937 MiB** |
| 控制组（仅开发机，不计入目标机口径） | 216 MiB |
| MariaDB | 437 MiB |
| Redis ×2 | 15 MiB |
| **合计（含控制组）** | **约 1.6 GiB** |

虚拟机整体：总量 11935 MiB，已用 2121 MiB，可用 9813 MiB。

磁盘：镜像 4.79 GiB、本地卷 492 MiB。Docker 数据盘 118 GiB，余量充足。

## 与预算表的差异（必须记下来）

预算表按"每组 1.5 GiB"估，实测每组只用约 0.3 GiB，**比预估低一个数量级**。原因是：站点只装了 Frappe 框架，没有装 ERPNext，也没有业务数据，DocType 缓存很小。

**不许拿这个数字去承诺容量。** 装上产品 App、导入数据、有人真实操作之后要重测。

## 过程中踩到并已修复的坑

1. **新建的 sites 卷里 `common_site_config.json` 是空的**，realtime 服务会去连容器自己的 `127.0.0.1:6379`，然后无限重启。必须把 `redis_socketio` 等写进全局配置。已固化进 `scripts/sample-init-group.sh`。
2. 卷初始化必须以 root 执行拷贝，否则 frappe 用户无权写入。
3. 创建含 digest 的基础镜像时，本机是 arm64；**目标机是 x86_64，构建时必须显式指定目标平台**。

## 隔离证据（P1 硬要求）

- Frappe 建站时自动为该站点创建独立数据库与独立账号：实测 `GRANT ALL PRIVILEGES ON \`_001bcab0891630c2\`.*`，外加 `GRANT USAGE ON *.*`——**没有任何全局权限，也无权访问其它库**。
- 手工测试同样通过：`user_a` 读 `site_b` 被拒（`ERROR 1142`），建新库被拒（`ERROR 1044`）。

## 这份结论不能说明什么

- 没有 Keycloak（身份服务）与 WeKan ×2，**这两块是资源预算里的大头**。
- 站点是裸 Frappe，没装产品 App，没有真实数据量，没有并发负载。
- 本机是 arm64，目标机 x86_64，资源数字不可互推。
- 因此：**"未发现阻断"不等于"12GB 够用"**。真机结论在 P7。
