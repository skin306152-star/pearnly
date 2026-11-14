# 需求影响评估：公司接入整体重做（Express 与 MR.ERP 同一套）

- 状态：评估主会话已审（11-14），**第 1 批（落库）施工中 / 待合**，第 2–4 批等用户回话。分支 `pearnly/company-onboarding-b2d996`，基线 `a838abe54`（= 当时 origin/main）。
- 依据：主交接 `~/pearnly-devtools/main-handoff-2026-11-13-evening.md` 第十三、十四、十五、十七、十八节与末尾「用户 11-13 深夜改口」
  （主档同步进 ERP 不能取消，覆盖第十七节 1–3 条）。
- 实测环境：本机实验栈 `pearnly-onboard-lab`（`infrastructure/compose/regression-lab.yml`，线上同版镜像 `fb1037ad…`，amd64 在 Mac 上模拟；
  源码 = 本分支）。**没连生产、没动测试机、没写任何真实账套**。实测脚本在本节末尾「实测记录」。

## 0. 需求原文与理解

- 原话：「现在用起来真的好麻烦」（接一家公司 5 步：CC 授权位置 → 授权账套 → ERP 网页新建公司 → 勾接 Express → 点同步）；
  「还有 MR.ERP 不要只做 Express」；「那个同步问题，记得要覆盖完整」；「同步数据到 erp 这个不能取消，当初设计这个一定是有原因的，有没有更好的办法」。
- 用户已定（不再讨论）：
  1. 5 步改 2 步：① CC 桌面版授权 Express 位置（或登录 MR.ERP）② 勾选要用的公司。
  2. ERP 公司页两个入口：「从连接添加」（弹窗同 CC 对话选账套，Express / MR.ERP 分标签，勾谁建谁，名称 / 税号 / 分店自动带出）和「新建」（只用 Pearnly ERP）。
     去掉「接 Express」勾选，来源决定是否互通。
  3. A 方案：CC 授权完成后网页端自动弹出「选择要用的公司」；以后要加再点「从连接添加」。不做「授权后全部自动建」。
  4. 不用点同步：勾选后自动同步并显示同步中；CC 授权后自动上报。
  5. 主档仍落库进 ERP；慢与堵分开修（单独后台进程 / 队列、首次批量写、查清整本改写的原因）；不加固定月费。
  6. Express 与 MR.ERP 同一套、同批。
- 我的理解：接了账本的公司 = 账本主档自动进 ERP、所有入口都能选到；人只做「授权」和「勾公司」两件事；
  大账套同步不能再拖慢别家公司的写账和 LINE 回复。
- 不在范围：收款文案（第十六节，归收款闭环那批）；页面加载性能体检（第十九节，另一窗口，结果引用见 §8.4）；
  CC 位置「只选其中几本账套」（第七节，见 §12 第 5 问，建议另立）。

## 1. 现状（查清的事实，均带代码位置）

### 1.1 testerp 那 65 家「没手工建过」的公司是怎么来的：现有代码会自动建

- `apps/pearnly_erp_portal/pearnly_erp_portal/express/companies.py:32-35,92-113`：CC 桌面版心跳报来的账套名单一有变化
  （`pearnly_core/cc_desktop/books.py:19-43` `replace` → `products.books_changed` → ERP 钩子 `hooks.py:371-378`），
  就排后台任务 `companies.ensure`：把在用电脑（CC 桌面版 ≥ 1.12.17、令牌在用、没退役，`companies.scoped`）报来的**全部**账套按
  「税号 + 分店」分组，**没有公司的组一律自动建公司**（`express_enabled=1`）并排主档同步。
- 所以 testerp 的 65 家就是测试机 CC 授权的 Express 位置下所有有税号的账套，被自动建出来、并且每家都整本同步了主档。
  这正是用户说的「授权后自动建好」，但它是**全部**建、**全部**同步——与用户定的 A 方案（只建勾选的）相反，也是第十五节后台堵塞的放大器。
- 没有 13 位税号的账套现在**不建公司、也选不到**（`companies.py` 文件头、`groups()`）。

### 1.2 「点同步」到底是什么

- CC 桌面版里没有「同步」按钮（设置里最接近的是「重新查找」`desktop_settings.py:539-558`）；「同步 Express」在 ERP 公司页
  （`public/desk/company.js:484-494` → `express/api.py:27-32` → `masters.start`）。
- 不点同步也会自动同步，只是慢：新建的公司立即排队；已存在的公司要等 `check_marks`（每 10 分钟，首轮只发看标记的请求，约 10–20 分钟）
  或 `sync_all`（每小时兜底）。11-13 晚用户「授权后没点同步就显示未连接」是这段等待加上公司页「未连接」判定（`push.connected`）造成的。
- 口径不一致：公司页「连上没有」用 `books.for_company`（不看 CC 版本与退役），建公司用 `companies.scoped`（看）。重做时统一成一处。

### 1.3 主档落库为什么慢、为什么「改写 7,500+」

实验栈合成一份同规模账套（客户 10,511 / 供应商 800 / 物料 1,157 / 仓库 2 / 结存 1,157 行），用现有代码 `masters.apply` 落库：

| 场景 | 改动条数 | 耗时（实验栈） | 另生成修改记录（Version） |
|---|---|---|---|
| 首次落库（全部新建） | 12,470 + 结存调整 1,118 | 33.5 秒（客户 20.5、供应商 1.8、物料 3.5、结存 7.1） | 0 |
| 同一份再落一次 | 0 | 0.3 秒 | 0 |
| **只换账套名（`express_book`）再落** | **12,470（全部重写）** | 37.1 秒 / 42.1 秒（两次） | **12,470** |

- **怀疑已证实（机制）**：`express_book`（只记「最近来自哪份账套」）在 `masters.SYNC_FIELDS` 里参与比对（`masters.py:606,627,681`），
  这家公司现用账套一换（新年度账套出现、同期两本时 `target.current` 选到另一本、电脑报的写法变了），**整本每一行都算「有变化」**，
  逐条 `get_doc + save`，每条再多写一条 Version 修改记录（客户 / 物料两个 DocType 开了 `track_changes`）。
