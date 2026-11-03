# ERP 接 MR.ERP：导入直写（评估 §13 第 6 步的「导入直写」一半，11-03）

- 分支 `pearnly/erp-mrerp-direct`，工作树 `.claude/worktrees/agent-ac3d5df580195cf43`，基线 origin/main `790dc639`。没有推送、没有合入 main、没有部署。
- 依据：[评估](2026-11-02-mrerp-operator-and-erp-company-评估.md) §0、§4、§5、§12.1、§13 第 6 步、§14.4–14.6；第 5 步
  [mrerp-core-rules](2026-11-02-mrerp-core-rules.md)、第 10 步 [mrerp-cc-credentials](2026-11-02-mrerp-cc-credentials.md)、第 9 步
  [erp-cc-chat-push](2026-11-02-erp-cc-chat-push.md)、[erp-ledger-state](2026-11-02-erp-ledger-state.md)。
- 不在本步：ERP 的 MR.ERP 代录接线（`express/operator.py`、`pearnly_core/express_operator/`、`companion/` 一律没碰）；Cowork
  `connections/service.py`、`connections/mrerp_login.py` 没碰。不连任何真实 MR.ERP（mrerp4sme.com 只读也没碰）。

## 1. 影响评估（开工前）

| 项 | 结论 |
|---|---|
| 同类复用 | MR.ERP 规则与导入执行全用共用层 `pearnly_core.mrerp`（第 5 步）；Cowork 里还剩的三段纯网络逻辑（一次导入的执行、菜单 / 主档读取、账套目录采集）与「用户数据错」判定**机械下沉**（单独提交，函数体原样，Cowork 改为从共用层导入、对外名照旧），ERP 与 Cowork 调同一份，不复制。账本栏用通用 `ledger_*`；要人选的停在单据上（`ledger_needed`，同 ERP 的 Express）。 |
| Frappe 原生 | 新 DocType `ERP MRERP Connection`（JSON，系统管理员只读，Password 字段加密）；`ERP Company` 加 7 个隐藏只读字段；原生迁移加列，不写补丁、不迁数据。后台任务 `frappe.enqueue`（去重 job_id、提交后可见）。 |
| 上下游 | 上游 = ERP 采购 / 销售单（Express 写账规则的同一份单据 `express.push.document`）→ 规范载荷（Cowork 冻结的同一形状）→ 共用层导入 5 步 + 列表回读 → 单据账本栏（`ledger_target = mrerp`）。Express 写账、Cowork 推送都不变。 |
| 隔离与权限 | 全在服务端：选账本、连接、账套、读菜单只限 ERP 管理员；单据上选一次 / 对话直写沿用单据页的开单权与可见公司（`cc_tools` 原有判据）。连接 DocType 只给系统管理员。 |
| 各入口 | 网页 / LINE / 拍照提交都汇到 `express.push.queue`，在那里分路；对话直写 `cc_tools._route`；每分钟 `express.push.poll` 末尾调 MR.ERP 巡检。没有新页面、菜单、可售功能。 |
| 共用件 | `mrerp-rules`（下沉 4 个模块）、`cc-desktop`（ERP 声明 MR.ERP 登录钩子，开关开着才声明）。 |
| 成本 | 零：不调模型、不识别、不加常驻任务（复用已有每分钟巡检）。 |
| 作废删除 | 本步不做 MR.ERP 里的作废（导入没有作废）；已导入的单在 ERP 作废不回写 MR.ERP（列进限制）。 |
| 文案 | 不加界面文案（没有页面）；只引用已有词条 `Not Permitted`、`Invalid request`。 |
| 验收 | 黄金样本逐字节、开关两种状态的离线测试；真站点等岳父测试账号（§6）。 |
| 入口先不开放 | 主会话 11-03 改口径：不用站点配置，改 **ERP 代码常量** `pearnly_erp_portal.mrerp.MRERP_LEDGER_ENABLED = False`；所有 MR.ERP 入口先读它（`mrerp.enabled()`）。对用户开放时在一个提交里改成 `True`，不按站点开。没有新增站点配置，不用登记。 |

## 2. 实现

### 2.1 机械下沉（提交 `b84471b9`，逻辑不改）

