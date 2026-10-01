# 三租户半量压测：计费服务被打穿、直传登记 1205（2026-10-01）

压测窗口报（2821e5c1，12:39–12:55 UTC，三租户各 18 套对账 + 发票共 491 张，从 Mac 发起）：1020 已消失（开始对账失败 0/54），
识别吞吐每分钟约 100 份（上一轮约 10），单套 p50 285–329 秒（上一轮 710–918 秒）。新问题：
- cowork-web 55 条 `billing.status 查不到 … billing-web Read timed out`；对账 6 套「暂时查不到账户余额」直接失败；
- 直传登记 `files.api.finish_upload` 12 次 1205 Lock wait timeout；
- 数据库锁等待峰值 53 个事务、最长 124 秒；客户端约 400 个请求 60–120 秒拿不到响应。

分支 `codex/billing-throughput`，基线 main `2821e5c1`。

## 证据（生产只读）

- testcowork 的 Error Log：12 条 1205 全在 `service.py:81 _new_source < finish_direct < finish_upload`（插入一行原件）；6 条对账失败在
  `document_ocr.submit → intake.balance`（查余额 lookup_error）。billing.internal 没有错误日志（是调用方超时）。
- `pearnly_control/billing.charge`：每笔先 `select … from tabPearnly Tenant … for update`，钱包 `for_update`、按参考号带锁查台账，
  同一租户的扣费串行；钱包开着修改留痕，`wallet.save()` 每笔在锁里多写一条 Version。台账两个带锁查询的索引在（`usage.ensure_indexes`）。
- billing-web 是 2 进程 × 4 线程 = 8 个处理位，全平台所有租户、所有产品的扣费与问余额共用。

## 改法

1. **直传登记 1205（上一批 1020 修复的副作用）**：`files.access.protected_sources` 按二级索引加锁读一个"没有的值"时，MariaDB 锁住前后两条记录之间
   的空档，别的请求往空档里插原件（直传登记）就得等那个事务结束。改成查找一律不加锁，查到了才按主键把那几行原件锁住（只锁记录）。
   本机样板实测：同一场景，原来插入被挡（3 秒超时），现在 0.00 秒插入成功。代价：本事务开始后别人刚提交的同内容原件这一次看不到
   （时间窗是一个请求之内）；判什么算原件不变。
2. **扣费锁里少做事**：`billing.charge` / `_charge_with_subscription` 写回钱包、套餐用量改成一条 UPDATE（`_store_wallet`），不整份保存、
   不每笔多写一条 Version；每笔扣费仍在台账里有一行，计价、阶梯、幂等都不变。充值、调账、开套餐（低频）照旧整份保存。
3. **计费服务处理位**：`billing-web` 默认 2 × 16 线程（`PEARNLY_BILLING_WEB_THREADS` / `_WORKERS` 可调）。等锁的线程几乎不占内存；
   每线程最多一个数据库连接（MariaDB 151）。容量检查 24 项通过。
4. **对账查不到余额不再直接失败**：`recon_center.waiting.balance`——`lookup_error` 时交还进程、30 秒后再跑一轮查，最多等 10 分钟；
   余额不足照旧马上报。对账文档（`document_ocr.submit`）与销项税核查发票（`invoice_ocr`）都走它。录入发票提交被拒 `lookup_error`
   时浏览器本来就会隔几秒重交（`intake-upload.js` RESUBMIT），不改。

## 验证

- 镜像里：`test_cowork_source_lock_scope.py`（查找不加锁、查到的按主键锁、判定不变）、`test_control_billing_usage_counter.py`
  （钱包一条 UPDATE、计价输入不变）、`test_cowork_billing_wait.py`（新，候选离线道）、`recon_waiting` / `recon_scanned_docs` /
  `recon_speed` / `recon_progress` 通过；`test_control_billing_gate.py` 通过。
- 还缺：计费服务部署（billing-web 只换镜像、不动数据库：停 → 用旧镜像备份 → migrate → `up -d --no-deps`，不跑 activate-foundation）、
  部署后压测窗口同场景复测（从一开始每 10 秒探测页面响应）。