- **生产已证实（11-14 只读）**：CO-00146 的主档 10-25 就建好了；11-13 20–21 点客户 10,511 条**全部**改动、各多一条 Version（物料 1,157、
  供应商 513 同样），档上同时有「T.8 บริษัท นิว ลิงค์…」和「9.บริษัท นิว ลิงค์…」两个账套名——现用账套换了一本，整本按「有变化」重写；
  旧账套里有、新账套里没有的 1,034 个客户 / 78 个物料 / 39 个供应商被停用。修法：去掉 `express_book` 的逐行比对（§3.2，第 1 批）。
- 速度差：同一段代码实验栈每分钟约 2.2 万条，生产 testerp 当晚约 1,700 条，慢约 13 倍。生产 ERP 后台进程 `cpu_shares 256`（让 Web 与数据库），
  当晚同一进程排着 66 个定时任务，资源被抢；**生产慢多少要上线后在 testerp 回读实测**，实验栈只能给相对比例。
- 剖析（500 条新建客户）：时间花在 Frappe 逐条 `insert` 的固定开销上——发号（`tabSeries` 加锁）、validate、清缓存、实时推送、每条一次往返，
  不是我们的比对逻辑。

### 1.4 批量写原型（只在实验栈脚本里，没进产品代码）

- 做法：一次预留一段编号（同 Frappe 发号的 `tabSeries` 计数器加锁一次）→ 按与逐条保存相同的归一字段组好行 → `frappe.db.bulk_insert` 每 1,000 行一批。
- 结果：**10,511 个客户 1.15 秒**（逐条 20.5 秒，快约 18 倍）；批量写完再用现有逐条比对跑一遍：**0 条差异**（字段口径一致）。
- 按同一比例折算生产：CO-00146 这样的账套首次落库约 7 分钟 → **约 20–30 秒**（估算，上线后 testerp 实测确认）。
- 落库进程内存峰值 187 MB（逐条重写 1.2 万条时，`ru_maxrss`）。

### 1.5 MR.ERP 一侧现在是什么样

- **整条 MR.ERP 账本线是关着的**：`apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/__init__.py:13` `MRERP_LEDGER_ENABLED = False`，
  选择窗里 MR.ERP 标签不出现，CC 的 MR.ERP 授权钩子没注册（`hooks.py:379-384`）。
- 账套候选：服务端用 CC 授权时存下的账号直接登录 MR.ERP 抓（`mrerp/catalog.py:23-55`，存 `ERP MRERP Connection.catalog_json`），**不靠电脑在线**。
- 主档：**不落进 ERP**。只读「导入菜单 + 默认值候选」小表（`pearnly_core/mrerp/master_reader.py`，`bshlistboxdata.php` 列表接口，每行只有 `[id, 代码, 名称]`）；
  缺客户 / 物料时推送里在 MR.ERP 自建（`pearnly_core/mrerp/push/autocreate.py`）。ERP Customer / Product 上没有任何 MR.ERP 字段。
- 要达到与 Express 同样体验差的：① 读整套主档——MR.ERP 自带导出页 `exparmas`（客户）/ `expapmas`（供应商）/ `expstkmas`（物料，含售价、单位，
  `expstkmas/component/expdata.php`）与结存报表 `rpstkmivtrbalstore`（按仓结存），只读、服务端登录即可读（MR.ERP 源码核对，未在 TEST2019 实读）；
  ② 读成与 Express 同一份 facts 形状，走同一个落库（来源字段从「Express 专用」改成「账本通用」，见 §3.3）；③ 变化判定：MR.ERP 没有文件属性可看，
  只能定时整读比对（服务端读，不占电脑；频率见 §12 第 4 问）；④ 打开 `MRERP_LEDGER_ENABLED`（随本批上线，按「功能不按租户开关」对全部租户生效）。

## 2. 全入口清单（接了账本的公司能不能看到账本主档）

### 2.1 物料调整下拉看不到同步物料（CO-00146 实例）

- 取数路径：物料调整页 → `public/desk/stock-entry.js:114` → `public/desk/transactions.js:468-470`，原生 Link（`ERP Product`，`filters: is_active=1`），
  服务端由权限钩子 `masters/access.py:84-95 master_query` 加条件「本地档 或 `express_company` = **当前公司**」（`active_company`，即用户默认公司）。
  没有按来源、按 origin 的过滤。
- 实验栈实测（同一个管理员账号，1,157 个同步物料挂在 CO-00001）：当前公司 = CO-00001 时空搜索、按编码、按品名都列出同步物料；
  当前公司切到另一家时**一条都没有**。代码在「当前公司就是那家」时是对的。
- **生产已定案（11-14 只读一次）**：用户 aksarapak 的默认公司 = 当前公司 = CO-00146，隔离过滤是对的；CO-00146 启用的物料 1,079 条都在范围内。
  以他的身份空搜，原生下拉**只回 10 条**，排前面的是手工档（P-00000x、PRD-170039 等）和几条同步档——同步档在，只是挤不进前 10 条；
  物料的搜索字段又没有 Express 编码，按编码打字也不一定搜到。所以是「空搜只显示 10 条、本公司账本档没排前、编码搜不到」，不是漏接。
- 修法（第 2 批，§2.3，主会话 11-14 定）：**按类，全部物料 / 客户 / 供应商选择框一起**，不只物料调整——原生链接搜索里本公司账本档排在本地档前面，
  账本编码进三个 DocType 的搜索字段（客户再加税号）；选择框按**单据的公司**过滤（不是会话的当前公司）。

### 2.2 全入口表

「现在」两列是接了账本的公司在这个入口能不能看到账本主档；MR.ERP 一列全部「不能」的原因相同：**MR.ERP 主档现在不落进 ERP**（§1.5），
只看得到本地档。「改后」= §2.3 修完 + §3.3 MR.ERP 落库后，两边同一结果。

