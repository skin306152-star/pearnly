# 不能丢的状态挪到不淘汰的 Redis（2026-10-02）

可拆分自检查出：redis-cache 是 128MB、`allkeys-lru`，m4 压测时已到 138–142MB（在淘汰），可锁、登录 state、防爆破计数、
一次性票据、进行中任务的状态都放在 `frappe.cache` 里，被挤掉就会重复执行、登录失败或绕过限流。压测窗口定：按类修、零成本。

## 修法

- 新增 `pearnly_core/execution/state.py`：`store()` 返回与 `frappe.cache` 同一个类（Frappe 的 `RedisWrapper`，键照样按站点
  `db_name` 加前缀，`get_value` / `set_value` / `lock` / `eval` 用法一样），连到不淘汰的 Redis：站点配置 `pearnly_state_redis`，
  没配用本站点的 `redis_queue`（生产 noeviction、appendonly）。`release(key, owner)` 是原子的「值仍是自己的才删」。
  Redis 类用到时才加载，离线契约检查导入不碰 Redis。
- 只挪「丢了会出错」的 24 组（只读审计逐条分类）：
  - core：`session_lock`（账号互斥）、`line_binding`（绑定码分配锁、防爆破计数）、`line_session`（会话锁）、`identity`（OIDC state /
    nonce / PKCE；发现文档仍是缓存）、`account/oidc_session`（refresh token 与轮换锁）
  - DMS：`config/pacing`（失败计数与冷却）、`booking/delivery`、`booking/recovery`（租约；游标仍在缓存）、`adapter/change_sync`、
    `adapter/session_cache`（只挪「被踢」标记；cookie 缓存不动）、`intake/customer_attempt`、`menu/rich_menu`（发布锁）、
    `account/api`（门户票据与锁）、`editor/browser_ticket`、`approval/approval_store`
  - ERP：`sales/entry`、`purchasing/entry`、`inventory/line_mobile`、`integrations/line_menu`（发布锁）
  - Cowork：`line/intake`（LINE 回复上下文）、`recon_center/jobs`、`recon_center/document_ocr`（进行中对账的状态）、`line/menu`（发布锁）
  - control：`issues`（开户锁）
- 可丢、能重算的（翻译、主数据、PDF 文字、菜单「已应用」标记、进度条、扫描游标等 32 组）仍用 `frappe.cache`。
- DMS 的这些模块用函数内导入（`_state_store()`），离线检查只带 DMS 也能导入。

## 顺手修的同类问题

- ERP 销售 / 采购入账锁原来是不带站点前缀的裸键（`erp-sales-post`），所有租户排同一把锁 → 加 `make_key`，按租户。
- DMS `change_sync`、`approval_store` 的锁原来是裸键、释放是「先读再删」（中间别人刚拿到的锁会被误删）→ 加站点前缀、改用原子释放。
- 控制面开户锁加站点前缀。

## 验证

- 架构检查 PASS；`check-dms-offline.py` 全过（exit 0）。
- Cowork：line_intake 27、recon_progress 13、recon_scanned_docs 121、ocr_tasks 42、recon 其余各项全过；候选离线道清单里的
  应用内测试与 main 相同（`test_orient` 在本机镜像缺模型文件，main 上同样报错）。DMS / core 脚本 `test-session-lock`、
  `test-dms-session-reuse` 等全过。
- 测试只改了一处：原来把替身装在 `frappe.cache` 上的，同一处再把状态存储指向同一个替身，断言一条不改。
- 未部署。上线时 redis-queue（160MB、noeviction）多出的都是带过期时间的小键；上线后看 `INFO memory` 与键数。
