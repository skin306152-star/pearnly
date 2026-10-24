# 10-25 夜间改进（主会话安排，用户睡前授权「完成任务（包括新发现的问题）再睡眠」）

不部署（明早主会话定）；每项单独分支、单独提交，都过共用件连带回归闸门（check-impact 清单 + 实验栈冒烟 + 验收窗口复核）。

| # | 项 | 分支 / 提交 | 状态 |
|---|---|---|---|
| 1 | 【高】大数据量列表卡死：客户 / 供应商 / 公司 / 收银班次改服务端分页 + 服务端搜索；冒烟页面打开时限 3 秒 | `pearnly/large-lists` | 实现 + 本地用例；实验栈 testerp（7.4 万客户）：客户页从卡死到 1.2–1.8 秒、供应商 1.1–1.3 秒，按税号 / 编码 / 名称搜各约 0.35 秒、总数 73,918 计数 0.15 秒；ERP 全部页面 < 3 秒。待验收复核 |
| 2 | 【中】401 自愈 + 按需请求 502 退避（CC 1.12.1） | `pearnly/cc-self-heal` | 实现 + CC 全套 1078 用例；测试机装 1.12.1 实测：实验库作废令牌后 04:07:20 首次 401、04:08:58 满一分钟再 401 → 成员标 `rejected` 并停下（之后 0 条 401），还原后恢复。对话页打开时自动带 rekey 换新这一步要人在 CC 窗口打开对话页，待验收 / 明早实测 |
| 3 | 【低】扫码探针相机没画面时尽早报出原因 | 已在 main `7327ed9e` | 完成 |
| 4 | 【中】同一本账套换了路径写法自动认回 | `pearnly/book-identity-taxid` | 架构上已支持（Cowork 按目录文件号认、ERP 账套标识有文件号只认文件号）；生产 ccdemo 只读实证：DEMO01 本地路径已自动认回 C$ 共享登记；补回归用例；更正发布记录里「要重选账套」那一步 |
| 5 | 【低】打税号找账套 | `pearnly/book-identity-taxid` | 根因：`connections.service.targets()` 账套目录白名单没放行 `tax_id`；已放行 + 用例 |
| 6 | 【中】CC 窗口里直接切换 Cowork / ERP 站点 | `pearnly/cc-site-switch` | 用户菜单列出这台电脑连着的站点（当前打勾，点另一个直接切；重开回到上次站点原本就有）；不新增文案；cc-visual 截图场景 `user-menu-sites`。对话页随服务端镜像发布 |

新发现（不是本批引入，报主会话）：库存卡页偶发未处理报错（`Cannot read properties of undefined (reading 'parent')`，约四次一次，页面仍能用）；
实验栈与生产共用域名时，CC 主取任务线程的长连接 lab-start 后仍连着生产（DNS 改了也不重连），实验栈写 Express 前要重启一次 CC。

## 第 1 项扩大口径：Frappe 原生能力全面排查（分支 `pearnly/frappe-native-lists`，叠在 `pearnly/large-lists` 上）

用户口径（主会话转述）：「Frappe 自带的原本好的功能我们要利用起来，不要只管一处，其它或者未来会出现的这类问题要一并考虑进入」。

### a. 前端重写 Frappe 能力的排查（只读排查，按风险排）