| 入口 | 位置 | 取数 / 过滤 | 现在 Express | 现在 MR.ERP | 改后要动的 |
|---|---|---|---|---|---|
| 入库 / 出库 / 调拨 / 物料调整：物料框 | transactions.js:468；stock-entry.js:114 | 原生 Link + master_query（当前公司） | 能（当前公司对时）；不能按 Express 编码搜 | 不能 | 按单据公司；`search_fields` 加账本编码 |
| 同上：回车按码 / 扫码 | transactions.js:552；scan.js:55 → products/service.py:360 | candidates(get_list) | 能 | 不能 | 同上 |
| 库存单仓库 / 调入仓 | transactions.js:7,45 | Link + company | 能 | 不能（MR.ERP 仓不落库） | MR.ERP 仓落库 |
| 库存单过账校验物料 | inventory/stock.py:34 | get_doc，**不校验公司** | — | — | 补服务端校验（对称守卫） |
| 物料 / 客户 / 供应商列表页、打开 | products/service.py:348；masters/customers.py:53；purchasing/suppliers.py:64 | get_list + master_query；打开用 require_master | 能 | 不能 | — |
| 采购 / 销售：对方、物料选择与扫码 | entry-choice.js → masters/selection.py:116 `api_choices` | 手写 `express_company in [单据公司, '']` | 能 | 不能 | 改调公共函数 |
| 采购 / 销售提交自动认对方、物料 | purchasing/entry.py:34；sales/entry.py:103,280；products/purchase.py；products/sales.py | `selection.for_company` | 能 | 不能 | 改调公共函数 |
| 采购 / 销售识别预选 | purchasing/recognition.py:197；sales/recognition.py:85 | match_party / match_product | 能 | 不能 | 同上 |
| 采购 / 销售列表的对方筛选 | purchase.js:50 等 | Link + master_query | 能 | 不能 | — |
| 单据仓库 | purchase-fields.js:62；sales-fields.js:55 | warehouse_query | 能 | 不能 | MR.ERP 仓落库 |
| LINE 后台选对方（搜索 / 最近用过） | integrations/line_flow.py:292-389 | 手写 SQL（:313,362,379,383） | 能 | 不能 | 改调公共函数 |
| LINE 收单记忆 / 建议物料 | products/line_memory.py:40 | 手写判断 | 能 | 不能 | 同上 |
| LINE 收款找客户 | integrations/line_collection.py:388 → collection/service.py:160-174 | **只认 Express 档**（`express_code` 有值） | 能（本地客户反而看不到） | **一条都没有** | 改「账本档 + 本地档」口径，MR.ERP 同 |
| LINE 手机盘点扫码 | inventory/line_mobile.py:146 → count.py:95 | codes.candidates(盘点公司) | 部分：盘点没挂仓时只认本地档 | 不能 | 公司为空时取单据公司 |
| CC 收款找客户 `erp_collection_customers` | collection/cc_tools.py:118 | 同 LINE 收款 | 能（本地看不到） | 一条都没有 | 同 LINE 收款 |
| CC 盘点 / 盘点调整 / 收发存导出工具 | inventory/cc_tools.py:110-245 | 同盘点、收发存 | 部分 / 能 | 不能 | 同盘点 |
| CC 代录卡片物料搜索 | express/operator.py:482 | list_products（acting 公司） | 能 | 不能 | — |
| CC 代录 / 直写的存货、对方、科目 | express/precheck.py:575 | 读账套 facts（Express 自己的档） | 能 | 走 MR.ERP 自己的选项 | 不变 |
| CC 代录选物料校验 | express/operator.py:467 | `db.exists`，**不校验公司** | — | — | 补校验 |
| 收银台物料区 / 扫码 | cashier/counter.py:55-84 | list_products / find_product | 能 | 不能 | — |
| 收银台客户框 | pos adapter:1093 | Link；客户 `search_fields` 只有名称、电话 | 能（不能按编码、税号搜） | 不能 | `search_fields` 加编码、税号 |
| 收银台提交校验 | cashier/sales.py:58,119 | get_value，**不校验公司** | — | — | 补校验 |
| 在库一览 / 结存 | inventory/stock.py:344-473 | 仓按公司 + master_query | 能 | 不能 | MR.ERP 结存落库 |
| 收发存（物料卡）明细与筛选 | inventory/report.py:47-110；stock-card.js | 同上 | 能 | 不能 | — |
| 收发存导出物料范围 | inventory/card_export.py:92 | 手写 `ifnull(...) in ('', 当前公司)` | 能 | 不能 | 改调公共函数 |
| 期初物料框 / 批量模板 | stock-opening.js:153；opening_batch.py:129,422 | Link / 手写 | 能（接账本的公司本来不录期初） | 不能 | 改调公共函数 |
| 盘点录入框搜物料 | inventory/count.py:1191-1225 | 自写 SQL：**只列本盘点里或该仓结存 > 0 的** | 部分：零库存的同步物料搜不到 | 不能 | 去掉「结存 > 0」限制（改服务端分页搜索）|
| 盘点扫码 / 模板 / 快照 / 差异报表 | count.py:95,640；report/erp_stock_count_variance | candidates / 手写 or_filters | 部分 / 能 | 不能 | 改调公共函数 |
| 仓库列表、各单据建议仓 | inventory/warehouses.py:159-300 | company | 能 | 不能 | MR.ERP 仓落库 |
| 打印抬头取客户 / 供应商 | printing/context.py:159 | 手写 | 能 | 不能 | 改调公共函数 |
| 直写预检认物料、写回挂码 | express/push.py:267,700 | find / match | 能 | 不适用 | — |

### 2.3 按类修：一个公共函数 + 一道检查

- 现状：「本公司能看哪些主档」有 **1 个权限钩子 + 5 个口径不一的函数 + 17 处手写**（附录 B）：钩子按会话「当前公司」，`api_choices` 按「单据公司」，
  `codes.candidates` 公司为空时只认本地档，收款只认 Express 档，盘点搜索只列有结存的。新入口照哪处抄就是哪种口径，物料调整这次就是这样掉进去的。
