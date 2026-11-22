# 需求影响评估：第三方账本变化即时同步（Express / MR.ERP / 云端 Express）

- 状态：**主会话 11-22 审定照批**（三项见 §12 结论）；服务端 + 契约测试施工中（分支 `pearnly/ledger-instant-sync`），
  CC 桌面版部分等 1.12.23 进 main 后随下一版（与库存 K1 同车，主会话协调）；合入前等主会话回复。
- 基线：`origin/main` = `21f452b9a`（线上 release-20261120-1422）。评估工作树 `.claude/worktrees/ledger-instant-sync-1eeddc`，施工时从最新
  `origin/main` 开 `pearnly/ledger-instant-sync`。
- 证据：仓库只读梳理（文中路径）、测试机1 SMB1 实测（§11.1，2026-11-22 13:47–14:15）、MR.ERP 源码副本只读查（仓库外
  `/Users/skin/mrerp-source/src`）。没连生产，没写任何真实账套。

## 0. 需求原文与理解

- 原话：用户 2026-11-20 同意方向「变更通知 + 增量同步」：第三方账本里的变化尽快同步到网页 / CC；Express 由 CC 桌面版用 Windows
  文件变更通知盯账套，报变了哪几张表，服务端只读那几张并推网页；MR.ERP 查 webhook / 修改时间做增量；两边加「用时再核一次」；
  Express / MR.ERP / 云端 Express 统一一套逻辑；公司页「同步 Express」「同步 MR.ERP」按钮一一对应。
- 理解：会计在 Express 或 MR.ERP 里改了客户 / 供应商 / 商品 / 仓库 / 结存 / 应收，ERP 网页、手机、LINE、CC 里看到的和用到的跟着变，
  从现在的最坏约 20 分钟（Express）/ 1 小时（MR.ERP）缩到 Express 约半分钟、MR.ERP 用到时即核；写单与选主档时不拿过时的档。
- 不在范围：不改写账逻辑；不新增 Cowork 网页的同步按钮（Cowork 已有「重新读取」）；不做云端 Express 的读取实现（现在只有占位标签，
  无任何读取代码，只把接口留成同一形状）；不做 MR.ERP 写入路径改动；不改办公室网络 / SMB 设置。

## 1. 现有同类与复用

现状（路径均为仓库内）：

| 环节 | 现在怎么做 | 周期 |
|---|---|---|
| CC 看账套变化 | 只有 `os.stat` 轮询：`master_refresh` 比 SCCOMP / GLACC（大小, mtime, inode），`express_masters.mark()` 对 8 张主档表（`MARK_TABLES`，:59）取 size:mtime 摘要，`ar_mark` 只看 ARTRN | 名单 60 秒；主档标记由服务端请求 |
| 服务端发现 Express 主档变了 | `erp express/masters.check_marks`（:197）每 10 分钟发 `express_marks` 请求，下一轮比 `ERP Company.express_mark` | 最坏 10–20 分钟 |
| 读与落库 | `queue` → `advance`（每分钟）发 `express_masters` **整套**读 → 后台 `_land` / `apply`（只写变化的行） | +1 分钟 + 读落库 |
| 应收 | `collection/snapshot.marks_seen` 比 `ar_mark`，`snapshot.advance` 每分钟落 `ERP Receivable Snapshot` | 近 30 天用过收款的账套 |
| 兜底 | `sync_all` 每小时 | 1 小时 |
| 推网页 | 只有手动同步做完推给发起人（`pearnly_erp_express_sync`）；Frappe 保存文档时原生 `list_update` | — |
| MR.ERP | `mrerp.masters.sync_all` 每小时整读（11 个请求 + 登录），写单后一次，选账套一次；无标记、无游标；**总开关 `MRERP_LEDGER_ENABLED=False`** | 1 小时 |
| 用时再核 | CC 写前 `uid_mismatch`（退回 + `rescan_soon`）；`masters.forget` / `_stock_stale`；收款写前按客户现读应收；MR.ERP 写入时同会话现读小主档与菜单；**没有「选主档前现核」** | — |
| 服务端叫醒电脑 | `requests_waiting` / `request_wake`，领单回复带 `rescan.master_refresh` | 领单 3 秒 |