| # | 位置 | 问题 | 处理 |
|---|---|---|---|
| 1 | ERP 收发存汇总 `stock-card.js` + `inventory/report.py` | 一次取全；服务端每个键扫一遍全部流水，每行单查一次仓库名；前端排序后切页 | **已改**：新接口 `api_stock_summary_page`，服务端排序加分页。按商品 / 仓库 / 仓位排序（默认）时只读这一页商品的流水；按数量列排序时整批算一次（按键一次分组）再切页。仓库名一个仓库查一次，单品明细页同样处理。旧接口 `api_stock_summary` 保留 |
| 2–4、8 | 客户 / 供应商 / 收银班次 / 公司 | `limit: 0` 一次取全 | 已在 `pearnly/large-lists` 改 |
| 5 | 盘点明细（ERP / Cowork `stocktake.js`） | 整张盘点单全量；每次搜索都重建 DataTable | **保留**：整份快照是结束盘点时 `review_token` 校验的依据；DataTable 自带虚拟滚动，几千行可用。下一批可改为不重建、只刷新数据 |
| 6 | 共用筛选 `list.js` `p.filter_values` | 只认「等于」和「介于」，其余条件（不等于、包含、大于）都被当成「等于」去查，结果是错的 | **已改**（主会话 05:00 定：按原生运算符真正实现，不删选项）：新增 `p.filter_conditions` 与共用 `pearnly_core.desk.list_filters`，其余运算符原样交给 `frappe.get_list`。已接销售、采购、收银小票、库存一览（含导出）、收发存、Cowork 录入、物料页（上架 / 停用）。Link 字段（供应商、仓库、商品）的「包含 / 不包含」用框架原生的 `Link字段.名称字段` 过滤（验收 10-25：原来按编号匹配，按名称搜命中 0；先取编号再 in 会截断，改成 JOIN）。树形与其它 App 配进来的条件这些服务端不认，下拉不列 |
| 7 | 共用 `select.js`（全站 Select 换成 Autocomplete） | 超过 99 个选项不打字就看不到 | **已改**：弹层长度跟选项数走（同原生 Select），显式 `max_items` 优先 |
| 9 | 看板（两个产品 `work.js`） | 整块看板、归档全量，前端过滤 | **保留**：原生 KanbanView 不支持镜像卡、清单、多板。归档分页放下一批 |
| 10 | 看板导出 | 卡片号拼进 GET 网址，上千张会超过网关的地址长度上限 | **已改**：改用 POST（服务端 GET / POST 都收） |
| 11 | CC 待办 `pending.tsx` | 只在前 200 条里搜 | **下一批**：把 search 交给服务端 `companies/pending`（属于正确性问题，不会卡死） |
| 12 | Cowork 账套 / 科目 / 文件夹下拉 | 外部数据全量取回，前端过滤 | **保留**：不是 DocType，不能用 Link；上限已显式写成 1000 / 2000 |
| 13 | ERP 公司菜单 | 公司放在启动数据里 | 保留（几十到上百家）；超过时改 ControlLink |
| 14–16 | 仓库、团队、自动规则、`entry-choice` | 量小，或已经是服务端带上限的搜索 | 保留，并进基线 |
| — | `link.js`、`p.table`、`p.export_file`、DMS 与多数 ERP / Cowork 列表、POS 商品、CC 公司与会话列表 | 原生或服务端分页 | 无需处理 |

### b. 服务端无上限查询（静态扫描，不含 tests / patches：508 处候选，高 37、中 71、低 400）

- **高风险**：大多是 ERP 的 `api_list_*` 把前端传来的 `limit` 原样交给 `limit_page_length`，前端传 0 就整表取回。这条链在客户、供应商、公司、班次上已修（large-lists）。
- **其余高风险，排进下一批**：
  - 用 `len(get_list(limit 0))` 算总数：商品、小票、出入库、采购。
  - 期间汇总在 Python 里逐行累加：采购汇总、录入汇总、身份证录入汇总。
  - 库存一览先全量取回，在内存里排序、分页。
  - 建盘点单、盘点模板时一次取单仓全部结存。
  - CC 同步时一次取公司全部档案，应改成按 name 游标分块。
  - Cowork 历史按金额排序时取全量。
  - Cowork 买方匹配、税表计算时取全部客户。
- **中风险**：随时间增长的列表，如看板归档、一张单的全部行、按工作空间的记忆。
- **低风险**：主键单行、一张单的子表、按用户 / 设备、小配置表、单行聚合。这类可写 `# bounded:` 豁免。

