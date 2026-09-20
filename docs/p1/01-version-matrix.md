# P1.01 版本矩阵与资源预算

日期：2026-09-17。全部为**实测取证**，不是记忆或转述。

## 已锁定的版本

| 组件 | 版本 | 取证方式 |
|---|---|---|
| Frappe | `16.33.0` | 本机样板 `sites/apps.json`（`~/pearnly-erp/scripts/business-sample` 的卷，只读挂载查看） |
| ERPNext | `v16.34.1`（commit `0b50853985312bc64977f9324c55b5d8c1ab2e59`） | 同上，含 branch 与 commit 双证 |
| Python | `3.14.7` | 运行 `pearnly-ci:runtime-…` 容器实测 |
| Node / npm | `v24.20.0` / `11.19.0` | 同上（需 source nvm 后才在 PATH） |
| frappe-bench CLI | `5.31.0` | 同上 |
| MariaDB | `11.8` | 样板 compose 镜像 tag，且容器实际运行过 9 小时 |
| Redis | `6.2-alpine`（cache + queue 两个实例） | 同上 |
| WeKan | `v11.58.0` | `~/wekan/package.json` |
| Meteor | `3.5.2-rc.0` | `~/wekan/.meteor/release` |
| MongoDB | `7` | `~/wekan/docker-compose-mongodb-v7.yml` |
| Cowork PDF文本提取 pypdf | `6.15.0` | 2026-09-21 独立lab锁定镜像实查，与固定旧来源requirements.lock同版；frappe.Dockerfile显式锁定 |

## 尚未锁定（不能凭记忆写）

| 组件 | 状态 |
|---|---|
| **Keycloak** | 已锁定 `26.7.4`（quay.io，digest `sha256:82a77884…`）。选法：查 registry 实际可用 tag 后取最新稳定版，**以启动成功且建表成功为准**，不猜。 |
| 镜像架构 | 本机样板镜像是 **arm64**（本机开发用）；目标机 SSD Nodes 是 **x86_64**。两者不可互换。 |
| 基础镜像链 | 现有链是 `upstream/frappe_docker` → runtime → business-sample（见 `scripts/business-sample/Containerfile` 与 `manage.py`）。新仓库要决定沿用还是自建。 |

补充锁定项：

| 组件 | 版本 | 说明 |
|---|---|---|
| Keycloak | `26.7.4` | 独立 MariaDB 库 `keycloak` + 独立账号；实测建表成功、`/health/ready` 200 |
| WeKan 样板镜像 | `ghcr.io/wekan/wekan:v11.83` | **占位**：与项目固定源码 `v11.58.0` **不是同一版本**（GHCR 无该 tag）。建立镜像↔源码可追溯映射与自建构建是 P5 任务 |
| MongoDB | `mongo:7` | 每个 WeKan 实例独立一个，独立账号 |

## 资源预算（T0 初始目标，待实测修正）

目标机 12GB。控制 Site 不占目标机（ADR-004）。以下为**每服务限额的初始设定**，用来做可行性检查；不是承诺值。

第一版预算是纸面估算；实测后**已按实测修正**，见 [03-sample-t0.md](03-sample-t0.md)。要点：WeKan 单实例必须给到 **2 GiB 上限**（否则启动即崩），Frappe 运行组的实际占用远低于估算。

| 组件 | 实例数 | 单实例上限 | 上限小计 | **实测占用** |
|---|---|---|---|---|
| MariaDB（所有 Site 库 + Keycloak 库，**每库独立账号**） | 1 | 2.0 GiB | 2.00 | 0.47 |
| Redis cache / queue | 2 | 128 MiB | 0.25 | 0.015 |
| Frappe 产品运行组（web/worker/scheduler/socketio/bench） | 3 组 | 1.7 GiB / 组 | 5.06 | 0.95 |
| Keycloak | 1 | 1.0 GiB（堆 512M） | 1.00 | 0.50 |
| WeKan 应用 | 2 | **2.0 GiB** | 4.00 | 1.60 |
| MongoDB | 2 | 512 MiB | 1.00 | 0.20 |
| 控制组（仅开发机，不计目标机） | 1 | 0.83 GiB | 0.83 | 0.22 |
| **目标机合计（不含控制组）** | | | **13.31** | **约 3.7** |

上限小计 13.31 GiB 已超过 12 GiB，但**上限是天花板不是预留**：实测同时运行只占约 3.7 GiB。风险在于多个组件同时出现启动峰值（WeKan 启动时需要约 1.2 GiB 堆）。这点要在 P7 真机混压时专门验。

## 已知会推高资源的三项

1. **WeKan 逐租户实例化**：每个启用看板的租户一套 Node + Mongo，是随租户线性增长的固定成本。
2. **Frappe 每产品一组进程**：三组是下限，产品拆细会继续加。
3. **OCR 与 AI**：按计划走本机受限执行器、全机重任务初始并发 1；并发提高会直接吃内存。

## 不许做的事

- 不得为了"跑起来"悄悄降低隔离（共享库账号、共享文件卷）。
- 不得测试后改阈值凑通过。
- 不得把本机 arm64 的资源数字当目标机结论。