结论：**扩展现有**，不新建同步体系。

- CC：新增一个文件监视模块，产出与现有 `MARK_TABLES` / `ar_mark` 同口径的「哪本账套、哪几张表」；经现有心跳（复用「扫完即报」最快 5 秒
  不等下一拍的通道，worker.py:1004-1018）上报。
- 服务端：心跳收到「变了的表」直接 `masters.queue(company, tables)`，跳过 10 分钟 `check_marks` 的两轮延迟；应收变了直接走
  `snapshot.marks_seen` 同一入口。
- 读：`express_masters` 请求加可选 `tables`，只读、只落这几类（`apply` 本来就按类别 + 只写变化的行）。
- `check_marks`（10 分钟）与 `sync_all`（每小时）**保留为兜底**：旧版 CC、监视失效、in-place 改动未关文件（§11.1）都靠它。

## 2. Frappe 原生能力

- 定时：沿用 `scheduler_events`（已有的 advance / check_marks / sync_all），不加新定时任务。
- 推网页：选择框每次搜索都现查库，落库后下一次搜索就是新的；公司页状态沿用 `pearnly_erp_express_sync` 推给发起人。不自建推送
  通道。（施工时核实：ERP 主档页是产品 Page，原生 `list_update` 不会让它们自动重画，见 §14。）
- 公司页按钮：原生 `frm.add_custom_button` / Page 按钮，沿用 `company.js` 现有「同步 Express」写法。
- 后台：`frappe.enqueue`（已有 `erp_masters` 队列，同公司只留一件）。

## 3. 按类通用

- 类别：「第三方账本主档 / 结存 / 应收读回」。覆盖 Express（本地 / 服务器共享盘）、MR.ERP；云端 Express 只留同一形状的接口。
- 统一形状（纯契约，放共用层 `pearnly_core`，不依赖 Frappe）：`ledger_change = {ledger: express|mrerp|express_cloud, book_key,
  kinds: [customer, supplier, product, warehouse, stock, receivable, ...], source: watch|mark|probe|manual|after_write, at}`。
  表名 → 类别的映射在各账本自己的规则里（Express：ARMAS→customer、APMAS→supplier、STMAS→product、STLOC→stock/warehouse、ARTRN→receivable …）。
- 产品侧一个入口 `ledger_changed(company, kinds, source)`：排读 → 落库 → 推网页，三种账本同一条；公司页状态字段 `express_sync_*`
  已被 MR.ERP 共用，按钮按账本出「同步 Express」/「同步 MR.ERP」。
- Cowork：同一份心跳变化也交给 Cowork 的 `request_refresh`（投影只在内容哈希变了才升版本，天然去重）。范围：Cowork 只接
  「变化 → 触发已有刷新」，不改 Cowork 页面。

## 4. 上下游数据

- ERP 内：主档落库 → 采购 / 销售选择框、库存一览、收发存、对账、写账载荷（`facts`）。落库仍只写变化的行；结存对齐仍是
  「Express 结存 + 未写入单据变动」，库存调整流水照旧，`_stock_stale` 防「读取之后又有回执」照旧。
- Express：**只读**。监视用只读打开目录句柄（`FILE_LIST_DIRECTORY`，共享读写删），不锁文件、不影响 Express 与我们自己的写入锁
  `PEARNLY.LCK`。我们自己写入也会触发事件 → 服务端照常读一次（落库只写变化，成本可忽略），不做「忽略自己写入」的特判。
- CC 桌面版：心跳加一个可选字段 `ledger_changes`（不是写账载荷，不升 `payload_contract` 版本）；服务端 `report_rules` 加净化
  （账套键必须是本机已报账套、表名白名单、条数上限）。旧版 CC 不报 → 服务端照旧 10 分钟标记兜底；新 CC 连旧服务端 → 多余字段被忽略。
  因为服务端先上，按「CC 更新走心跳」口径先部署服务端再发 CC。
- 服务端要告诉 CC 盯哪几本：只盯「已接公司的现用账套」（ERP 公司绑定的 `express_book` + Cowork 已选账套），不盯 574 本全部。
  名单经现有心跳回复下发（加一个 `watch_books` 字段），CC 按名单增删监视。