### c. 防止再犯

- `scripts/check-unbounded-queries.py`，CI 步骤「Block new unbounded queries…」。拦截范围：
  - Python 里 `frappe.get_all / get_list / db.get_all / db.get_list` 没给上限，或上限写成 0 / None。
  - 前端 `limit: 0`、`p.sort_rows(`。
- 基线 `scripts/unbounded-queries-baseline.json`：116 个文件、343 处，按文件计数，只许减少，修掉后用 `--update` 降下来。天然有界的写 `# bounded: 原因`。
- 第二期（排查给的设计，未做）：
  - 识别「接口参数透传 `int(limit)`」和 `len(查询)`。
  - 识别 `frappe.db.sql` 里没有 LIMIT 的 SELECT。
- 规则写进 `docs/development/architecture.md`「列表、选择与搜索」和 `AGENTS.md`「官方 UI」。冒烟的 3 秒时限已在 large-lists 分支的 `verification.md`「大数据量」。

### d. 验证

- 本地用例：
  - `tests/test_opening_report.py`：14 项，新增两项。一项断言分页结果与整批算出的同一行完全一致、只读这一页商品的流水、一个仓库只查一次；另一项覆盖按名称 / 数量排序、未知排序字段、上限截断。
  - `tests/test-desk-list-filter.cjs`：新增，覆盖条件下拉与两路拆分。
  - `tests/test_list_filters.py`：新增。覆盖 !=、like、not like、in、not in、>=、介于、时间段、为空的成功用例，以及白名单与非法运算符丢弃。另有销售记录两项：条件进销售记录查询；收银小票是否列出按条件判断。
  - `tests/test-native-desk.cjs`：Select 选项数。
  - `tests/test_check_unbounded_queries.py`：新增。
- **实验栈实测（testerp 大账套，收发存页 3 秒时限）、验收复核：未做**。电量不足，明早接着做。

## 10-25 约 05:40 暂停时的状态（本机电量到 10%，主会话通知暂停）

已合入 main，一次合一个，每个都回读了远端 SHA：

| 分支 | 合入后 main | 清理 |
|---|---|---|
| `pearnly/large-lists` | `92ca4366` | 工作树、本地分支、远端分支都已删除 |
| `pearnly/cc-self-heal`（CC 1.12.1 代码） | `e93d3307` | 同上 |
| `pearnly/book-identity-taxid` | `fce01cd0` | 同上 |

远端 main = `fce01cd0`。

还没合，已推送，串成一条链变基在 `fce01cd0` 上：

| 分支 | 远端 SHA | 验收 | CI |
|---|---|---|---|
| `pearnly/cc-site-switch` | `fa924011` | 通过（窄屏场景已按意见修） | 暂停时在跑，明早看 |
| `pearnly/safety-net-gaps` | `95c06b51` | 通过（5 处入口已按意见改） | 暂停时在跑，明早看 |
| `pearnly/spa-errors` | `e4564e4b` | 上一版有 1 处不通过（登录会话上报因缺 CSRF 返回 400），已修，待复核 | 推送后未看 |
| `pearnly/frappe-native-lists` | `118946c7` | 上一版有 2 处不通过（Link「包含」按编号匹配、物料页没接条件），已修，待复核 | 推送后未看 |

- 链条最上面那个分支的本地全量闸门：123 步，无失败。
- 合入顺序：cc-site-switch → safety-net-gaps → spa-errors → frappe-native-lists。每个都要 CI 绿、验收通过，再快进 main、回读。
- 临时工作树 `.claude/worktrees/night-batch-check`（本地分支 `pearnly/night-batch-check`，没推送）：只用于今晚实验栈合并冒烟，没有独有成果。保留到上面四个分支都合入后删除。

明早清单：

