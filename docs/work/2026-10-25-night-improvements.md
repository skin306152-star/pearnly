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
| 6 | 共用筛选 `list.js` `p.filter_values` | 只认「等于」和「介于」，其余条件（不等于、包含、大于）都被当成「等于」去查，结果是错的 | **已改**（主会话 05:00 定：按原生运算符真正实现，不删选项）：新增 `p.filter_conditions` 与共用 `pearnly_core.desk.list_filters`，其余运算符原样交给 `frappe.get_list`。已接销售、采购、收银小票、库存一览（含导出）、收发存、Cowork 录入、物料页（上架 / 停用）。Link 字段（供应商、仓库、商品）的「包含 / 不包含」按名称找出编号再查（验收 10-25：原来按编号匹配，按名称搜命中 0）。树形与其它 App 配进来的条件这些服务端不认，下拉不列 |
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