- MR.ERP：只读查列表接口与整读导出，写入路径不变。
- DMS：不涉及（DMS 有自己的 eventlog 增量）。

## 5. 隔离与权限

- 心跳经机器认证（`pcc_` / `exp_` 令牌），变化只认这台电脑已报的账套键，服务端再按「账套 → 公司」映射，只排这家公司的读；
  别的租户 / 站点收不到（站点各自一份心跳）。
- 网页推送 room 按公司文档，只推给能看这家公司的人（服务端判权限，不靠前端过滤）。
- 「同步 Express」「同步 MR.ERP」按钮：沿用现有 `team.manage` 权限（服务端 `express.api.sync_company` 已拦）；MR.ERP 按钮同一权限。
- 跨公司不串：同一账套挂多家公司时（同税号不同分店）按现有 `express_book` 映射逐家排，与 `sync_all` 一致。

## 6. 入口：网页、手机、LINE、拍照

- 网页（桌面 / 窄屏）：列表靠原生 `list_update` 自动刷新；公司页收到推送后重画状态。窄屏同一页面。
- 手机 / LINE：LINE 收款、LINE 编辑页选对方 / 物料时读 ERP 库，数据新了自然新；LINE 收款写前已现读应收，不变。
- CC 对话：CC 工具查 ERP 库，跟着新；CC 网页端不做额外推送（用时取）。
- 拍照：不涉及（识别后选主档同上）。
- 「用时再核一次」（两边）：
  - 写单前：Express 写前 CC 已有 `uid_mismatch`；新增「准备载荷前看一眼标记」——后台 `push.send` 准备时若该账套最近一次变化
    时间晚于 `facts` 时间，先只读变了的那几类再准备（在后台任务里做，不在网页请求里等）。MR.ERP 写入本来就在同一会话现读小主档，
    不变。
  - 选主档前：打开选择框不阻塞等电脑（CC 往返 3–15 秒会让选择框卡住）。做法：选择框打开时若本公司距上次核对超过 2 分钟，
    后台发一次轻量核对（Express：`express_marks`；MR.ERP：§8 的列表探测），变了就排读，读完 `list_update` 刷新。
    **是否接受「不阻塞、读完自动刷新」见 §12 第 1 问。**

## 7. 共用件与连带回归

`python3 scripts/check-impact.py --files apps/pearnly_core/pearnly_core/cc_desktop/machine.py
apps/pearnly_erp_portal/pearnly_erp_portal/express/masters.py companion/src/companion/worker.py` 命中共用件 **cc-desktop**
（14 个入口：erp.cc.connect / write / masters / stock-void / companies / chat-push / stock-transfer、cowork.cc.channel、
cc.operator.page、cc.mrerp-login、erp.mrerp.direct / masters、cc.desktop.bridge / places / write、cowork.cc-chat.push、
erp.collection.server、erp.line.collection、erp.entry.choices、shared.transaction）。施工时逐入口按闸门实测记「通过 / 不通过 / 没测到」。

新冒烟 / 测试：
- `companion/tests/test_ledger_watch.py`：事件 → 表 → 类别、去抖合并、缓冲溢出 / 句柄失效 → 报「全部」并重挂、名单增删。
- `tests/test_ledger_changes.py`：心跳净化（陌生账套键 / 非白名单表丢弃）、变化 → 只排这家公司、只读这几类、旧 CC 无字段走标记兜底。
- 产品地图新增 / 更新 `erp.cc.masters`（「改 Express 主档后约 20 分钟跟上」改成实测数）、新增 `erp.mrerp.sync-button`。
- `companion/` 改动要与 1.12.23 分支协调：**碰 companion/ 前先问主会话**（任务书要求）。

## 8. 存储与成本

- 固定月费：**0**。不加常驻实例、不加定时任务（只复用已有的）、不加云服务。
- CC 桌面版：一个后台线程，每本盯的账套一个目录句柄，`ReadDirectoryChangesW` 阻塞等待，**空闲 CPU 为 0**、无轮询；内存几十 KB / 本。
  用 ctypes 直调（同 `account_lock.py` 写法），不加 pywin32 依赖、安装包不变大。
