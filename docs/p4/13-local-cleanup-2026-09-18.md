# 2026-09-18 本机清理记录

用户要求：**"把本机其它无关的全部关停或者清掉，没有别的项目在本地跑，可能是忘关了，
现在重心全部放在我们这边。"**

下面记清"删了什么、判断依据是什么、能不能找回"，免得以后要查。

## 判断依据（动手前先查的）

- 新仓库全仓搜 `colima`，**只命中 `colima-pearnly`** —— 不存在第二个被本项目引用的 profile。
- `default` profile 里 29 个镜像全部是**老项目**的：`pearnly-ci:*`（本机构建的 CI 镜像）、
  老项目 WeKan 镜像、以及通用的 postgres/mongo/mariadb/redis/alpine。
- 两个 harness VM 状态是 `Stopped`，且不与任何项目关联。
- 老仓库 `pearnly-app` 里**没有任何** `colima` / `docker context` 引用。

## 删掉了什么

| 对象 | 说明 | 体积 |
|---|---|---|
| Colima VM `default` | 老项目的本地环境。VM 与磁盘一并删除 | 28 GB 盘 |
| 其内 29 个镜像 | 见文末清单。`pearnly-ci:*` 本机可重建，其余可从 registry 重拉 | 约 39.9 GB（含缓存） |
| 构建缓存 | `docker builder prune -af` | 11.3 GB |
| VM `b3b2a-pg-harness`、`b3b2b1-pg16-harness` | 早已 `Stopped` 的 harness 残留，VM + 磁盘 | 1.3 GB |
| 孤儿磁盘 `colima-stocktake-pg` | 盘在、`colima list` 里没有对应 VM | 409 MB |
| 孤儿容器 `pearnly-line-owner-inspect` | 状态 `Created`，**从没启动过** | — |
| `~/.colima/pearnly-resize-backup-CLNYSD` | 扩容留下的临时备份 | 12 KB |

**`~/.colima` 合计 46 GB → 14 GB。**

## 本项目自己的：停了但没删

**两套 WeKan 夹具已停止**（`wekan-a` / `wekan-b` 与各自的 Mongo）。它们是 Cowork 看板
功能的样板（P1 拿来测资源形态），做 DMS 用不到，且各占约 850 MB 常驻内存。
**数据卷没动**，恢复只要一条命令：

```bash
scripts/stack.sh up wekan-a wekan-b wekan-a-db wekan-b-db
```

## 关于"能不能找回"

删除用的是 `/usr/bin/trash`（macOS 自带、移入废纸篓）。但**我列不了废纸篓**
（`ls ~/.Trash` 被系统拒绝），所以**无法确认**能不能放回 —— 按**不可恢复**对待。

实际影响很小：删掉的要么是孤儿（没有 VM 引用），要么内容此前已被清空，
要么可以从 registry 重拉、或由 `~/pearnly-erp/scripts/business-sample` 重建。

⚠️ 一处要留意：`docs/p1/01-version-matrix.md` 里 Python / Node 版本那两行的**证据来源**
是 `pearnly-ci:runtime-…` 容器（现已删除）。**记录下来的值仍在文档里**，构建源码也还在，
需要时可重建复现；但如果将来要"重新实测一次版本"，得先重建那个镜像。

## 留档：当时 default profile 里的 29 个镜像

```text
asia-southeast1-docker.pkg.dev/pearnly/pearnly-app/wekan:bc1ed19b98ae8326f6a2e2afe99b7280edad0ec3	357MB
asia-southeast1-docker.pkg.dev/pearnly/pearnly-app/wekan:c401672f035fe2f6404fb44765ef4ea8456a0b0e	1.58GB
asia-southeast1-docker.pkg.dev/pearnly/pearnly-app/wekan:c6c116846ceb47f32796e28c4b147c5cbd5eedc9	355MB
pearnly-ci:sample-2f779849c014-d986389078ad	4.83GB
pearnly-ci:runtime-2f779849c014-d986389078ad	4.83GB
pearnly-ci:sample-5167c81afcf3-101074aba101	4.83GB
pearnly-ci:runtime-5167c81afcf3-101074aba101	4.83GB
pearnly-ci:sample-e7db52be87d9-1ac9ade30135	4.83GB
pearnly-ci:runtime-e7db52be87d9-1ac9ade30135	4.83GB
pearnly-ci:sample-e7db52be87d9-ffed3c60f451	4.83GB
pearnly-ci:runtime-e7db52be87d9-ffed3c60f451	4.83GB
pearnly-ci:sample-f047f7185a36-e53ed19c396c	4.83GB
pearnly-ci:runtime-f047f7185a36-e53ed19c396c	4.83GB
pearnly-ci:sample-770345402e2d-efa4c6ccde64	4.83GB
pearnly-ci:runtime-770345402e2d-efa4c6ccde64	4.83GB
pearnly-ci:sample-6a88369d9a0d-09e6a822d0d6	4.83GB
pearnly-ci:runtime-6a88369d9a0d-09e6a822d0d6	4.83GB
pearnly-ci:sample-f9c2c972386e-d1b062f63cc4	4.83GB
pearnly-ci:runtime-f9c2c972386e-d1b062f63cc4	4.83GB
pearnly-ci:sample-9ac1c799f8d9-fb72530429e9	4.83GB
ghcr.io/wekan/wekan:<none>	2.82GB
asia-southeast1-docker.pkg.dev/project-d0fbd530-1ee3-436d-b58/pearnly/erpnext:096f82a3c92d5fe119681537a794d6ffc3b8e3de	4.8GB
frappe/erpnext:v16.34.1	4.94GB
postgres:16	663MB
mariadb:11.8	484MB
redis:6.2-alpine	42.4MB
mongo:<none>	1.13GB
postgres:16-alpine	411MB
alpine:latest	13.6MB
```

当时还有 10 个数据卷（合计 369.9 MB），主要是老项目 CI 的
`pearnly-ci-business-sample_database`（364 MB）与 `pearnly-ci-business-sample_sites`；
它们随 VM 一并删除。

## 清理后的本机状态

```
colima list
PROFILE    STATUS     ARCH       CPUS    MEMORY    DISK      RUNTIME
pearnly    Running    aarch64    6       12GiB     120GiB    docker
```

只剩我们这一个 VM。栈照常（24 个容器 running），DMS 验证脚本 25/25 通过。
