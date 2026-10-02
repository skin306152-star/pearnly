# 统一上线 batch-next（2026-10-02 第三批）

集成分支 `codex/batch-next`，基于 `main` b078e796，快进合入。按「攒成一批、过闸、一次构建、一次激活（含 foundation）」上线。

## 改动

| 来源 | 内容 |
| --- | --- |
| codex/cowork-history-index-patch（派发窗口，cf41d070） | 发票历史 workspace 索引随 migrate 建（e72676ac）；检查点开头列一次目录（597484dc，读与新建合并随之做到）；flash-lite 全球入口两次没回音换备用入口（768d4597）；派发分到多个 Cloud Tasks 队列、站点 `shards` 开关，默认 1（432e562e）；列表总数只在第一页装满时数、20 秒内沿用（cf41d070） |
| codex/scaling-base（本窗口） | 数据库 / Redis 地址可配置；按核数与内存算容量参数（`sizing.py`，现值不变）；`docs/development/production-capacity.md`（`2026-10-02-scaling-base.md`） |
| codex/redis-state（本窗口） | 锁、登录 state、票据、在途任务状态挪到不淘汰的 redis-queue（`2026-10-02-redis-state.md`） |
| codex/offsite-backup（本窗口） | 每日异地备份到 GCS 新加坡、恢复演练脚本、MariaDB binlog 7 天（`2026-10-02-offsite-backup.md`） |
| 集成时 | 候选离线道查出 DMS 富菜单、快照两条离线测试只替换了 `frappe.cache`，补成同一个替身；状态存储没配地址时报明缺的配置（d0d7a953） |

## 关

1. 本地 Development checks（按 `development.yml` 逐步）全过；GitHub Development checks（d0d7a953）success。
2. 生产镜像完整候选离线道（`074775d1`，挂整批代码，`--memory 2g`、只读挂载）：46 步全过（第一次跑出上面两条测试问题，修后重跑）。
3. 闸 ①：标签修订版 batch3（cowork-ocr-00067-sow，从 5ddae892 构建）在 testcowork 跑两批金标全部形态（11:04:12–11:16:40Z，跑完切回并回读），
   `--count-missed`，对照 batch2（上一批上线前、同样 testcowork 3.8）：

| 指标 | 金标 287（batch2 → batch3） | 扩充 646 |
| --- | --- | --- |
| 印了的读错 | 32 → 30 | 144 → 140 |
| 字段总错 | 41 → 42（/2039） | 210 → 214（/3910） |
| 其中没印却填了 | 9 → 12 | 66 → 74 |
| 漏掉的真票 | 1 → 1 | 4 → 4 |
| 整份漏掉 | 0 → 0 | 3 → 3 |
| 非票据入账 | 19 → 17 | 114 → 113 |

   - 逐格：金标新错 8 / 改对 7（分散 7 / 6 份文件），扩充新错 23 / 改对 19（18 / 15 份）；新错最多的是没印分店填了 00000，多在没有 Vision 原文的直读票上。
     两轮拿到文字层的文件对称（4 / 3、6 / 10），没有系统性丢原文。
   - 兜底路径：914 份读完里 3.8 没回音 6、被限流 2；降到 flash-lite 读完 1 页（无新错）；flash-lite 换入口 0 次。失败 / 不明两轮同样 3 份。
   - 本批没改读法：按 `verification.md` 闸 ① 新增的「没改读法的批次」口径判过关（压测窗口转用户口径，2026-10-02）。
4. 闸 ②：上线后由压测窗口在测试租户跑 100 人（待补）。

## 部署与回读

待补。
