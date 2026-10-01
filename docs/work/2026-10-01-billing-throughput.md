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

## 同批：抢 CPU 时网页与数据库先跑

压测窗口补交（用户问"并发时前端慢怎么解决"）：半量时 long-worker 峰值 454%、ocr-worker 269%，与 cowork-web（324%）、mariadb（224%）抢同一台
8 核机器，后台一忙页面就慢。`infrastructure/production/compose.yml`：后台（各产品 worker、ocr-worker、long-worker、dms-sync-worker、
scheduler、ocr-dispatch）`cpu_shares: 256`，其余（web、frontend、socketio、billing-web、mariadb、redis、keycloak）保持默认 1024——只在争抢时
前台 4:1 先跑，空闲时后台照样用满整机，不限核数。底座服务配置不动，激活产品时不会连带重建数据库；后台容器在激活产品时本来就重建，
随统一部署生效。`check-production-capacity.py` 新增一项钉住（25 项通过）。

## 同批：对账结果页存档（用户："不要再改一点修一点"）

用户打开三类对账结果"进不去"：页面没坏，是 `recon_center.sheets.load` 每次打开都重算对账、生成整本工作簿、再画成页面（压测窗口：557 行的银行对账
空闲时一次 10 秒，压测时更慢）。改成存档：

- `recon_center/view_store.py` + 新表 `Cowork Recon View`（无任何角色权限，只由服务端读写）：按「对账类型 + 任务 + 页面语言 + 界面语言」存一份
  结果页数据，带指纹（任务修改时间与修订号、本工作空间这类对账的预设条数与最后修改时间、重跑问题记录、生成结果页的代码与文案的内容摘要）。
- `sheets.load` 先按指纹取存档，对得上直接回；对不上现算一次、存回。对账出结果时（`jobs._prepare_view`，在「完成」通知之前）按发起时的
  语言先生成一次；保存修改、回答预设都回 `load`，跟着重新生成。删除对账时存档一起删。没有存档的旧任务第一次打开现算并补存。
- 导出 Excel 不改：导出读任务完成时存下的结果行（各自的 `export`），本来就不重算对账；换成结果页那份会改变导出内容的来源。
- 测试机（testcowork 测试副本，2821e5c1 镜像 + 本分支实时源码）：三类共 58 套，「现算 / 第一次打开（现算并存）/ 第二次打开（读存档）」
  三份逐字一致；读存档 p50 0.005 秒，最大那套收入对账（结果页数据 5 MB）0.16 秒，现算 3.58 秒。挑一套银行对账原样保存一次：修订号
  1 → 2，存档跟着更新、与新的现算一致。`tests/test_cowork_recon_view_store.py`（候选离线道）。

## 验证

- 镜像里：`test_cowork_source_lock_scope.py`（查找不加锁、查到的按主键锁、判定不变）、`test_control_billing_usage_counter.py`
  （钱包一条 UPDATE、计价输入不变）、`test_cowork_billing_wait.py`（新，候选离线道）、`recon_waiting` / `recon_scanned_docs` /
  `recon_speed` / `recon_progress` 通过；`test_control_billing_gate.py` 通过。
- 还缺：计费服务部署（billing-web 只换镜像、不动数据库：停 → 用旧镜像备份 → migrate → `up -d --no-deps`，不跑 activate-foundation）、
  部署后压测窗口同场景复测（从一开始每 10 秒探测页面响应）。