- 修法：
  1. `masters/access.py` 收成一个公共函数 `master_scope(doctype, company)`：账本档（不分 Express / MR.ERP）按「归属 = 这家公司」+ 本地档，
     权限钩子、`api_choices`、`for_company`、`candidates`、收款、盘点、LINE、打印、导出全部调它；有单据的入口传**单据公司**，没有的才用当前公司。
  2. 三个主档 DocType 的 `search_fields` 补账本编码（客户再补税号），原生 Link 能按编码搜。
  3. 服务端过账 / 收银 / 代录选物料补「物料、对方属于本单公司」的校验（与看得到对称）。
  4. **检查** `scripts/check-master-scope.py`（进 `development.yml` 与本地闸门）：扫描 `apps/` 里对 `ERP Customer / ERP Supplier / ERP Product`
     的 `frappe.get_all`、`frappe.db.sql`、`frappe.db.get_value(…, {过滤})`，凡是带 `express_company` 字样却不经过 `master_scope` 的、或在用户入口模块里取主档列表却不经过
     `get_list` / `master_scope` 的，一律拦；存量登记基线、只减不增（同 `check-unbounded-queries.py` 的做法）。
  5. 流程用例：一条「接账本的公司在每个主档选择框都能选到同步来的档」的流程，按产品地图里标了「用主档」的入口逐个点（物料调整、出入库、调拨、采购、销售、
     收银台、盘点、期初、收发存、LINE 收款），Express 公司、MR.ERP 公司各一遍；以后新入口登记进产品地图时要标这个属性，`check-product-map.py` 拦没标的。

## 3. 方案

### 3.1 接入两步（Express、MR.ERP 同一条路）

1. **授权**：CC 桌面版授权 Express 位置（现有）或登录 MR.ERP（现有 `mrerp_login`，凭据存服务端连接，服务端自己抓账套目录）。
2. **勾公司**：网页端自动弹出「选择要用的公司」（A 方案）。
   - 触发：Express 账套名单变化（`books.replace` → `books_changed`）、MR.ERP 账套目录抓到新账套（`mrerp/catalog.run`）时，
     服务端算出「还没建公司的账套」，有新的就给能建公司的人（`cc_service.can_build`）推实时事件；不在线的下次打开任一 ERP 页由 boot 带出。
     同一批账套弹过、关掉了就不再弹（按账套键记「看过」），以后要加点公司页「从连接添加」。
   - 弹窗：原生 `frappe.ui.Dialog`，两个标签（Express / MR.ERP；没有该连接的标签不出），每个标签一张可勾选的列表 + 搜索，
     数据走现有服务端分页接口 `masters/cc_service.choices`（CC 对话里的选择窗已在用，同一份目录、同一套判重和权限），
     勾选后一次提交、服务端循环现有 `cc_service.create`（锁内幂等），名称 / 税号 / 分店由服务端读目录带出，前端给的一律不收。
   - 建好的公司立刻进入同步：列表与公司页显示「同步中」（现有 `express_sync_state`，进度只用真数：已落多少 / 共多少）。
3. **不再自动建公司**：删掉 `companies.ensure` 的「全部自动建」（`books_changed` 只负责弹窗通知），CC 对话里「选中即建」不变。
4. **公司页两个入口**：「从连接添加」（上面的弹窗）+「新建」（现有新建页，去掉「接 Express」勾选，建出来的只用 Pearnly ERP）。
5. **去掉「接 Express」勾选，来源决定互通**：`ERP Company` 加一个只读的来源（`ledger`：空 / express / mrerp），由「从连接添加」或 CC 选中即建时写入；
   现有 `express_enabled`、`mrerp_enabled` 的读者统一改读 `push.linked / enabled / linked_at` 三个函数（读者清单见附录 A），
   `pos_day` 两条 SQL 同改。「断开」保留（来源清空 = 回到纯 ERP），文案沿用现有。
6. **授权后不用点同步**：建公司即排同步（现有）；已接通公司授权后（名单变化）立即排一次标记检查，不再等 10–20 分钟。
   「连上没有」判定统一用 `companies.scoped` 的口径（§1.2 的不一致一并修）。

### 3.2 落库：慢与堵分开修

1. **不再整本改写**：行上的 `express_book` 不参与逐行比对、不再逐行写（§1.3 实测：换账套名 = 1.2 万条重写 + 1.2 万条修改记录）。
   全仓核过，行上的 `express_book` 除同步自己比对外没有读者（公司上的 `express_book` 保留，公司页、锁、补丁读的是公司上的）。
2. **首次落库批量写**：新建的行预留编号段后 `bulk_insert`（子表单位同批），字段归一与逐条保存同一份函数；改动的行仍逐条 `save`（变少后是少数）。
   实验栈：客户 10,511 条 20.5 秒 → 1.15 秒，写完用现有比对复核 0 差异。结存调整（1,118 行 7.1 秒）同样改批量。
3. **单独后台队列**：新增队列 `erp_masters` 与容器 `erp-masters-worker`（照 `dms-sync-worker` 的写法：1 个进程、只听这一个队列、`cpu_shares 256`），
   主档同步 / 落库 / 账套目录通知 / MR.ERP 主档读取都排到这里；原 `erp-worker`（short/default/long）只剩写账、LINE 回复、定时任务，不再被大账套堵。
   内存：落库进程实测峰值 187 MB，容器上限给 512m；生产现有上限合计在 1.3 倍超分以内（`check-production-capacity.py` 33 项通过），
   加 512m 后由同一检查复核，**不加机器、不加月费**。`sizing.py`、`production-capacity.md`、容量检查同提交改。
4. **整份 facts 先存、选择框先能搜**：现状已是「电脑读好 → 先存 facts → 再落库」。批量写后首次落库估 20–30 秒，
   不再另做一套「从 facts 搜」的选择框（会变成两套搜索、两套隔离判断）；同步中的那几十秒界面显示同步中与真实进度。

### 3.3 MR.ERP 同一套

- 读：`pearnly_core/mrerp/` 加整套主档读取（导出页 `exparmas` / `expapmas` / `expstkmas` + 按仓结存 `rpstkmivtrbalstore`），
  输出与 Express facts **同一形状**（customers / suppliers / products / units / warehouses / stock / settings），由服务端登录读，不占电脑。