| 新位置（共用层） | 原位置（Cowork） | Cowork 侧 |
|---|---|---|
| `pearnly_core.mrerp.push.execute`（`execute`、`_exception`） | `push/mrerp/runner.py` | `runner` 从共用层导入 `execute`（对外名照旧，测试 `patch.object(runner, "execute")` 照用） |
| `pearnly_core.mrerp.push.user_data`（`USER_DATA_ERROR_CODES`、`USER_DATA_ERROR_THAI_PATTERNS`、`is_user_data_error`） | `push/retry.py` | `retry` 从共用层导入，三个名字照旧（`lifecycle`、源码对照测试照用） |
| `pearnly_core.mrerp.menu_read`（`read`、`LOCK_WAIT_SECONDS`） | `connections/mrerp_menu_reader.py` | 导入 `read`；排队任务 `run` 与点路径不变 |
| `pearnly_core.mrerp.collect`（`collect`、`_collect_once`、`_collect_locked`、`_read_details`、`_reading_order` 与三个常量） | `connections/mrerp_collector.py` | 导入 `collect`；租约、快照、刷新记录、排队点路径不变 |

- 测试只改替身位置：`test_cowork_mrerp_collector` 打补丁的对象从 Cowork 模块换成 `pearnly_core.mrerp.collect`；
  `test_mrerp_core_boundary` 登记三个新模块的外部依赖（会话锁、计时），`user_data` 进纯规则；`test_cowork_push_purity` 放行 `user_data`；
  `keep-cjk.tsv` 登记 `user_data.py` 的泰文原文。
- 没有对外 RPC、排队任务或持久化点路径移动，所以不留旧路径壳（同第 5 步先例）。

### 2.2 ERP 域包 `apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/`

| 模块 | 内容 |
|---|---|
| `__init__` | `MRERP_LEDGER_ENABLED = False`、`enabled()` |
| `contracts`（纯函数） | ERP 单据（`express.contracts.document` 形状）→ 拍平票面（方向明确、本方 = ERP 公司、付款按单据、采购货 / 费按单据种类 `expense` / `service` → 费用）、规范载荷、导入结论 → 账本栏、要人选的项（共用层 `needs`）、原因码。`id` = 单据标识摘要（十六进制），MR.ERP 单号序号因此固定且不撞 |
| `connection` | 连接网址（管理员）、连接视图（不含密码）、CC 授权钩子 `login_scope`（管理员 `site`，别人空串）/ `login_store`（写 Password 字段与授权记录，此刻不是管理员拒写）、后台任务取凭据 `lease` |
| `catalog` | 刷新账套目录（后台，共用层 `collect`）、服务端分页列账套、公司选 MR.ERP 账套当账本（还勾着 Express 就拒；记「接上时刻」`mrerp_linked_at`）、取消 |
| `choices` | 读导入菜单与主档（后台，共用层 `menu_read`），选项存连接、选过的存公司（按账套键，共用层 `menus` / `masters` 的规则），选一次只收最近读到的可用值 |
| `push` | `handles`（开关关着不查库直接回假）、`queue`（提交事务里排上，`ledger_target = mrerp`）、`send`（再写一次 / 对话直写）、`run`（行锁里从排队中领成「正在导入」并提交 → 准备 → 代录占着账号就照旧排队 → 共用层导入 → 存回读到的菜单 / 主档 → 结论记回）、`answer`（单据上选一次）、`poll`（等账号的再排；「正在导入」超过 15 分钟转结果不明） |
| `api` | 管理员的薄入口（网址、账套目录、选账本、读选择、预先选一项）；不收不回密码 |