- 办公室文件服务器：每个句柄在服务器上挂一个待决的 SMB NT_TRANSACT_NOTIFY_CHANGE 请求，无数据流量；只盯已接公司的现用账套（通常个位数）。
- 服务端：变化时多一次「只读变了的表」的请求，比现在 10 分钟一次整套标记 + 整套读更省。去抖：同一账套 3 秒内的事件合成一次；
  同公司排队只留一件（现有）。
- MR.ERP：没有 webhook、没有修改时间（§11.2）。增量只能做「列表探测」：客户 / 供应商 / 物料三个列表接口各 1 个 POST（返回
  `[id, 码, 名]`，MR.ERP 测试账套 级别几 KB），摘要变了才整读。代价是**每次都要有登录会话**：会话缓存 20 分钟，过期就要重新登录，
  而登录会把会计在浏览器里同一账号踢下线（`account.py:3-4`）。所以探测只在「会话还热」或「有人正在用这家公司」时做，
  不为探测单独登录；见 §12 第 2 问。

## 9. 删除、作废、追溯

- Express 里删档：现有落库「没了的停用」不变；变化触发后按类整读该表，删除能被发现（监视也报 Deleted / Changed）。
- Express 里作废单：影响结存 / 应收 → STLOC / ARTRN 变化 → 只读这两类。
- 追溯：公司上 `express_synced_at` / `express_sync_state` / `express_sync_note` 照旧；新增记「最近一次变化来源」（watch / mark /
  probe / manual / after_write）到 `express_sync_note` 不新增字段，便于判断即时监视是否在工作。
- 已写进 Express 的单作废：不涉及（不改写账 / 作废路径）。

## 10. 文案与官方 UI

- 新增：公司页 MR.ERP 一节按钮「同步 MR.ERP」（th / zh / en / ja，照「同步 Express」现有四语改账本名）。新增文案，按官方风格直接写。
- 不改已有文案；状态文案沿用现有「读取中 / 落库中 / 已同步 / 电脑离线 / 失败」。
- 不加任何说明性文字（不显示「即时监视已开」之类）。
- 官方组件：原生按钮与 `show_alert`，无自定义样式。交付附 `python3 scripts/check-copy.py --report`。

## 11. 测试与验收

### 11.1 SMB1 变更通知实测（已做，测试机1，2026-11-22，主会话批准、占用表已登记）

条件：测试机1（Wi-Fi，SMB1）以 skin3 普通权限跑 `System.IO.FileSystemWatcher`（底层即 `ReadDirectoryChangesW`）盯
`\\办公室文件服务器\d$\ACCOUNT\70EXP\test`，同时每 10 秒比一次 dbf/cdx/fpt 的大小与修改时间作对照。触发方式不改数据：把 DBF 表头 32 字节
原样写回（内容不变，只更新修改时间）、在目录里建 / 删临时文件；测完按写前快照还原修改时间，256 个文件逐个 SHA256 与快照一致；测试机临时脚本、计划任务、进程清零，占用表已写释放。

| # | 动作 | 通知 | 对照轮询 |
|---|---|---|---|
| 1 | 打开 → 写回 → 关闭 STCOD.DBF（13:47:47） | 13:47:58 Changed（**约 11 秒**） | 13:48:04 看到 |
| 2 | 连写 STMAS / ARMAS / APMAS（13:48:15） | 13:48:26 三条 Changed（约 11 秒） | 同时看到 |
| 2b | 建 / 删临时文件 | Created / Deleted **立即**（< 0.1 秒） | — |
| 3 | 打开不关，写回（大小不变），**60 秒后才关** | 写入时**没有**事件；关闭后 11 秒才 Changed | 关闭后才看到 |
| 4 | 临时 DBF：打开不关，原地写（大小不变） | **没有**事件 | 没变 |
| 4b | 同一句柄不关，追加 512 字节（大小变） | **6 秒后 Changed**（文件仍开着） | 同时看到 |
| 5 | 空闲约 3 分钟后再写（13:55:23） | 13:55:34 Changed（约 11 秒） | 13:55:38 |
| 6 | 空闲约 15 分钟后再写（14:10:09） | 14:10:20 Changed（约 11 秒），监视未掉 | 14:10:24 |

