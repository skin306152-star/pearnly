# 页面加载性能体检（2026-11-14）

分支 `pearnly/page-perf-audit-50db59`，基线 `a838abe54`（origin/main）。主会话任务书：先量盘点明细、再全产品体检、再按类修并加闸。
进度与给用户的一屏在共享笔记 `性能体检-进度-11-14.md`、`性能体检-一屏-11-14.md`。

## 1. 起因与线上证据

用户 11-13 晚在 testerp 的 นิว ลิงค์（CO-00146）建盘点 STC-186247（251 行），明细页「要加载半天」。
11-14 一次生产只读（nginx 计时日志 + erp-web 容器里只读查询，跑完删临时文件）：

| 时间 UTC 11-13 | 接口 | 服务端（nginx urt） |
|---|---|---|
| 14:18:38 | `api_create_stock_count`（251 行快照） | 7.9 秒 |
| 14:18:47 / 14:18:55 / 14:19:06 / 14:19:53 | `api_stock_count_review`（明细） | 7.4 / 6.0 / 6.4 / 6.2 秒 |
| 同页 | `api_list_stock_counts` / `api_stock_count_total` | 0.07–0.32 秒 |

同时段静态文件 rt 66–121 秒而服务端 0.1 秒：那是线路（另一条线），与本记录无关。STC-186247 现已不存在（14:21 有一次删除盘点的请求），无法原样复量。
testerp 表规模（精确计数）：结存 20,220、流水 20,705、物料 59,430、客户 84,912、仓库 771。客户 / 物料这么多是自动建公司造成的
（公司接入窗口查实，11-14）；结存按物料查 EXPLAIN 为整表扫（`type ALL`，rows 20,225）。

## 2. 原因（本机实验栈按线上表规模复现）

`scripts/perf/seed_big_company.py` 造同量级数据，`scripts/perf/measure.py` 量：

| 情形 | 明细接口 | 查询条数 |
|---|---|---|
| 改前 | 0.83 秒 | 1,362（仓库名按行 552 次、库存令牌每物料 3 条 × 251） |
| 只加索引（提交①） | 0.39 秒 | 1,362 |
| 加索引 + 按名单取（提交①②） | 0.04 秒 | 64 |

两个原因叠加：逐行查询（N+1），且结存表按物料没有索引，每条逐行查询都整表扫一遍。

### 线上 6–7 秒与本机 0.83 秒差 7–8 倍的解释（可核对的推断）
- 同一分钟里与盘点无关的小接口也慢同样倍数：`api_stock_count_total`（约 8 条查询）线上 0.075–0.315 秒、本机约 15 毫秒；
  `api_list_stock_counts`（9 条）线上 0.07–0.21 秒、本机 15 毫秒 → 线上每条查询（含 Python 往返）约 5–20 倍于本机。
  按这个比例：1,362 条 × 线上每条约 4.5 毫秒（本机 0.6 毫秒 × 7.5）≈ 6.1 秒，与实测 6.0–7.4 秒对得上。
- 线上为什么每条更贵：多租户共用一个 MariaDB（3G 内存上限、缓冲池被所有租户分），网页进程与库之间走容器网络，晚高峰有后台同步争库
  （交接第十五节：同晚 CO-00146 落库同步）。这几项各占多少没拆开量，**不当结论**；倍数关系是从日志读出的事实。
- 改后估算：64 条 × 约 4.5 毫秒 ≈ 0.29 秒，且不再整表扫；目标 < 300 毫秒，余量不大，上线后必须实测。

### 上线后在 testerp 的验证（由主会话排一次只读）
1. 被动：部署后有人打开盘点明细，读 nginx 计时日志里 `api_stock_count_review` 的 urt（不新增任何写入）。
2. 主动：在 testerp 找一份 ≥ 200 行的盘点（没有就请主会话安排在测试公司 CO-00146 建一份「盘点在库」，不动库存），
   在 erp-web 容器里用 `scripts/perf/measure.py`（每次回滚）以盘点创建人身份量 `api_stock_count_review`，记用时与查询条数。
3. 目标：服务端 < 300 毫秒、查询 ≤ 80 条；同时读一次结存按物料的 EXPLAIN，应走 `product_warehouse_index`。

## 3. 全产品体检（第 2 步）

方法：`scripts/perf/lab_tracer.sh` 把实验栈网页进程换成 `trace_server.py`（按请求记用时与查询条数），`scripts/perf/pages.py`
用无头 Chrome 按产品地图逐页打开（先预热一遍）。线上另读 nginx 近 7 天 `/api/method/*` 服务端耗时分布。

改前本机（ERP 大账套）：只有盘点明细超标（0.80 秒 / 1,363 条）；团队名单 101 条（同一语句 23 次，随人数涨）；其余 20 页首屏接口
≤ 45 毫秒、≤ 21 条。Cowork / DMS 实验库数据少，只看形状。改前改后逐页对照见第 5 节。

