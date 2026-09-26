# DMS 会话复用、白天增量同步与账号并发（2026-09-27）

决策见 [ADR-013](../adr/0013-dms-session-reuse-live-sync.md)。

## 授权与范围

- 用户 2026-09-27：研究"读档为什么慢、白天主档变化怎么实时同步到 LINE"，随后"现在你针对方案可以做验证和 dms 的改动"；并发问题"一起做，按你说的顺序来""4 的接口也留出来吧，配置功能写好"。
- 工作树 `/Users/skin/.codex/worktrees/dms-fast-sync/pearnly`，分支 `codex/dms-fast-sync`，基线 `origin/main` = `76b6a6e`。
- 外部读取只在 DMS-TEST（`testdms.pearnly.com` 的 EP-00001 / `dmstest`），经系统已配置的连接只读进行；不写第三方业务、不发 LINE、不做 OCR。用户给出的测试账号密码未被本 Agent 手动输入任何网页。

## 只读实测（DMS-TEST，线上镜像 `b2e32d17` 容器内执行）

| 项目 | 结果 |
| --- | --- |
| 客户列表 | 每页条数不设上限（2000 一页照常返回）；40 条 0.05–0.1 秒、约 480 字节/条。行内有客户编号、姓名、13 位证号、建档人与建档日期（只到天），**没有**电话/地址/修改时间；排序只有编号、姓名、证号 |
| 使用记录 | 可筛 52 个业务模块 + 4 类登录事件；无"全部业务模块"筛选。按模块计数约 0.07 秒；09-25 20:09:48 的客户更新当秒可见。全类别总数（含登录）一次请求 `dt::79940` |
| 登录 | 测试站 2–4 秒；探测进程总耗时 7–17 秒，大半在登录/登出 |
| 会话 | 只有 `PHPSESSID`；拿出浏览器用普通 HTTP 请求仍是登录态。闲置探测（间隔 1/2/4/8/16/32 分钟）与同账号再登录的结果见下节 |
| 账号配置 | ris 6 个启用连接的管理员全是 `dms001`；testdms 3 个全是 `dmstest`（绑定时主凭据即管理员凭据） |

### 会话闲置与重复登录（进行中，结果回填）

- 1 / 2 / 4 / 8 分钟闲置后仍有效（`dt::245`，0.25–0.43 秒）。
- 16 / 32 分钟：待回填。
- 同账号再登录后原会话：待回填。

## 实现

| 位置 | 内容 |
| --- | --- |
| `adapter/session_cache.py`（新） | 加密存取 cookie、存活检查、被顶下线记录；管理员 600 秒、销售 180 秒两档保留时长 |
| `adapter/session.py` | 登录先试复用；结束能复用就不登出；销售锁先拿、管理员锁用到才拿；只用管理员的读取只锁管理员；从账号池挑空闲账号；等锁心跳 |
| `adapter/admin_pool.py`、DocType `DMS System Account`（新） | 系统账号池；连接自带管理员永远在首位，池空时行为不变 |
| `connections/api.py`、`public/desk/connect.js` | 池子的列出/添加（先真登）/停用启用/移除；页面菜单入口，页面仍只有一个主按钮；状态区一行只读摘要 |
| `adapter/operations.py` | `skip_sales_session`；等锁超时 → `ERR_DMS_BUSY`；白天同步开着时不在操作员路径里翻日志 |
| `config/pacing.py`、`booking/conversation/booking_flow.py` | `ERR_DMS_BUSY` 可重试（锁没拿到就不会有任何提交） |
| `line/out.py` | 后台任务挂等锁回调：续"正在输入" |
| `adapter/live_sync.py`（新）、`hooks.py` | 白天每分钟一轮增量；窗口/间隔/开关；账号被顶掉时退让 |
| `adapter/change_sync.py` | 白天大组（客户/地址）整组重读留给凌晨 |
| `scripts/production/configure-dms-runtime.py` | DMS bench `scheduler_tick_interval=60` |
| 文案 | `ERR_DMS_BUSY` 与账号池界面 23 条，四语，进 DMS 自己的 `messages.tsv` |

第 2 步"读取不再用管理员"的结论：前台只剩订车提交前主档复核还用管理员实时读，按安全考虑保留（见 ADR 第 3 点）。

## 验证（本机，候选源码）

- 新增 `scripts/test-dms-session-reuse.py` 16 条（锁定 Frappe 16.33.0 镜像、`--network none`），已加入 `scripts/ci-candidate-checks.sh`。
- `tests/test_dms_change_sync.py` 20 条（新增"白天大组留给夜里"，旧用例固定为不延后）。
- 架构 0 违规；调用契约、客户身份、快照读取、DMS 运行配置、队列目标、架构守卫、Desk 契约、功能目录、离线套件（含额度 46/46）通过；`i18n-build --check`、前端 i18n 严格检查通过；`check-i18n --strict` DMS 相关计数与 main 相同（既有积压）。
- 镜像内：line-runtime 37、intake-queue 23、query-output 11、query-snapshots 40、snapshot-consumers 25、teardown 3、entrypoints 4。
- flow lab：line-usage-redis 5、native-flow 10、native-recovery 8、booking-flow 33/33。snapshot lab：store 27、reads 17、manual 13、queue 2。

## 未验证 / 待办

- 真实 DMS 上的会话复用、账号池、白天同步只做了只读实测，没有在测试环境做完整业务试用（AGENTS.md：需真实环境验证的改动先在 Windows 测试环境由用户试用）。
- 池里的账号需要老丈人在 DMS 后台新建，权限需与管理员相同。
- 仍会排队的：建单的全局锁（防单号撞号）。