- 落：同一个 `masters.apply`，不另写一份。主档上的 `express_company / express_code` 字段名保留（改名牵动隔离钩子、条码判重、写账认档几十处），
  语义改成「账本公司 / 账本编码」，另加只读 `ledger`（express / mrerp）标来源；界面标签改「账本」口径（四语）。
- 触发：MR.ERP 没有文件属性可看，按「写单后一次 + 每小时整读比对」；比对后只写变化行，整读一次一本约几秒（未实测，第 3 批在 TEST2019 实读给实数）。
- 打开 `MRERP_LEDGER_ENABLED`（全部租户同时生效，不按租户开关）：随第 3 批与 Express 同一车上线，之前 MR.ERP 标签不出现。
  **用户 11-14 定：整条线（连接、主档、写账）一起开，不拆成两个开关、不分段上线**；前提是写账先在 test01·TEST2019 真写读回验过（「MR.ERP 写账四格」窗口负责）。
  这个开关一处同时放开：CC 授权钩子（`hooks.py:382`）、连接与登录票（`mrerp/connection.py:24,130`）、账套目录与选账本（`mrerp/catalog.py:38,100`）、
  导入菜单与默认值（`mrerp/choices.py:74`）、选公司窗 MR.ERP 标签（`masters/cc_service._mrerp_on`）、设置里的连接行（`masters/cc_projects.py:44`）、
  写账直写（`mrerp/push.py:39,149,224`、`express/push.py:431` 分流）、待处理与核对（`express/pending.py:76`、`mrerp/ledger_check.py:19`）、CC 工具措辞（`express/cc_tools.py:717`）。

### 3.4 无税号账套

- 现在：按「税号 + 分店」认账套，没税号的账套建不了公司、选不到、写不进。
- 做法：选择窗里没税号的账套也列出（标「无税号」），选中即建，公司与账套按**账套键**直接挂（新增公司 ↔ 账套键的挂接表）；
  `target.current / pick`、`books.for_company`、公司页「连上没有」先看挂接表、再按税号。新年度账套：同一位置、Express 登记的公司名相同的自动挂到同一家，
  对不上的在选择窗里出现为新账套、人勾一次。MR.ERP 一侧本来就按「连接 + 账套键」认，不受税号影响。

### 3.5 已接通公司的过渡与回退

- 已接通（按税号自动建或手工勾过「接 Express」的）：补丁把 `express_enabled=1` → `ledger=express`、`mrerp_enabled=1` → `ledger=mrerp`，
  其余照旧，**不重建、不重同步**；去掉逐行比对后第一次同步只写真有变化的行。
- testerp 的 65 家自动建的公司：保留（它们已接通、已同步）。生产只读（11-14）：只有 testerp 有这种情况（66 家公司、63 家接 Express、
  59 家启用却没有任何单据）；mrerp.pearnly.com 1 家接 Express 没单据；gize、klm、polymer、test01 没有。
  要不要停用「从来没用过（没有任何单据）」的那批，见 §12 第 1 问。
- 回退：每批独立可回退。第 1 批（落库与队列）回退 = 回滚镜像，新队列里排着的任务改由原队列取（补一个迁移函数）；
  第 2 批（接入流程）回退 = 回滚镜像，`ledger` 字段留着不影响旧代码；第 3 批（MR.ERP）回退 = 关开关。

## 4. 上下游数据

- ERP 单据流：主档仍按「账本公司 + 账本编码」认档，采购 / 销售 / 库存单引用的还是 ERP 档名，**单据与库存流水不变**；结存对齐逻辑（`_pending`、`_stock`）不变，只是写法改批量。
- Express：读回口径不变（CC 读整套主档 `express_masters`、看标记 `express_marks`，契约不变）；写账认档不变。
- MR.ERP：新增「读整套主档」（只读），写账照旧走导入直写；缺档时现有自建（`autocreate`）保留，自建后下一轮整读把它落回 ERP。
- CC 桌面版：**不改**。Express 账套上报、读主档、看标记都是现有契约（`tests/test_companion_contract.py` `ErpExpressFront`、`PoolRequests`、`AccountSetIdentity`）；
  MR.ERP 登录授权是现有 `mrerp_login`。所以本批不发 CC 新版、不碰 test-pc runner。若 §12 第 5 问定「位置下只选几本」，那一项要改 CC，另立。
- Cowork / DMS：Cowork 也收同一条 `books_changed` 钩子（`pearnly_core/cc_desktop/products.py`），本批只改 ERP 的钩子实现；共用层 `books.replace` 不改。

## 5. 隔离与权限

- 主档按公司隔离沿用 `masters/access.master_query`（`permission_query_conditions`）：账本档只在当前公司是它那家时列得出、选得到；MR.ERP 档写同一个归属字段，自动同受隔离。
- 建公司只给能建公司的人（`cc_service.can_build`）：弹窗、实时通知只推给这些人，服务端 `create` 再判一次；员工看不到「从连接添加」。
- 弹窗里只列这个租户自己电脑报的账套 / 自己连接的 MR.ERP 账套（现有 `catalog()` / `_mrerp_sets()`），跨租户不可能串。
- 批量写绕过逐条 `insert` 的钩子：主档三个 DocType 的 `validate` 只有锁检查（同步本来豁免）和字段归一（批量写用同一份归一函数），由测试锁死「批量写 = 逐条写」（§1.4 的复核做法做成单元测试）。

## 6. 入口：网页、手机、LINE、CC

- 网页桌面 / 窄屏：弹窗是原生 Dialog，窄屏原生自适应；列表 + 勾选在 390px 宽要验一遍（流程用例 mobile 宽度）。
- 手机 / LINE：不加建公司入口（建公司是管理员在网页或 CC 做）；LINE 里选客户 / 物料的入口受益于主档可见性修复（§2）。
- CC 对话：选账套弹窗本来就是这份目录，行为不变；「添加公司」建出来的公司同样自动同步。

## 7. 共用件与连带回归