线上近 7 天服务端 p95 超标的读接口：

| 接口 | 中位数 | p95 | 次数 | 结论 |
|---|---|---|---|---|
| ERP 盘点明细 | 0.44 秒 | 6.2 秒 | 25 | 已修（提交①②） |
| ERP 新建盘点 | 1.8 秒 | 7.9 秒 | 6 | 写入路径：每行原生插入约 8 条（编号、外键校验、写入守卫），本机 251 行 0.97 秒 / 2,032 条；去掉了逐行「先查有没有」，其余要改批量写，另行评估 |
| Cowork `companies.api.detail` | 0.72 秒 | 2.4 秒 | 1,365 | 取数依赖 Express 账套连接、电脑文件夹等，实验栈造不出像线上的数据；需线上测试租户只读量（见第 6 节） |
| Cowork `companies.api.month` / `pending` / `pending_counts` | 0.3–0.6 秒 | 1.2–1.9 秒 | 529–1,418 | 同上 |
| ERP `work.api.board` | 0.97 秒 | 2.6 秒 | 6 | 本机 32 条，重复的是「当前用户是否启用」同一请求查 7 次，次数固定、不随卡片 / 成员涨，不是逐行查询；未改 |
| CC `assistant.api.session_files` | 1.36 秒 | 13 秒 | 1,115 | 不是查库，见第 7 节 |
| 原生 `getdoc` | 0.49 秒 | 2.1 秒 | 56 | 框架原生，未动 |

## 4. 修法（三个提交）

1. `perf: index …`（机械）：结存 `product_warehouse_index`、`warehouse_product_index`，盘点行 `count_product_index`，盘点记录
   `count_item_index`；Frappe 原生 `on_doctype_update` + 补丁 `v1_29_stock_count_indexes`（已有同名就跳过；提交说明里写的 v1_28 在变基时因 main 新进 v1_28_admin_collection_menus 改名为 v1_29）。
2. `perf: … by list instead of per row`（行为不变）：ERP 盘点明细 / 查找 / 记录 / 别处结存 / 导出表 / 录入后重算合计按名单一次取
   （`count._warehouse_labels`、`count_state.stock_tokens`；加锁读仍逐个取，锁的行与先后不变）；ERP、Cowork 团队名单按名单取账号、
   角色、公司、姓名。证据：`scripts/perf/equal_check.py` 旧模块与新模块同参调用逐字段比——ERP 盘点 30 组（含库存令牌 252 个、录入后重算
   读回）、ERP 团队 11 组、Cowork 团队 7 组，全部相等。
3. 闸门与可重复测试：`scripts/check-loop-queries.py`（循环里查库，含经本仓函数间接查库；基线 941 处只许减少）+ 单测，进
   development.yml；`scripts/perf/`（造数、测量、逐页、上限表 `budgets.json`）。

## 5. 改前 / 改后（本机实验栈，ERP 大账套）

`scripts/perf/pages.py --budget scripts/perf/budgets.json`，改后 ERP 23 页、Cowork 9 页、DMS 4 页全部在上限内。
（改前那遍没有预热，Cowork 各页改前的 100–500 毫秒多是冷启动；改后先预热再量，页面「可用」改后是首页整页重载、其余页内换路由。）

| 页面 | 可用（毫秒） | 首屏接口最慢（毫秒） | 单个接口最多查询 |
|---|---|---|---|
| ERP 盘点明细（251 行） | 893 → 171 | 802 → 67 | 1,363 → 65 |
| ERP 团队 | 214 → 129 | 63 → 40 | 101 → 33 |
| ERP 其余 20 页 | 104–220 → 110–125 | ≤ 45 → ≤ 69 | 不变（≤ 21） |
| Cowork 团队 | 209 → 114 | 54 → 14 | 86 → 7（含冷启动差） |
| Cowork 其余 7 页 | 不超 2 秒 | ≤ 35 | ≤ 35 |
ERP 其余页的毫秒差在同一台机器的波动范围内（实验栈经 amd64 模拟），查询条数逐页不变。

## 6. 未做与交接

- Cowork 公司详情 / 月份 / 待处理：需线上 testcowork 只读量一次（同 `measure.py`，可与上线后 testerp 复量合成一次 SSH）；
  量到逐行查询再按本记录的做法改，不是就记结论。
- 新建盘点的逐行原生插入：已在第二批改成批量写（见第 9 节）。
- 看板：同一请求里重复做当前用户权限检查（7 次，固定），可按请求缓存，收益小，未做。

## 7. CC `session_files`（转引导式 / CC 线）