1. 看上面四个分支的 CI，按顺序合 main；后两个先等验收复核新提交。
2. CC 1.12.1 正式版发布：代码已在 main，今晚按口径没发，由主会话 / 用户决定。
3. 第 7 项 Cowork 推送写 Express 自动冒烟：没做，要用测试机。
4. 验收没测到的：
   - 对话页打开时自动带 rekey 换新密钥：要人在 CC 窗口操作。
   - Cowork 录入筛选、Cowork 看板导出：要起 Cowork 栈。
   - Vue / React 错误边界上报：要换镜像。
   - LINE 真机：三星手机 adb 掉线，要人插线。
   - 大数据量下收发存的 3 秒时限：测试租户流水太少，证明不了。
5. 冒烟建议：实验栈刚起时先预热一轮再计时（冷启动误报：供应商 6.5 秒、Cowork 首页 frappe is not defined）。
6. 服务端剩余高风险项，排下一批：
   - 用 len() 算总数。
   - 在 Python 里逐行累加的汇总。
   - 库存一览全量取回。
   - CC 同步时整批取档。
   - CC 待办只在前 200 条里搜。
   - 盘点明细每次搜索都重建表格。
   - 看板归档没有分页。
7. 原有的：S 盘取样（accserver 白天在线）、库存方案评审、库存卡页偶发报错 `reading 'parent'`。

## 10-25 白天：服务端高风险剩余项（分支 `pearnly/server-heavy-queries`，叠在 `pearnly/stock-movement-index` 上）

| 项 | 处理 |
|---|---|
| 用 len() 算总数（先全取回再数） | **已改**：新增共用 `list_filters.count()`，用原生 `get_list` 的 COUNT 聚合，跟着权限、在数据库里数。覆盖客户、供应商、公司、收银班次、商品、采购、盘点单、出入库单（新 `count_entries`）、收银小票（新 `count_sales`，与列表共用 `_sale_query`）、销售记录（`total()` 两侧各数，收银那侧与列表一样用 `get_all`）。大数据量闸门基线 343 → 335 |
| 收银小票按客户名搜 | **已改**：原来先把名字命中的客户编号全取出来拼成大 IN，现在用原生 Link 点号条件 `customer.customer_name` |
| 采购列表的合计（`records.overview`，每翻一页都跑） | **已改**：原来借 `rows(limit=0)` 取全部单据，每行还查 3 次库（附件数、分类、分类名）。现在只取合计要用的几列，不做逐行附加查询。合计按行舍入再累加，口径不变，所以仍逐行算 |
| 采购列表当页的附件数 / 分类名 | **已改**：一页一次批量查（原来每行 3 次） |
| CC 待办只在前 200 条里搜 | **已改**：搜索词交服务端（`companies.api.pending(search=)`，前端停止输入 300 毫秒后再取）。库里先按单号、名称、税号、文件名、单据号用原生嵌套 OR 找出全部命中，再与最近的候选一起按页面同一口径细判（原因、金额、日期这类算出来的字段只能细判） |
| 采购期间汇总 `purchase_summary`、Cowork 旧录入汇总 `intake.summary`、DMS 身份证汇总 `id_card.summary` | **不改**：已经没有页面调用（Cowork `IntakePage.vue` 路由不可达，另外两个没有调用方），并进基线；下一批可清理 |
| CC 同步整批取档（`express/masters.py`） | **保留**：要拿这家公司的全部档案做差异对比（建 / 改 / 停用），是后台任务、只取必要的几列，按游标分块不减少总量。行数随这家公司的主档量走 |
| 库存一览全量（`stock._overview_rows`） | **待测后定**：看验收用 30 万行假流水测的库存一览时间；超过 3 秒再改成库内分页 |

验证：
- `tests/test_list_filters.py`：计数是一次聚合查询、收银那侧用 `get_all`。
- `tests/test_cowork_companies.py`：待办搜索能找到最近候选窗口外的单，并按页面口径细判。
- 前端 `tsc` 通过。
- 实验栈实测、验收复核：待做。