- **规范载荷**与 Cowork `_prepare_mrerp` 冻结的同一形状：`{"histories": [票面], "mappings": {"accounts": [], "clients": [], "_own_tax_id", "_mrerp_menus", "_mrerp_defaults"}}`。
- **只做采购（赊购）与销售**：MR.ERP 导入只有赊销、现销、赊购（货 / 费）；现购停下 `mrerp.purchase_cash`（同 Cowork 拒收现金采购）。出入库、收银日汇总、盘点不排。
- **同账号互斥**：导入 5 步里拿共用层短锁 `account_lock`；被代录占着（`operator_holds`）时不导、`ledger_reason = mrerp.account_busy` 照旧排队，每分钟再看，不算失败（同 Cowork runner）。
- **幂等 / 结果不明**：只从排队中领；导入发出后出错、受理了却查不到 → `held` + `mrerp.result_unknown: …`，不自动重推（`send` 也拒）；同一张单 MR.ERP 单号固定，重推遇「单号已存在」由共用层回读认领。
- 依赖方向：`express.push` / `cc_tools` → `mrerp`；`mrerp` 不导入 `express.push`（任务入口 `express.push.mrerp_run` 把建单据的函数交给 `mrerp.push.run`），也不导入 Cowork。连网的共用层（requests / openpyxl / Redis）只在后台任务函数里载入，模块在只有标准库的离线检查里也能导入（Express 入口导入它）。

### 2.3 接进 Express 那条路（提交 `68f08a33`）

| 位置 | 改动（开关关着时 `handles` 回假、不查库，行为一字不变） |
|---|---|
| `express.push.queue` | 账本是 MR.ERP 的公司 → `mrerp.push.queue`，不排 Express |
| `express.push.send` | 单据 `ledger_target = mrerp` 且开关开着 → `mrerp.push.send`（单据页「再写一次」、对话直写都经它） |
| `express.push.poll` | 跳过 `ledger_target = mrerp` 的行；末尾调 `mrerp.push.poll()`（关着直接返回） |
| `express.push.mrerp_run` | 新的后台任务入口（见上） |
| `cc_tools._route` / `_push_one` / `_outcome` / `push_choices` / `save_push_choices` | MR.ERP 分支：交直写走 `mrerp.push.send`；要人选的停下给选项、选一次走 `mrerp.push.answer`；结果不明回 `unconfirmed`；`PROGRAM` 开着时 "Express or MR.ERP"、关着 "Express" |
| `hooks.py` | `pearnly_cc_desktop` 的 `mrerp_login_scope` / `mrerp_login_store` 只在开关开着时声明（关着时共用通道照旧回 `unsupported`） |
| 待处理 `express.pending` | 不改：只按通用账本栏读，MR.ERP 的单（同样的状态码）自然进出；关着时不存在 MR.ERP 的单 |

## 3. 黄金样本（硬要求 1）

- Cowork 已有的「黄金样本 3」= `tests/test_mrerp_rules_golden.py` 的 3 项（导入表 8 张、单号预检、15 次推送），样本 `tests/fixtures/mrerp/golden-rules.json`。
- 新增 **`tests/test_erp_mrerp_golden.py`**（5 项），样本 `tests/fixtures/mrerp/erp-direct-golden.json`：
  1. ERP 单据 → 规范载荷固定样本：赊购货品、赊购费用、赊销、现销四张（采购 / 销售 / 费用类），逐字节相同；
  2. 四份规范载荷经 ERP 用的执行推假 MR.ERP：发出的请求（方法、路径、参数、表单、上传 xlsx 的成员级与整份摘要）与回执与样本相同（四张都成功：`PG690912-051353`、`PN690912-609947`、`SI690912-931946`、`SE690912-757161`，类型 purchase / purchase_expense / sales_credit / sales_cash）；
  3. 同一份载荷，Cowork `runner.execute` 与 ERP 的执行发出的请求逐字节相同；
  4. Cowork 黄金样本 3 的 15 次推送经 ERP 用的执行再跑，逐项与 `golden-rules.json` 相同；
  5. 规范载荷是纯 JSON、映射键固定。
- 样本在生产镜像里用 `PYTHONHASHSEED` 0 与 12345 各生成一次，字节相同；测试用 777 跑通过。进候选检查离线车道（`scripts/ci-candidate-checks.sh`），同 Cowork 黄金样本（要 bench 的 Python）。
- 开关两种状态：**`tests/test_erp_mrerp_direct.py`**（20 项，只用标准库，进 `.github/workflows/development.yml`）：关着时常量为 False、`handles` 不查库、全部入口 403、排队 / 再写 / 巡检 / 三个后台任务什么都不做、hooks 不声明登录钩子、`PROGRAM` 照旧、ERP 不导入 Cowork；打补丁开着时排上、成功记单号、现购停下、账号被占排队并由巡检再排、没授权登录停下、要选菜单停在单据上并选一次后再排（不认的值拒）、结果不明不再推、重复领取不导两次、「正在导入」过期转结果不明、只有管理员能授权且视图不含密码；纯规则两项。
- `tests/test_erp_cc_chat_push.py`（17 项）不改断言，只给 Frappe 替身补 `frappe.utils` 子模块（`cc_tools` 现在导入 `mrerp`）：关着时对话工具输出与原来相同。