结论：
1. SMB1 + Wi-Fi 下变更通知**可靠可用**，延迟约 6–11 秒，与修改时间轮询同步，不会比轮询更晚。
2. **限制**：文件一直开着、原地改（大小不变）时，通知和修改时间都要等关闭才出现。Express 新增单据是追加记录（交易表、凭证表变大）
   → 及时；原地改（改主档名称、结存行数量）若 Express 一直开着那张表，要等它关表。这是文件系统本身的限制，轮询也一样看不到，
   所以 10 分钟标记兜底的价值也有限；能补的是「用时再核一次」时读表头 / 内容。施工时用服务器版 Express 实测它保存后是否关表（§13）。
3. 未测（今天条件不具备）：另一台客户端写入时的通知（测试机2 今天不可用；通知由服务器产生，按协议同样有效，施工验收补测）；
   Wi-Fi 断线 / 办公室文件服务器 夜间关机后的句柄恢复（设计上按 Error / 超时重挂 + 报「全部」兜底，施工验收补测）。

### 11.2 MR.ERP 增量能力（只读查）

- 代码与 MR.ERP 源码副本里**没有 webhook、没有修改时间 / 更新日期字段、没有按修改时间过滤的参数**。源码里的 `mrerplog` 只记登录 /
  登出（`component/php/mrerplog.php`，只被 `login/mainmenu.php`、`login/logout.php` 调用），不是变更日志；同厂商 DMS 的 eventlog 账务系统没有。
- 唯一抓手：列表接口每行 `[id, 码, 名]`，id 自增 → 能发现新增、改名、删除；改价、改地址、结存变化发现不了（结存只能读报表）。
- 所以 MR.ERP 方案 = 列表探测（便宜）+ 用时再核 + 手动「同步 MR.ERP」+ 每小时整读兜底（已有）；**不是真正的增量**。

### 11.3 施工后的验证

- 单元 / 离线：§7 两份新测试 + 现有 `test_erp_express_direct.py`、`test_erp_mrerp_*`、`companion/tests` 全跑；候选离线检查（生产镜像）。
- 测试机实测（测试机1，C 盘或 70EXP\test，先排队）：服务器版 Express 改一个客户名 / 开一张单 → 计时到 ERP 列表变化；断 Wi-Fi 30 秒恢复、
  CC 重启、名单增删后监视恢复；旧版 CC 走标记兜底。
- 受影响流程：§7 的 14 个入口逐个记结果。
- 用户要看的：在 Express 里改一个客户名，半分钟内 ERP 网页列表、CC 里搜到的是新名字；公司页 MR.ERP 一节有「同步 MR.ERP」按钮。

## 12. 要问用户 / 主会话的点

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | 选主档时「用时再核」是阻塞等结果，还是不阻塞、读完自动刷新列表？ | 不阻塞：选择框照常秒开，变了的话几秒后列表自己刷新；写单前那次在后台准备时核，不让人等 | 阻塞的话每次打开选择框要等电脑 3–15 秒，电脑离线时更久 |
| 2 | MR.ERP 读主档会顶掉会计在浏览器里同一账号的登录（MR.ERP 同账号只能一处在线）。要不要让客户给 Pearnly 单开一个 MR.ERP 读取账号？ | 先不加：探测只在会话还热或有人正在用这家公司时做，不为探测单独登录；手动「同步 MR.ERP」照常登录。等真实客户用起来有被顶掉的反馈再定 | 若要更勤地探测（例如每 5 分钟），不开单独账号会频繁踢掉会计 |
| 3 | MR.ERP 总开关 `MRERP_LEDGER_ENABLED` 现在关着，MR.ERP 部分施工完也看不到。随本批一起做（开关关着、测试里打开验）还是等开关打开再做？ | 随本批做：契约统一、按钮和探测都在开关后面，测试里打开验 | 推后则「统一一套」要分两次改 |

### 12.1 主会话审定（2026-11-22）