`python3 scripts/check-impact.py --files`（masters.py、companies.py、masters/access.py、cc_service.py、company.js、cc_desktop/books.py、compose.yml）命中：
共用件 `cc-desktop`（14 个入口）、`erp-masters-access`（4 个入口）。要逐项实测的入口：`erp.cc.connect`、`erp.cc.masters`、`erp.cc.write`、`erp.cc.stock-void`、
`erp.cc.companies`、`erp.masters.scope`、`erp.records.scope`、`erp.entry.choices`、`erp.page.company`、`erp.company-menu`、`erp.mrerp.direct`、`cc.mrerp-login`、
`erp.print.purchase/sales`（供应商 / 客户档取数）、`erp.collection.server`、`erp.line.collection`、`cowork.cc.channel`（同一钩子）。
新增流程用例：公司页「从连接添加」（Express 标签勾两家 → 建出两家、同步中 → 同步完）、「新建」不带接账本、无税号账套建公司、MR.ERP 标签（假 MR.ERP）。

## 8. 存储、成本与性能

### 8.1 成本
- 新容器 512m 内存在现有机器内（容量检查复核），**无新增月费**；MR.ERP 整读是登录客户自己的 MR.ERP 服务器，不花钱。
- 存储：facts 每本约 1 MB（现有）；MR.ERP 每本同量级。

### 8.2 只建勾选的公司会让库变小
- 性能体检窗口 11-14 线上只读：testerp 客户 84,912、物料 59,430、结存 20,220 行——就是 65 家自动建的公司各自整本同步上来的。
  只建勾选的公司后，新租户的主档量只等于真正在用的公司之和。

### 8.3 落库耗时（实验栈实测 → 生产估算）

| | 现在 | 改后 |
|---|---|---|
| 万条客户级账套首次落库（客户 10,511 / 供应商 1,051 / 物料 1,313） | 实验栈 23.8 秒；生产 testerp 约 7 分钟（每分钟约 1,700 条） | 实验栈 4.8 秒（第 1 批实测）；生产按同比例估 1–1.5 分钟 |
| 换账套（新年度 / 同期另一本）——11-13 CO-00146 就是这种 | 整本重写 + 每条一条修改记录（生产 7 分钟以上） | 0 条改动（第 1 批实测） |
| 大账套同步期间别家写账 / LINE 回复 | 排在同一个后台进程后面，等几分钟到十几分钟 | 不同进程，不等 |

### 8.4 与性能体检的关系
- 体检窗口（`pearnly/page-perf-audit-50db59`）查出盘点明细页慢在逐行查询（251 行发 1,362 条）+ 结存表按物料没索引，线上比实验栈慢 7–8 倍。
  那是页面取数写法，归体检窗口修；本评估不重复改。两者叠加点：主档量大让缺索引的整表扫更贵，只建勾选的公司能缓解但不能代替补索引。

## 9. 删除、作废、追溯

- 账本里删了的档：照旧停用不删（单据还引用）。
- 断开一家公司：来源清空、主档停在断开那一刻（现有 `disconnect_express` 口径），MR.ERP 同。
- 修改记录（Version）：同步改的行照旧留修改记录（只剩真变化的行，量小）；批量新建不写修改记录（新建本来也不写）。
- 已写进 Express / MR.ERP 的单不受本改动影响。

## 10. 文案与官方 UI

- 官方组件：`frappe.ui.Dialog`（标签页 + 可勾选列表 + 搜索）、`frappe.show_alert`、公司页 Page 原有布局；不自定义样式。
- **新增文案**（四语，泰 / 英 / 日待用户看译名，见 §12 第 6 问）：

| 中文 | en | th | ja |
|---|---|---|---|
| 从连接添加 | Add from connections | เพิ่มจากการเชื่อมต่อ | 接続から追加 |
| 选择要用的公司 | Select companies | เลือกบริษัทที่จะใช้ | 使用する会社を選択 |
| 无税号 | No tax ID | ไม่มีเลขผู้เสียภาษี | 税番号なし |
| MR.ERP 编码 | MR.ERP Code | รหัส MR.ERP | MR.ERP コード |
| 已同步 {0} / {1} | Synced {0} / {1} | ซิงค์แล้ว {0} / {1} | 同期済み {0} / {1} |

- **去掉的已有文案**（不是改字，是入口删掉，仍列给用户看）：公司新建页与公司页的「接 Express」勾选（`company.field.expressEnabled`，四语现有）。
- 不改的已有文案：「同步 Express」按钮保留（手动再同步一次）、「同步中」「断开」沿用。
- 交付时跑 `python3 scripts/check-copy.py --report` 附清单。

## 11. 测试与验收

- 单元：批量写 = 逐条写（同一份账套两种写法落库后逐字段一致）；换账套名再落 0 改动；`books_changed` 不再建公司、只发通知；
  弹窗 `choices`/`create` 权限（员工拒、跨租户拒）；`ledger` 补丁幂等；无税号账套挂接与新年度自动挂；MR.ERP facts 形状与 Express 同（假 MR.ERP）。
- 入口检查（§2.3）：新检查脚本进 `development.yml` 与本地闸门。
- 实验栈流程：§7 列的新增用例 × desktop / mobile；`erp.masters.scope`、`erp.entry.choices` 等受影响入口全跑；`--changed` 之外全套跑一次。
- 生产镜像候选离线检查（改了 ERP 代码 + 编排）。
- 上线后：testerp 用测试机已授权的 Express 位置走一遍「授权 → 弹窗 → 勾一家 → 同步完」，只读回读落库耗时与后台队列（排队连生产）；
  MR.ERP 在 TEST2019（test01 可自由写）走一遍。用户亲自看：弹窗长什么样、勾完多久能选到物料。

## 12. 要问用户的点

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | testerp（及生产其它租户）那些「授权后自动建出来、从没用过」的公司怎么办 | 保留不动；生产各租户先只读数一下有多少家，数大再说 | 不处理：它们照旧每小时兜底同步，占库占后台 |
| 2 | 授权后弹窗：只给管理员弹，还是所有能建公司的人 | 所有能建公司的人（同 CC「添加公司」口径） | — |
| 3 | 无税号账套这批做不做 | 做，放第 4 批（最后），不挡前 3 批上线 | 不做：没税号的公司仍接不上 |
| 4 | MR.ERP 主档多久整读一次 | 写单后一次 + 每小时一次（读客户自己的服务器，不花钱） | 更频繁会给客户 MR.ERP 服务器加压 |
| 5 | CC 位置「只选其中几本账套」（交接第七节） | 另立，不并进本批（要改 CC 桌面版并发新版） | 并进来：本批要发 CC 新版、等测试机 runner |
| 6 | 新增按钮四语译名（§10 表） | 照表 | — |