调用链：对话页每次打开对话、每轮跑完都 `GET session_files` → `assistant.files.listing` → 对这段对话的每一段远端会话
`_segment_files` → `engine.reader(...).files(remote)`，**同步**调智能体引擎的文件列表接口（外部 HTTP），段越多调用越多；附件再按缓存补大小 / 页数。
线上近 7 天中位数 1.36 秒、p95 13 秒、最大 18 秒，慢在外部接口，不是查库。能不能不同步等：可以——本站自己上传的附件编号本站就有，
可先回本站已知的附件、引擎产出的结果文件后取或按段缓存（文件生成后不变，跑完一轮时失效一次）；对话页先出、文件列表随后补。
这是 CC 线的事，本任务只报告不改。

## 8. 共用件连带回归（check-impact）

`scripts/check-impact.py` 命中 `erp-count-code` 等 12 个入口。流程用例（`scripts/flows/run.py --changed --base origin/main`，本窗口实验栈
`pearnly-perf-lab`，基点 a838abe5）：16 条流程 × new / old × 电脑 / 手机共 59 格，**59/59 通过**；其中 `erp.stocktake.new` new 两格首轮
因新开户站点没有库存（已知的流程间数据依赖，见共享笔记「已知问题与结论」11-01 条）不通过，先跑一格入库造库存后补跑通过
（造库存的 `erp.stock-in.units` new/desktop 那一格自己没通过：单位弹窗「台数与数量不符」，不在本次改动文件里，未追）。

| 入口 | 结果 | 依据 |
|---|---|---|
| erp.stock-count.code（盘点按码认商品） | 通过 | 流程 erp.stocktake.scan / web-code / gs1 / new-product、equal_check lookup_count 按码 / 按物料 |
| erp.stock-count.adjust（结束 → 调整单） | 通过 | 流程 erp.stocktake.adjust |
| erp.cc.stocktake（CC 对话盘点） | 通过 | 候选离线车道 111 组（生产镜像 77e2cec0） |
| erp.stock-count.upload（上传 / 凭证照片） | 通过 | 流程 erp.stock.excel-units；equal_check template_rows |
| erp.stock-count.new-product | 通过 | 流程 erp.stocktake.new-product |
| erp.records.scope（按公司） | 通过 | equal_check list_counts / list_items（同一权限过滤，结果逐字段相等） |
| erp.line.binding / cowork.line.binding | 通过 | 流程 erp.team.actions / cowork.team.actions；改动的 list_members 只读绑定状态，绑定 / 解绑函数未改 |
| shared.team.members | 通过 | 流程 erp/cowork.team.add-member / full-name / bulk-disable |
| shared.navigation / shared.shell-gate | 没测到（原因：改动函数不在侧栏与开通闸的调用链，命中是按文件） | — |
| shared.scan-codes.web-address | 通过 | 流程 erp.stocktake.web-code |

本地全闸门（`gate.sh`，照 development.yml）243 步全过；候选离线车道 LANE EXIT 0。

## 9. 第二批：新建盘点改批量写（分支 `pearnly/stock-count-bulk-create`）

用户原话抱怨的也包括新建（线上 7.9 秒）。本机 251 行原先 0.97 秒 / 2,032 条（每行原生 `insert`：发号加锁、外键校验、写入守卫各查一遍）。
做法：复用公司接入第 1 批的 `express/bulk.py`（预留编号段 + 照 `Document.insert` 写库前那一段逐行准备 + 多行 INSERT），不另造一份。
对 `bulk.py` 两处扩展，主档同步行为不变：`reserve_here`（在当前事务里预留、不提交——新建盘点必须单头与行一起成败，同 Frappe 逐条发号
锁到请求结束）；`prepare(..., express_sync=True)` 可选参数（盘点行传 False）；命名规则放宽到 `STC-ITEM-{#####}` 这类多段前缀。
盘点侧：`count._insert_snapshot` 先锁一次盘点单，逐行写入守卫不再各锁一遍（`count_state.validate_document` 认这次请求已锁的单）；
快照行差异直接写 0（原先插入后 `_refresh_totals` 逐行补写 0），最终库里一样。

| 情形 | 改前 | 改后 |
|---|---|---|
| 251 行（本公司仓） | 0.97 秒 / 2,032 条 | 0.10 秒 / 30 条 |
| 2 万行（另一家公司的仓） | 242 秒 | 10 秒 |

行为不变的证据：`equal_check.py erp-count-create`——旧版（origin/main 的 count.py）与新版各建一次（各自回滚，编号从同一个计数器起），
单头与全部盘点行除时间戳外逐字段相等（251 行、2 万行两组）；`erp-count` 读路径 30 组复核仍相等；公司接入的
`scripts/erp-masters-bulk-lab-verify.py` 在改后的 `bulk.py` 上重跑通过（failures 为空）。