1. 选主档「用时再核」不阻塞、读完自动刷新——同意；**写单前的核对仍按现有写前检查走，不放宽**（本批不改写前路径）。
2. 不要求客户单开 MR.ERP 读取账号，探测只在会话还热时做——同意；**不能因为探测新增任何会顶掉会计登录的登录次数**，对比见 §12.2。
3. MR.ERP 部分随本批做，只在测试环境打开开关验证；**线上总开关保持关**（11-16 定，不随部署打开），要打开另问用户。

### 12.2 MR.ERP 登录次数：改前 / 改后

| 触发 | 改前 | 改后 | 说明 |
|---|---|---|---|
| 每小时整读（`sync_all`，非长期没人用的公司） | 会话过期就登录一次（20 分钟缓存，几乎每次） | **同左** | 不变 |
| 写单后一次（`after_write`） | 推送那次的会话还热，通常不登录 | **同左** | 不变 |
| 选 / 建公司、重新选账套（人点的） | 清被踢标记、照常登录 | **同左** | 不变 |
| 公司页「同步 MR.ERP」（新，人点的） | 没有这个按钮；人只能「重新选一次账套」触发同一次整读 | 清被踢标记、照常登录 | 与原来「重新选账套」同一条路径，只是有了直达按钮 |
| 用时再核探测（新） | 无 | **0 次登录**：`account.use(login=False)` 只接缓存里还热的会话；没有 / 过期 / 被踢过 / 正在推送都不发请求或不登录 | `tests/test_mrerp_account_session.py::WarmProbe` 用假站点数登录次数锁住 |
| 探测发现变了之后的整读（新） | 无 | **0 次登录**：同一个热会话（`run(warm=True)` → `read(login=False)`）；恰好过期就算了、不记失败 | 同上 |

结论：自动路径的登录次数与改前相同；新增的只有人点「同步 MR.ERP」那一次（等同原来重新选账套）。

## 13. 工作量与排期

按顺序（机器耗时估算）：

1. 共用契约 `ledger_change` + Express 表→类别映射 + 心跳净化（服务端）：写代码约 1 小时，离线测试约 15 分钟。
2. ERP / Cowork 接 `ledger_changed`：按类只读只落、跳过 check_marks、推网页、写前核（后台）：约 2 小时 + 测试 30 分钟。
3. CC 桌面版 `ledger_watch`（ctypes ReadDirectoryChangesW、去抖、重挂、名单）+ 心跳字段：约 2 小时 + companion 测试 20 分钟；
   **先问主会话与 1.12.23 分支的协调**，再动 `companion/`。
4. MR.ERP：列表探测、公司页「同步 MR.ERP」按钮、四语：约 1 小时。
5. 闸门 + 候选离线（约 30 分钟）+ 受影响流程（本机约 30 分钟）+ 测试机实测（约 1 小时，含服务器版 Express 关表行为、断网恢复，
   办公室文件服务器 21:00 前做）。
6. 合入前发主会话等回复；部署先服务端、再发 CC。

依赖：1 → 2 → 3（CC 依赖服务端字段，但可并行写）；4 独立；5 在全部之后。

## 14. 施工记录（服务端，2026-11-22）

基线 `origin/main` = `21f452b9a`，分支 `pearnly/ledger-instant-sync`。