## 4. 测试与闸门

> 生产镜像：`ghcr.io/skin306152-star/pearnly/frappe@sha256:ea24fcb9ce7238414bf166bbe439339a06b9f601cd46b8a64dfdd0f4d7c1803d`（[erp-ledger-state](2026-11-02-erp-ledger-state.md) 记的生产镜像，本机已有，没有 SSH 生产）。

| 检查 | 结果 |
|---|---|
| 下沉后（生产镜像）：`test_mrerp_rules_golden` 3、`test_cowork_mrerp_push` 41、`collector` 15、`menus` 17、`catalog` 8、`masters` 12、`cc_login` 15、`folder_tasks_mrerp` 8、`core_boundary` 3、`push_lifecycle` 30、`push_purity` 2、`assistant_tools` 230、`companies` 183 | 全过（`test_cowork_push_source_parity` 要旧线上源码 `--source`，本机没有，没跑；它比的三个名字仍从 `push.retry` 取到） |
| `test_erp_mrerp_golden.py`（生产镜像） | 5 项通过 |
| `test_erp_mrerp_direct.py`、`test_erp_cc_chat_push.py`（本机 python3） | 20、17 项通过 |
| `check-architecture.py` | PASS（0 违规） |
| `check-unbounded-queries.py` | 通过（未超基线） |
| `check-impact.py` | 登记表完整（55 个共用件、202 个入口）；清单见 §5 |
| 全闸门 `gate.sh` | 见 §4.1 |
| 候选离线检查 `cand-local.sh offline` | 见 §4.1 |
| 流程 `run.py --changed` | 见 §4.1 |
| 生产镜像导入冒烟（无站点） | 新 ERP 模块、改过的 Express / CC 工具 / hooks、四个下沉模块与 Cowork 调用方、新 DocType 控制器共 19 个都能导入；关着时 hooks 不含登录钩子 |
| `check-copy --report` | 提交钩子：新增 0、改动 0、删除 0、拦截 0、标黄 0（只多 5 处引用已有词条） |

### 4.1 全闸门、候选检查、流程

| 项 | 结果 |
|---|---|
| 全闸门第一遍（`f9faa024` + 登记补正 `8f400113`，含 `npm ci`） | 194 步通过、1 步不通过：`tests/test_erp_express_direct.py`（它的 Frappe 替身里 `frappe.utils` 不是包，`express.push` 现在导入 `mrerp`，连带导入了密码模块） |
| 修正 `8b09750a` | `mrerp.connection` 只在解密的后台任务里导入密码模块；`test_erp_express_direct` 166 项、`test_erp_mrerp_direct` 20 项、`test_erp_cc_chat_push` 17 项通过 |
| 全闸门第二遍（`SKIP_NPM_CI=1`，`03c195b8`） | 171 步通过、1 步不通过：流程覆盖闸（新的管理员接口 `mrerp/api.py` 与它背后的三个服务没有流程） |
| 修正 `7b244d1b` | 四个文件登记进 `tests/flows/not_flows.toml`（原因：总开关关着、入口不开放、要真 MR.ERP；对用户开放时补流程）；`behind_baseline.txt` 不动 |
| 全闸门第三遍（`SKIP_NPM_CI=1`，`7b244d1b`） | **172 步全部通过，exit 0** |
| 候选离线检查 `cand-local.sh offline`（生产镜像 `ea24fcb9`） | 通过（exit 0，72 组）；本机清单旧，`ci-candidate-checks.sh` 里多出的 MR.ERP 9 个测试在同一镜像另跑：push 41、collector 15、catalog 8、menus 17、黄金样本 3 3、cc_login 15、**ERP 黄金样本 5**、边界 3、purity 2，全过 |
| 流程 `run.py --changed` | 改动命中的只有 `erp.express.new-item-stock`（覆盖 `express/push.py`）。**没测到**（原因：本机正有别的窗口的两套实验栈 `pearnly-regression-lab`、`pearnly-stfollow-lab` 在跑，规矩是一次只跑一套、不接管共享实验室）；开关关着时 `express.push` 只多一次不查库的判断，`test_erp_express_direct` 166 项锁住 |