## 13. 分批与机器耗时

| 批 | 内容 | 依赖 | 机器耗时（写代码 + 闸门 + 流程） |
|---|---|---|---|
| 1 | 落库：去掉逐行比对 `express_book`、首次批量写（含结存调整）、单独队列与 `erp-masters-worker`、容量文档 | 无 | 约 3 小时（写 1.5 + 闸门 / 候选 / 受影响流程 1.5） |
| 2 | 接入两步：停自动建、通知 + 自动弹窗、公司页「从连接添加」/「新建」、去「接 Express」勾选、`ledger` 字段与补丁、`linked` 口径统一、§2 入口全修 + 入口检查 | 1 | 约 5–6 小时（写 3、闸门与全套流程 2–3） |
| 3 | MR.ERP 同一套：整套主档读取（TEST2019 实读核对）、facts 同形状、同一落库、开 `MRERP_LEDGER_ENABLED` | 2 | 约 4–5 小时（写 2.5、实读与流程 2） |
| 4 | 无税号账套挂接 | 2 | 约 3 小时 |

- 用户要求「Express 与 MR.ERP 同批」：建议第 1–3 批攒成**一车**一起部署（第 1 批可先合不单独部署，按攒批口径）；第 4 批可同车或下一车。
- 合计约 15–17 小时机器时间；其中等人的只有 §12 的几个问题和上线后真机 / 测试机回读。

## 附录 A：`express_enabled` / `mrerp_enabled` 的读者（去掉勾选时统一改读 `push.linked / enabled / linked_at`）

直接读写：`express/push.py:58,62,67-68`、`express/masters.py:426-427`、`express/companies.py:66`、`express/pos_day.py:57,62,181`、
`masters/company.py:34,93,543,561-566,683-692`、`masters/cc_service.py:31,82,297,402,424,427`、`masters/cc_tools.py:123`、`masters/cc_projects.py:91-93`、
`mrerp/catalog.py:82-84,102-105`、`mrerp/push.py:41-42`、`mrerp/choices.py:26`、补丁 `v1_17_company_settings.py`、`v1_22_express_linked_at.py`、`public/desk/company.js:32,195,264-266`。
经 `push.linked / enabled` 间接：`collection/service.py:59,71`、`express/masters.py:69`、`express/cc_tools.py:310`、`express/precheck.py:482,639`、`express/api.py:55,114,206`、
`inventory/warehouses.py:256`、`inventory/opening_batch.py:74`、`masters/company.py:179,431`、`masters/boot.py:30`。
测试：`test_erp_cc_companies.py`、`test_erp_express_direct.py`、`test_erp_write_precheck.py`、`test_erp_mrerp_direct.py`、`test_cc_conversation_ledger_erp.py`、
`test_flows_run.py`、`test_erp_default_company_warehouse.py`。

## 附录 B：手写「本公司可见主档」口径的地方（第 2 批改调公共函数）

`integrations/line_flow.py:313,362,379,383`、`inventory/count.py:657,1207`、`inventory/card_export.py:92`、`inventory/opening_batch.py:129,422`、`printing/context.py:163`、
`products/line_memory.py:40`、`products/sales.py:41`、`products/purchase.py:57`、`sales/entry.py:121,288`、`purchasing/entry.py:48`、`express/masters.py:566,645`（同步自己认档，保留）。
口径不同的函数：`masters/selection.py:22 for_company`、`:116 api_choices`、`products/codes.py:43 _scope / :111 visible`、`products/service.py:119 taken / :138 find`、
`collection/service.py:163,177`。

## 实测记录

- 实验栈：`docker compose -p pearnly-onboard-lab -f infrastructure/compose/regression-lab.yml`，端口 18791–18793，镜像 `fb1037ad…`（线上同版），源码 = 本分支；
  站点 `erp.regression.localhost`（`lab_init.py` 建）。评估后已删（`down -v`）。
- 脚本（拷在 `~/pearnly-devtools/company-onboarding-1114/`）：`bench_land.py`（first / again / book / book2 / profile）、`bench_bulk.py`（批量写原型 + 复核）、
  `link_probe.py`（以管理员身份调原生 `search_link`，切当前公司对比）。
- 合成账套：客户 10,511（三分之一带税号）、供应商 800、物料 1,157（四分之一有第二单位，全部带条码、售价）、仓库 2、结存 1,157 行。


## 第 1 批施工记录（落库：去掉逐行比对、首次批量写、单独队列）

主会话 11-14 批准先做第 1 批（用户 11-13 深夜已对方向回「可以」），推分支报主会话，不合入。

### 改动
- `express/masters.py`：档上的 `express_book` 不比对、只在新建时记（`SYNC_FIELDS = ("is_active",)`）；新建的行收集起来交 `bulk.insert_new`；
  同步、落库任务排 `lane()`（bench 登记了 `erp_masters` 就走它，没登记的旧测试栈退回 long）。`express/companies.py` 建公司任务同。
- `express/bulk.py`（新）：预留编号段（同 Frappe 发号的 `tabSeries` 计数器 + 行锁，先提交再锁、1020 快照冲突重来最多 5 次）→ 每行照
  `Document.insert` 写库前那一段走（默认值、操作人时间、命名、父子、before_validate / validate / before_save、`_validate`）→ `bulk_insert` 父表与单位子表；
  一批撞唯一索引回滚到保存点、改逐条。
- 部署：`compose.yml` 加 `erp-masters-worker`（512m、`cpu_shares 256`、只听 `erp_masters`）；`configure-erp-runtime.py` 登记队列（超时 3900 秒）；
  `activate-product.sh` 停 / 起 ERP 时带上它；`provision-agent.py` 重启 ERP 组时带上它；测试环境 `finish-start.py`、回归实验室 `lab_init.py` 与
  `regression-lab.yml` 同登记；`check-production-capacity.py` 加「ERP 主档道只听 erp_masters」并重算（34 项过，整机超分仍在 1.3 倍内）；
  `production-capacity.md` 加一行。