| 位置 | 改动 |
|---|---|
| `pearnly_core/ledger_sync/contracts.py`（新，纯函数） | 统一变化形状 `{ledger, book_key, kinds, source}`；Express 表名 → 类别（主档 8 张 + ARTRN + STCRD / STTRN / APTRN）；心跳 `ledger_changes` 净化（只认本机报过的账套键、认识的表，`*` = 全部） |
| `pearnly_core/cc_desktop/machine.py` | 心跳收 `ledger_changes` → 产品钩子 `ledger_changed`（保存点内，出错回滚到保存点、记日志、不挡心跳；1020 / 1213 照旧整笔重来）；回复带 `watch_books`（产品给账套键，共用层换成这台电脑的本机路径，按电脑缓存 5 分钟，账套名单变了清缓存） |
| `pearnly_core/cc_desktop/products.py` | 钩子声明加 `ledger_changed`、`watch_books`（可选；没声明 = 不盯、不收） |
| `pearnly_erp_portal/express/live.py`（新） | `ledger_changed`：账套 → 同税号公司里现用这本的 → 主档类 `masters.kick`、应收类在用收款的账套整本刷新；`watch_books`：已接公司的现用账套；`touch` / `check`：用时再核（120 秒一家一次、排后台，不在请求里写库） |
| `pearnly_erp_portal/express/masters.py` | `kick`（当场发起读取，在读时记「读完再来一轮」，在读满 6 家就排队）、`check_soon` + `advance` 里比用时再核的标记（不等 10 分钟）、`check_marks` 抽出 `_check_one`；10 分钟标记、每小时整读照旧 |
| `pearnly_erp_portal/masters/access.py` | 主档权限查询（选择框搜索、主档列表）按当前公司调 `live.touch`（只读一次缓存） |
| `pearnly_core/mrerp/session.py` / `account.py` | `may_login` / `use(login=False)`：只接热会话，没有就 `ERR_NO_SESSION`，绝不登录；过期的热会话丢掉 |
| `pearnly_core/mrerp/master_export.py` | `digest`（三张列表 `[id, 码, 名]` 的摘要，整读与探测同算法）、`probe`（热会话、5 秒锁）、`read(login=)` |
| `pearnly_erp_portal/mrerp/masters.py` / `api.py` | `probe_soon`（摘要变了才用热会话整读）、整读后存摘要、`start` + `api.sync_company`（公司页「同步 MR.ERP」，管理员） |
| `public/desk/company.js`、`i18n/messages.tsv` | 公司页 MR.ERP 公司多「同步 MR.ERP」按钮（复用同步状态文案与推送监听）；新词条 `company.syncMrerp` 四语 |

与评估的出入（如实记）：
- **推网页**：ERP 的主档页是产品自己的 Page，不是原生列表视图，原生 `list_update` 不会让它们自动重画；选择框每次搜索都现查库，
  落库后下一次搜索就是新的。本批不加额外推送（评估 §2 那句改为此结论）。
- **只读变了的那几类**：要 CC 桌面版认 `express_masters` 的表参数，旧版不认；本批服务端仍整套读（落库只写变化的行），按类只读随
  CC 桌面版那一版一起做。
- **Cowork**：Cowork 的机器通道是它自己的一套（ADR-025 登记的债），本批只接 ERP；Cowork 接 `ledger_changes` 随 CC 桌面版那一版评估。
- **写单前**：不改（主会话定：按现有写前检查）。

### 14.1 验证（服务端，提交 `46f861e68`）

| 层 | 结果 |
|---|---|
| 本地同名闸门（`development.yml` 全部 285 步，含 `npm ci`） | 通过 |
| 生产镜像 `29ae2ea7`（= 线上 release-20261120-1422 的 `21f452b9a`）候选离线车道（本机，113 组） | 通过，`LANE EXIT 0`；`test_mrerp_account_session.py` 12 项（含新增热会话探测 5 项）真跑未跳过 |
| 受影响流程（本机实验栈 `pearnly-lis-lab`，`run.py --changed --post-status`） | 11 条流程、两套数据、两种宽度 38/38 通过，状态已回写 |
| GitHub：Development checks / Candidate checks / Flows | 三项通过 |

共用件连带回归（`check-impact.py`：cc-desktop 14 个入口、mrerp-rules 7 个、erp-masters-access 10 个）：
- 流程覆盖并通过：erp.company.actions / connect / masters.same-period、erp.link.ledger-first.mrerp、erp.writer-at-claim、cc.machine.operator-ai、
  erp.profile.save、erp.team.bulk-disable 等（见上）；主档隔离与选择框由 `test_erp_express_direct.MasterIsolation`（查询条件不变、用时再核按当前公司）守住。
- **没测到（原因）**：要测试机 CC 桌面版实机的入口（erp.cc.connect / write / stock-void / stock-transfer、cowork.cc.channel、cc.desktop.*、cc.mrerp-login）——
  本批 CC 桌面版不变、心跳只多认一个可选字段、回复多一个旧版忽略的字段，旧版 CC 走原路径由 `test_ledger_instant_sync.Heartbeat`
  （不带字段什么都不做、产品出错不挡心跳）守住；实机在 CC 桌面版那一版（带文件监视）一起测。
- 没上线验证、没真机验收；MR.ERP 部分线上总开关关着，线上看不到。
