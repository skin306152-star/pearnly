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

## 尚未锁定（不能凭记忆写）

| 组件 | 状态 |
|---|---|
| **Keycloak** | **版本未定**。本机与四仓都没有任何 Keycloak 资产。P1 建样板时选一个支持 MariaDB 的稳定版本，**以实际启动成功为准**，不预先写进矩阵。 |
| 镜像架构 | 本机样板镜像是 **arm64**（本机开发用）；目标机 Contabo 是 **x86_64**。两者不可互换。 |
| 基础镜像链 | 现有链是 `upstream/frappe_docker` → runtime → business-sample（见 `scripts/business-sample/Containerfile` 与 `manage.py`）。新仓库要决定沿用还是自建。 |

## 资源预算（T0 初始目标，待实测修正）

目标机 12GB。控制 Site 不占目标机（ADR-004）。以下为**每服务限额的初始设定**，用来做可行性检查；不是承诺值。

| 组件 | 实例数 | 单实例上限 | 小计 |
|---|---|---|---|
| MariaDB（承载所有 Site 库 + Keycloak 库，**每库独立账号**） | 1 | 2.0 GiB | 2.00 |
| Redis cache / queue | 2 | 128 MiB | 0.25 |
| Frappe 运行组（web、worker、scheduler、socketio） | 3 组 | 1.5 GiB / 组 | 4.50 |
| Keycloak | 1 | 1.0 GiB（JVM 堆 512M） | 1.00 |
| WeKan 应用 | 2 | 512 MiB | 1.00 |
| MongoDB（每 WeKan 实例一个库） | 2 | 512 MiB | 1.00 |
| 系统 + Docker 自身 | — | — | 1.00 |
| **合计** | | | **10.75** |

余量约 1.25 GiB。**这是"应该能装下"，不是"验证过能装下"。** P1.08 的实测结论只说明"样板在 12GB 下未发现阻断"，真机容量结论留到 P7。

## 已知会推高资源的三项

1. **WeKan 逐租户实例化**：每个启用看板的租户一套 Node + Mongo，是随租户线性增长的固定成本。
2. **Frappe 每产品一组进程**：三组是下限，产品拆细会继续加。
3. **OCR 与 AI**：按计划走本机受限执行器、全机重任务初始并发 1；并发提高会直接吃内存。

## 不许做的事

- 不得为了"跑起来"悄悄降低隔离（共享库账号、共享文件卷）。
- 不得测试后改阈值凑通过。
- 不得把本机 arm64 的资源数字当目标机结论。