- 回退：`scripts/production/requeue-erp-masters.py`（回退到旧镜像后在 erp-web 里跑一次，把 `erp_masters` 里排着的任务按原任务号挪回 long）。

### `bulk_insert` 跳过的钩子与副作用（逐项）

| 项 | 结论 |
|---|---|
| `before_insert` / `before_validate` / `validate` / `before_save`（控制器与 doc_events） | 批量路径里照样跑（`prepare`）；三个主档没有 doc_events，控制器 `validate` = 锁检查（同步豁免）+ 字段归一 + 物料 `code_key` |
| 子表 `ERP Product Unit.validate`（换算 ≤ 0 改 1） | 照样跑（`_validate` 里跑子表） |
| 默认值、`owner / creation / modified`、docstatus | 照样设（`_set_defaults`、`set_user_and_timestamp`） |
| 命名 | 预留编号段，与原生发号同一计数器、同一行锁；实测与逐条写、并发落库、之后网页原生新建都不撞号 |
| 字段长度、类型、必填、Select 校验（`_validate`） | 照样跑 |
| 链接校验 | 跳过：唯一的链接 `express_company` 就是正在同步的公司 |
| 条码 / 编码判重 | 不受影响：在 `masters._products` 里写之前判（`products.taken`、本批 `taken_barcodes`）；库里的唯一索引兜底，撞了这一批改逐条 |
| 搜索字段 | 不涉及：Frappe 搜索按表列查，没有另建索引表 |
| `after_insert` / `on_update` / `on_change` | 三个主档都没有，不需要 |
| 修改记录 Version | 不需要：Frappe 新建本来不写（实验栈首次落库 0 条） |
| 实时通知（列表刷新） | 不需要：后台同步，落完公司页显示同步结果 |
| 文档缓存、权限缓存 | 不需要：新建的档没有缓存；权限按 `express_company` 查询时判，不缓存档 |
| 全局搜索 | 不涉及：三个 DocType 没有 `in_global_search` 字段 |
| 「关注我建的文档」 | 不需要：后台任务 |

### 实测（本机实验栈，线上同版镜像 `fb1037ad…`，`scripts/erp-masters-bulk-lab-verify.py`）

| 项 | 3,000 客户规模 | 10,511 客户规模（同 CO-00146） |
|---|---|---|
| 同一份账套逐条写 vs 批量写，各表除名字 / 时间 / 归属外逐字段相等（含单位子表） | 通过 | 通过 |
| 整本落库耗时：逐条 → 批量 | 5.9 秒 → 1.4 秒 | 23.8 秒 → 4.8 秒 |
| 两本账套两个进程同时首次落库 | 各 1.6 秒，共 4.1 秒，编号全不同，0 出错行 | 各 5.5 秒，共 8.3 秒，0 出错 |
| 批量写之后网页原生新建一条 | 编号接在后面，不撞 | 同 |
| 再落同一份 / 换账套名再落 | 0 条改动 | 0 条改动 |

- 首次实测发现：两个进程同时落库时，锁定计数器的那句撞上 MariaDB 11.8 默认快照隔离的 1020（「Record has changed since last read」）；
  已改成先提交再锁、撞上重来。Frappe 自己逐条发号在并发建档时也会撞同一个 1020（同仓 `assistant/sessions.py` 已有同类处理），
  批量写把一万多次发号收成二十几次，撞的机会反而少了。


## 接缝约定：公司接入（本窗口）× MR.ERP 写账四格（mrerp-write 窗口）

（主会话 11-14 定：约定只写这一份，对方引用不另写；改约定经主会话转两边。）

| 项 | 归属 | 约定 |
|---|---|---|
| `mrerp/__init__.py` 的 `MRERP_LEDGER_ENABLED = True` 那一个提交 | **本窗口**（第 3 批） | 只有一个开关、不拆；在对方报「TEST2019 四格真写读回通过」、主会话转达后才提交，与 Express 同一车 |
| 对方实验栈里打开开关做验证 | mrerp-write | 只在测试里 `mock.patch.object(mrerp, "MRERP_LEDGER_ENABLED", True)` 或自己实验栈的未提交改动，不提交开关 |
| 写账路径：`mrerp/push.py`、`express/push.py` 的 MR.ERP 分支（`:431` 起）、`pearnly_core/mrerp/push/**`、`mrerp/contracts.py`、`mrerp/ledger_check.py` | mrerp-write | 本窗口不改 |
| 连接与目录：`mrerp/connection.py`、`mrerp/catalog.py`、`mrerp/api.py`、`masters/cc_service.py` 的 MR.ERP 分支 | **本窗口**（第 2、3 批） | 对方只读、只调 |
| 主档整读：新文件 `pearnly_core/mrerp/master_export.py`（导出页读成 facts）、`mrerp/masters.py`（排读、交 `express.masters.apply` 落库） | **本窗口**（第 3 批） | 写账缺档时现有 `autocreate` 不变；自建后下一轮整读把它落回 ERP |
| `pearnly_core/mrerp/session.py`、`collect.py`、`master_reader.py`（登录、选账套、小表读取） | 共用、不改契约 | 两边都只调；谁要改先告诉主会话 |
| 公司「来源」：第 2 批加 `ERP Company.ledger`（express / mrerp）与 `push.linked / enabled / linked_at` 统一读法 | **本窗口** | 对方写账判定继续用 `mrerp.push.handles(company)`；第 2 批把它内部改读 `ledger`，签名不变 |
| 跨边测试 | 两边 | `tests/test_erp_mrerp_direct.py`（对方）在开关打开时跑全；本窗口第 3 批加「开关打开 + 从连接添加一家 MR.ERP 公司 → 主档落库 → 对方的写账用例照过」一条，放 `tests/test_erp_mrerp_onboarding.py` |
| 合入顺序 | 主会话排 | 对方写账分支先合（不含开关）→ 本窗口第 3 批（含开关）后合，同一车部署 |
