# 服务器架构改造：内存兜底、改动记录 5 分钟异地、甲骨文守望与演练（2026-10-17）

- 授权：用户 10-17「服务商已经回复了，按计划做：LINE 取图走甲骨文、内存超配修复、监控和数据库副本都做；动生产的操作提前和另一个窗口约好时间。」
  其后「数据库按你的建议：每 5 分钟同步改动记录到谷歌，甲骨文每月试恢复一次，加上每分钟检查网站、打不开发邮件给我。」
- 分支 `pearnly/oracle-edge`，基线 `786c376d`；并行窗口「工作清单窗口交接」（CC 外壳等，文件不重叠），生产操作逐项先约。
- 背景：SSD Nodes 工单 #752222 10-17 答复——到 LINE 取图绕洛杉矶是上游互联问题，不修；可免费搬东京（曼谷到东京 99–110ms，到新加坡 30–44ms，不搬）。

## 实测（10-17，各一次）

| 从 | 到谷歌（asia-southeast1） | 到 LINE 取图 api-data.line.me | 到 Anthropic |
|---|---|---|---|
| 生产 SSD Nodes | 建连 3–6ms（10-16 时 205ms，光缆修复后恢复） | 新连接首字节 1.6s；同一连接后续 0.32s | 200–290ms |
| 甲骨文 free-arm | 3ms | 0.38–0.56s | 2–4ms |

## 做了什么

1. **内存超配兜底**（`7b806173`、`5cc1862e`）
   - 生产 08:04 UTC 加 16 GiB swap（`/swapfile-pearnly`，swappiness 10，写入 fstab）；不重启容器。
   - mariadb、redis-cache、redis-queue 在线 `docker update` 为不许用 swap（启动时间未变），compose 同步加 `memswap_limit`。
   - `capacity.py`：上限之和 ≤ min(物理内存 × 1.3, 物理内存 + swap)；`--wait-seconds`：只在「现在的余量不够」时最多等 10 分钟（配置错误立即报）。
     `activate-product.sh` 先跑 `ensure-swap.sh`（幂等）再以 600 秒等待做容量检查；`bootstrap-host.sh` 新机器也建 swap。
   - 证据与未解：另一窗口只读分析（`~/pearnly-devtools/handoff/memory-analysis-2026-10-17.md`）指出 09-28、10-05 也有非激活时刻的骤降，
     「超配 + 无 swap」是必要条件、触发点仍未知。swap 覆盖平时与部署两种时刻；后台容器统一封顶（systemd slice MemoryMax）改宿主设置，未做，待用户定。
2. **LINE 取图经甲骨文中转：实测后撤销**
   - 建过 Cloudflare 隧道 `pearnly-oracle-edge` + `line-relay.pearnly.com` + 甲骨文 nginx/cloudflared，生产到中转连续请求 0.9–20s：
     Cloudflare 免费版把生产的请求送到东京 NRT / 巴黎 CDG 边缘，不保证新加坡。当天全部删除并回读（容器、镜像、隧道、解析、本机凭据、代码）。
   - 真正的慢点是 TLS 握手（每个往返 0.32s）；复用连接后 0.32s。DMS 已复用；没有另改。
3. **改动记录每 5 分钟异地**（`b50a724f`）
   - `scripts/production/binlog-ship.sh` + `pearnly-binlog-ship.{service,timer}`（`*:2/5`）：一次性客户端容器 `FLUSH BINARY LOGS`，已关上、未传过的
     gzip 后经只写账号传 `gs://pearnly-backups-sg/binlog/<UTC 日期>/<关上时刻>-binlog.NNNNNN.gz`；首次只传最近 26 小时。存储桶生命周期加 `binlog/` 15 天（已生效）。
   - 每日备份 `mariadb-dump` 加 `--master-data=2`，导出文件头记下位置。
   - 本地 MariaDB 11.8 实测：两库分别导出、中间有写入、批量插入、删除、改表结构，导入后按位置分库回放，结果与源完全一致。
   - 生产上线：随部署工具同步后执行 `install-offsite-backup.sh`（同时装两个定时器）。**未上线**（见下）。
4. **甲骨文守望与每月演练**（`b50a724f`，已装到甲骨文）
   - `infrastructure/oracle-watch/`：`watch.py`（每分钟查 6 个地址：ris / mrerp / erpcowork 的 ping、登录服务、fatcc.com、pearnly.com；
     连续 3 分钟失败发信、恢复发信；每 10 分钟查当天每日备份是否在、改动记录是否 20 分钟内有新的——后者 `CHECK_BINLOG=1` 才查）；
     `drill.sh`（每月 1 日曼谷 10:00：下载最新每日备份与之后的改动记录、核 SHA256、临时库导入并按位置回放、核表数与关键表行数、
     解密密钥包，成功失败都发信）。
   - `scripts/oracle-watch/deploy.sh`：密钥经管道直接写到那台 `/opt/pearnly-watch/secrets/`（600、root）：备份只读账号新密钥、
     hello@pearnly.com 发信配置、`backup.key`（顺带在主服务器之外有了一份）、`ALERT_TO=skinzihao@gmail.com`。
   - `smtp-config.py` 的控制面容器名可用 `PEARNLY_CONTROL_CONTAINER` 指定（本机现在只有 control-web）。
   - 回读：10-17 15:32（曼谷）首轮 6 个地址全部正常，每轮约 4 秒；存储桶检查读到 2026-10-17 的每日备份；测试邮件已发出。

## 检查

- `scripts/check-production-capacity.py` 33 项、`scripts/production/test-offsite-backup.py` 25 项、`scripts/check-architecture.py` 通过。

## 未完成 / 待办

- 生产上线：本分支随「工作清单窗口交接」那批或其后一批进 main、构建、同步部署工具，然后 `install-offsite-backup.sh`、
  第一份带位置的每日备份之后在甲骨文 `deploy.sh --check-binlog` 打开改动记录检查；上线后跑一次演练确认能回放到最近 5 分钟。
- 后台容器统一封顶（slice MemoryMax）、PSI 守护：动宿主 systemd，待用户决定。