## 5. 入口实测（`check-impact.py`，命中 `mrerp-rules`，只改到调用方的另有 14 个）

| 入口 | 结果 |
|---|---|
| `erp.mrerp.direct`（新登记） | 离线通过（`test_erp_mrerp_direct` 20 项、黄金样本 5 项）；真站点没测到（原因：不连 MR.ERP，等岳父测试账号） |
| `cowork.mrerp.push` | 离线通过（push 41、黄金样本 3 逐字节）；真站点没测到（原因：同上） |
| `cowork.mrerp.catalog` | 离线通过（collector 15、catalog 8、menus 17）；真站点没测到（原因：同上） |
| `cowork.cc.mrerp-setup` | 离线通过（`test_cowork_assistant_tools` 230、`test_cowork_companies` 183）；对话页实测没测到（原因：要真 MR.ERP 连接） |
| `erp.cc.chat-push` | 离线通过（`test_erp_cc_chat_push` 17，断言未改） |
| `erp.cc.write`、`erp.cc.connect`、`erp.cc.stock-void` | 没测到（原因：要测试机 CC 与 Express，不动测试机）；开关关着时 Express 入队 / 准备 / 巡检只多一次不查库的判断，由 `test_erp_mrerp_direct` 锁住 |
| `erp.line.chat`、`erp.execution`、`erp.entry.choices`、`erp.masters.scope`、`erp.provisioning`、`shared.navigation`、`cc.operator.thread` | 没测到（原因：只因 `hooks.py` / `express/push.py` 被列入；本步没改菜单、识别、主档、开户与代录，功能代码未动；要测试机或付费识别） |

## 6. 等岳父线上测试账号（真站点才能核对）

1. ERP 单据的票面在真 MR.ERP 里导入后的样子（赊购货 / 费、赊销、现销各一张），列表回读认出单号；
2. ERP 规范载荷里没有 `client_id`、商品映射：真站点按税号自建客户 / 供应商、按品名自建商品的结果（共用层 `autocreate`，Cowork 已有路径）；
3. 选账套目录、导入菜单与主档默认值在 ERP 公司上的读法（采集、读取任务真跑一次）；
4. 同账号：代录占着时直写排队、放开后接着导；会计本人登录时导入被踢的表现（判结果不明）；
5. CC 桌面版对 ERP 连接授权（共用通道 + ERP 钩子）真机一次（开关要先开，需主会话定）。

## 7. 限制与遗留（要主会话定的标 ★）

- ★ ERP 采购单多为「已付 + 到货」（10-28 口径），MR.ERP 导入只有赊购：现购一律停在 `mrerp.purchase_cash`，要等代录（另做）或主会话定口径。
- ★ MR.ERP 的停下原因（`mrerp.*`、`ERR_MRERP_*`）在待处理 / 单据页上没有译文，会显示原因码原文；加词条属于新增界面文案，开放前一并做（单据页 Express 弹窗也还不认 MR.ERP 的「选一次」，现在只有对话工具能选）。
- 结果不明的 MR.ERP 单没有「核对后记下」（`erp_resolve_push_result` 只认 Express 写账件）；现在只能人工在 MR.ERP 核对后再处理。
- 销售现销的收款方式按现金（不区分转账）；ERP 单据上的收款方式接进来留给开放前。
- 已导入 MR.ERP 的单在 ERP 作废，不回写 MR.ERP（MR.ERP 导入没有作废）。
- 公司从 MR.ERP 改回 Express / 取消时，还没导入的单不自动撤（Express 的 `detach` 不认 MR.ERP 的单）；关着的开关下不会发生。
- 代录（ERP 的 MR.ERP 代录接线）等主会话定，另做。
