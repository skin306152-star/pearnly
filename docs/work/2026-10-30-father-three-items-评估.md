# 需求影响评估：岳父三项（LINE 盘点改仓库、姓名全称、盘点 / 期初 Excel 模板）

> 主会话 10-30 交办，三项合一份评估。路径相对 `apps/pearnly_erp_portal/pearnly_erp_portal/`（另注明的除外）。每项按模板 0–13 栏写，
> 不涉及的写一句理由。要人定的点集中在每项的第 12 栏。

---

## 一、LINE 手机盘点页能改「记在哪个仓库」

### 0. 需求原文与理解
- 岳父 10-26：Desk 和 LINE 盘点都要能改「记在哪个仓库 / 库位」。Desk 录入弹窗已能改两项（`public/desk/stocktake-entry.js:34-45`，仓库 Link + 库位跟着仓走）；
  LINE 页 `www/stocktake.html:262-285` 只有库位输入，提交 `api_line_stocktake_add` 不带 `warehouse`（`:393-399`）。
- 理解：员工在手机上数到的货其实放在别的仓（同一家公司），要能把这一笔记到那个仓 / 库位，口径同 Desk。
- 不在范围：跨公司记账、新建仓库、调拨（多仓第二期另开窗口）。

### 1. 现有同类与复用
- 后端 `inventory/line_mobile.py:196-206` 已收 `warehouse` 并交给 `count.add_entry`（`inventory/count.py:992`），Desk 与 LINE 同一个函数；库位清单
  `locations.api_location_options(warehouse=…)`（`inventory/locations.py:40`）lookup 时已按盘点的仓给（`line_mobile.py:176-181`）。
- 结论：扩展现有。LINE 页加一个仓库下拉（同公司启用仓，显示「编号 · 名称」同 10-29 仓库编号口径）+ 库位清单跟着所选仓重取；提交带 `warehouse`。
  后端补一个票据接口回这份盘点所在公司的启用仓清单与某仓的库位清单（只读，`as_ticket_user` 同其他接口）。

### 2. Frappe 原生能力
- LINE 页是票据页（ADR-005/007 明确例外：LINE 一次性票据页不走 Desk），原生 `<select>`；不涉及 Desk 组件。

### 3. 按类通用
- 类：盘点录入的「记在哪」。入口三个：Desk 录入弹窗（已有）、LINE 手机页（本次）、Desk 上传 Excel（表里本来就有「仓库」「仓位」列，`inventory/workbook.py:20-29`）。
  三处都落到 `count.add_entry` / 导入，同一套服务端校验（仓属于这家公司、库位在清单里，`positions.check`）。

### 4. 上下游数据
- 记录落在 `ERP Stock Count Entry`（warehouse / location），过账时按仓 × 库位出差异与调整（`count.py` 过账、Express 盘点调整按仓）。改到别的仓：那个仓多出一行
  （`_insert_item`，账面取那个仓当前结存），原行不受影响——Desk 已是这个口径。
- Express：接了 Express 的公司，盘点调整按仓写进 Express（仓 = Express 仓库码），不变。
- CC 桌面版 / Cowork / DMS：不涉及（载荷不变，Cowork 盘点是另一套）。

### 5. 隔离与权限
- **发现的已有缺陷（一起修）**：`count.add_entry` 取仓用 `warehouses.required(warehouse)`（`count.py:1013,1020`），没传这份盘点的公司，落到**当前账号的当前公司**。
  手机端员工的当前公司若不是盘点那家（多公司员工），连用账面那一行的仓都会报「仓不存在」。改为 `required(…, company=count_company(count))`，Desk 同受益。
- 仓库清单只给这份盘点所在公司的启用仓，服务端照旧核（给别家的仓直接拒）；票据接口只读、要 `count.write`（同 add）。

### 6. 入口
- LINE 手机页（本次）、Desk（已有）、Excel 上传（已有列）。相机入口不变（扫码仍是共用件，不动）。窄屏：LINE 页本来就是手机布局。

### 7. 共用件与连带回归
- 改 `www/stocktake.html`、`inventory/line_mobile.py`、`inventory/count.py`（add_entry）。`scripts/check-impact.py` 施工时贴输出；预计命中「按码找商品 / LINE 共用流程」登记里的盘点入口。
- 流程用例：`erp.line.stocktake`（LINE 手机盘点，已有）加一格「改到另一个仓保存、记录落在那个仓」；`erp.stocktake.*` Desk 流程回归。

### 8. 存储与成本
- 不涉及：不新增文件、外部调用或固定费用。

### 9. 删除、作废、追溯
- 撤销照旧（`api_line_stocktake_undo`）；记录本来就存 warehouse / location，追溯不变。

### 10. 文案与官方 UI
- 新增文案 1 条：仓库下拉的标签沿用已有键「实盘仓库」（`stock.count.actualWarehouse`，Desk 同一键），不新造。

### 11. 测试与验收
- 离线：add_entry 传别的公司的仓被拒、当前公司不是盘点公司时仍能记。流程：两套数据手机宽度——改仓保存、库位清单随仓变、记录回读仓 / 库位。
- 验收：测试窗口在测试租户手机上点一遍（LINE 真机要测试机 + 三星手机）。

### 12. 要问用户的点
| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 手机上仓库下拉默认选哪个 | 默认这份盘点的仓（同 Desk），上一笔改过就沿用上一笔（Desk 已这样） | 无 |

### 13. 工作量
- 一步：后端两个只读票据接口 + add_entry 补公司 + 页面下拉 + 流程格。机器耗时：写约 1 小时，流程约 20 分钟。

---

## 二、个人信息加「姓名」全称（名 + 姓）

### 0. 需求原文与理解
- 岳父 10-28 清单第 1 条：个人信息加姓名（全称），用来分清是哪个用户。登录名不变；显示人名的地方改用姓名。
- 不在范围：改登录方式、改邮箱规则、DMS 操作员（已有独立姓名 `display_name`，`apps/pearnly_dms/pearnly_dms/operators/roster.py:126,150`）。

### 1. 现有同类与复用
- **关键现状**：原生 User 的 `first_name` 现在被当成「用户名」用——
  - 个人信息页 `apps/pearnly_core/pearnly_core/account/layout.py:28,33` 把 first_name 标签改成 Username、只读；`public/desk/user.js:2-5` 只露 email、first_name 等，没有 last_name；
  - **每次身份服务（Keycloak）登录**都把 first_name 改写成 `preferred_username`、清空 middle / last（`apps/pearnly_core/pearnly_core/account/profile.py:33-53`，调用在 `identity.py:264-265`）——不改这里，新填的姓名下次登录就被冲掉；
  - ERP / Cowork 建员工都写 `first_name = username`（ERP `team/service.py:340-345`，Cowork `apps/pearnly_cowork/pearnly_cowork/team/service.py:219`），Keycloak 那边 firstName 也是用户名（`apps/pearnly_core/pearnly_core/keycloak_admin.py:92,273`）。
  - 所以现在全站 `full_name` = 用户名。
- 已有「显示名」函数：ERP `team/service.py:96-104 username_of`（3 处在用）、Cowork 私有 `_fullname`；pearnly_core 没有统一函数，各处直接 `get_fullname` / `full_name` / `user_info().fullname`。
- 结论：扩展现有原生字段——`first_name` / `last_name` 回到原生含义（名 / 姓），`full_name` 原生拼出；用户名只在 `username` 字段。共用层加一个统一显示名函数
  （姓名有就用姓名，没有回落用户名），各处收口。

### 2. Frappe 原生能力
- 原生 User 有 first_name / middle_name / last_name，保存时拼 `full_name`；`get_fullname` / `user_info().fullname` 都读它。用原生两栏（名、姓），不加自定义字段。
- 身份服务：Keycloak 用户本来有 firstName / lastName（原生），登录时取 `given_name` / `family_name` 映射到 first / last，用户名仍取 `preferred_username` 写 `username`。

### 3. 按类通用
- 类：所有「显示一个人」的地方（三产品 + CC）。入口总表（调研 10-30，带路径）：
  | 产品 | 位置 | 现在用 | 改姓名后 |
  | --- | --- | --- | --- |
  | 共用 | 个人信息页 | first_name 当用户名、只读 | 改：名、姓两栏可编辑，用户名只读另显示 |
  | ERP | 团队列表 / 新建（`team/service.py:267`、`public/desk/team.js:45,203`） | username | 改：加姓名列、新建加名 / 姓 |
  | ERP | 收银员（`cashier/sales.py:366`、`cashier/output.py:29`，`username_of`） | username | 改公共函数 |
  | ERP | POS 历史单「Sold by」（`public/desk/pos/official-pos_past_order_summary.js:93`） | owner（邮箱） | 改 |
  | ERP | 小票收银员、班次、盘点人、任务看板、LINE 绑定回执 | full_name | 自动跟随 |
  | Cowork | 团队成员（`team/service.py:178-179`、`public/desk/team.js:30,126`）、LINE 绑定回执（`line/handlers.py:34`） | username | 改 |
  | Cowork | 盘点人、看板、推送核对、对账预设、连接负责人 | full_name | 自动跟随 |
  | CC 网页 | 问候语与头像（`frontend/assistant/src/assistant.tsx`，数据 `pearnly_core/shell.py:63`） | full_name（问候取第一个词） | 自动跟随（问候显示「名」） |
  | CC 桌面版 | 不显示人名 | — | 不涉及 |
  | DMS | 操作员花名册 | 自有 display_name | 不涉及 |
- 刻意用用户名的地方（团队管理里「登录名」那一列、改密码）保留用户名，旁边加姓名。

### 4. 上下游数据
- 存量用户：first_name 现在 = 用户名。迁移补丁：**不动存量**（first_name 保持用户名、last_name 空，显示不变），等各人自己在个人信息填了姓名才变；
  或把 first_name 清成空让显示回落用户名——见第 12 栏问题 2。
- Keycloak：建员工时把名 / 姓也送 Keycloak（firstName / lastName），个人信息改姓名时同步过去（`member_client` 已有改资料通道，施工时核）；登录覆盖逻辑只写 username，不再覆盖 first / last。
- Express：不涉及（单据上的「经手人」是 ERP 自己的显示，写进 Express 的单不带人名——施工时核 `express/push` 载荷）。
- CC 桌面版：载荷不变（不传人名）。Cowork 共用身份，同一套改动。

### 5. 隔离与权限
- 姓名只本人（个人信息）和团队管理员（团队管理）能改；服务端核（原生 User 写权限 + 团队管理接口已有 `team.manage`）。不跨租户。

### 6. 入口
- 网页个人信息页、团队管理（ERP / Cowork）、CC 网页问候。LINE 不改姓名（只显示，绑定回执自动跟随）。手机窄屏同 Desk 原生表单。

### 7. 共用件与连带回归
- 改共用层 `pearnly_core/account/*`（个人信息页、登录同步）——登记的共用件「登录 / 外壳」，`check-impact.py` 施工时贴；**登录页不动**（Keycloak 主题冻结）。
  共用层改动按规矩在生产镜像跑完整候选 offline 车道。流程：`cc.settings`、ERP / Cowork 团队管理流程、登录流程回归。

### 8. 存储与成本
- 不涉及：两栏文本，无外部调用。

### 9. 删除、作废、追溯
- 改姓名不影响历史单据（单据存的是 user id，显示时取当前姓名）。若要「打印 / 回执上留当时的名字」需另存快照——现在不做，见问题 3。

### 10. 文案与官方 UI
- 原生 User 表单的「First Name」「Last Name」标签用原生翻译（不新造）；个人信息页上「Username」标签保留给 username 栏。团队管理新建弹窗加两栏用原生标签。
  改已有文案：个人信息页原来叫「用户名」的那一栏会换成显示 username 字段（文案不变，绑定字段变）——施工前把清单贴给用户看。

### 11. 测试与验收
- 离线：登录同步不再覆盖 first / last；显示名函数（有姓名用姓名、没有回落用户名）。流程：个人信息填姓名保存 → 团队列表、看板、CC 问候显示新姓名；重新登录不被冲掉。
- 验收：用户 / 岳父看团队列表与单据上的人名。

### 12. 要问用户的点
| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 姓名必填吗（新建员工时） | 新建员工必填「名」，「姓」可空（泰国习惯常只写名） | 必填会挡住批量建员工 |
| 2 | 存量用户的 first_name（现在 = 用户名）怎么处理 | 不动：显示仍是用户名，各人填了姓名才变 | 清空会让所有显示短暂回落成用户名（结果一样），但补丁要逐站改写 |
| 3 | 打印 / 回执上的人名用「当时的名字」还是「现在的名字」 | 现在的名字（不存快照，轻便） | 改名后旧单显示新名字 |

### 13. 工作量
- 两步：① 共用层（个人信息页、登录同步不覆盖、统一显示名函数、建员工送名 / 姓到 Keycloak）；② 各产品显示收口（ERP 团队 / 收银 / POS 历史单，Cowork 团队 / LINE 回执）。
  机器耗时：写约 3 小时；候选 offline 车道约 25 分钟；受影响流程约 40 分钟。

---

## 三、盘点 / 期初 Excel 模板带内部编码与车辆栏位

### 0. 需求原文与理解
- 岳父 10-25：盘点用的 Excel 模板带上内部编码和车辆栏位。
- 不在范围：采购 / 销售 / 出入库的上传（那些是单据照片识别，不是 Excel 模板）；客户 / 供应商导入（现在没有）。

### 1. 现有同类与复用（调研 10-30，带路径）
| 模板 | 现有列 | 缺什么 |
| --- | --- | --- |
| 期初（`inventory/opening_workbook.py:8-19`，下载 `opening_batch.py:341`） | 物料标识、编码、名称、条码/二维码、单位、仓库、仓位、数量、成本 | 「编码」下载时填系统编码 `code`、导回按 code → internal_code → express_code 匹配，**下载与导回不一致**；表头严格，多一列整表报 headers_invalid（`:57,63-66`）；按台商品逐行拒（TRACKED，`opening_batch.py:142`）；接 Express 的公司整个期初不开（`:74-75`） |
| 盘点（`inventory/workbook.py:20-29`，下载 `count.py:473`） | 编码（显示内部编码→Express 编码→系统编码，`products/contracts.py:53`）、名称、条码/二维码、单位、账面、实盘、仓库、仓位 | 没有独立内部编码列；新商品「编码」格落成条码 / 二维码（`count.py:356-362`）；**按台商品填了实盘数整张表失败**（`_upsert_import_entry → add_entry → UNIT_BY_UNIT` 没被接住，`count.py:271`）；不带盘点下载时商品清单没按公司过滤（`count.py:493-496`） |
| 商品导入（`products/workbook.py:21-37`） | 名称、编码、**内部编码**、条码/二维码、单位、基本单位、分类、价格、成本、低库存、VAT、图片、库位 | 只新建不更新；没有按台 / 号码种类列 |
| 按台期初登记（`inventory/serials.py:383,539`） | 主号（车架号 / 序列号）、副号（发动机号 / 第二号）、颜色、仓库、仓位；一次一件商品、最多 2000 行 | 只在「开按台」那一刻能用（台数必须等于账面数，`:252-253`）；开过之后再进的台只能走进货 / 入库单据 |

- 结论：扩展现有模板，不新建导入入口。

### 2. Frappe 原生能力
- 模板是我们自己的 xlsx 读写（openpyxl），原生 Data Import 不适合（要按公司、仓、库位、按台规则校验）。不涉及新组件。

### 3. 按类通用
- 类：「按商品 × 仓 × 库位录数量」的 Excel 模板——期初与盘点两张。两张同时加「内部编码」列、同一套匹配口径（内部编码优先，再系统编码、Express 编码、条码）。
  商品导入模板已有内部编码列，不动。

### 4. 上下游数据
- 内部编码：长度 ≤ 20，与系统编码 / Express 编码 / 条码 / 二维码 / 车架号共用一个查重范围（`products/codes.py:17`、`inventory/serials.py:109-111`）；接 Express 的商品
  内部编码锁定为 Express 编码（`express/locks.py:17`），模板里给了不同的值不写、行内报出。
- 按台（车辆栏位）：期初、盘点两条路都要能按台——
  - 盘点：按台商品一台一行（车架号 / 发动机号 / 颜色），导入时按车架号找到那一台记「在」；表里没有的台 = 没盘到（与手机逐台扫同一口径，`count.py:113,160`）。
  - 期初：等于把「按台期初登记」并进期初模板（商品列 + 车架号 / 发动机号 / 颜色），一件商品的台数必须等于期初数量（现有规则）。
- Express：车辆号码不进 Express（Express 没有按台）；数量照旧。接 Express 的公司没有期初（不变）。
- CC 桌面版 / Cowork / DMS：不涉及。

### 5. 隔离与权限
- 模板下载 / 导入照旧按公司（顺手修「不带盘点下载时商品清单没按公司过滤」，`count.py:493-496`）；权限键不变（期初 `stock.manage`、盘点 `count.write`）。

### 6. 入口
- Desk 期初、Desk 盘点上传。LINE 没有 Excel 上传（只逐个扫，不变）。

### 7. 共用件与连带回归
- 改 `inventory/workbook.py`、`opening_workbook.py`、`opening_batch.py`、`count.py`（导入）、可能 `serials.py`（按台期初并入）。施工时贴 `check-impact.py`。
  流程：期初导入、盘点上传（含按台公司数据集）各加格；62 套离线回归不涉及（不写 Express）。

### 8. 存储与成本
- 不涉及：文件只在导入时读，不留存。

### 9. 删除、作废、追溯
- 导入失败的行照旧逐行报出不落库；按台导入落 `ERP Serial No`，盘点作废照旧冲回。

### 10. 文案与官方 UI
- 新增列名 4 个（内部编码、车架号 / 序列号、发动机号 / 第二号、颜色）——按公司类型换叫法，沿用按台期初模板已有的列名键；新增行级报错键 1–2 个（车架号不存在、台数不符）。

### 11. 测试与验收
- 离线：两张模板表头新老格式都认（老模板没有内部编码列照样导）；内部编码优先匹配；Express 商品内部编码不改；按台：车架号认台、不在册的报出、台数不符报出、普通公司忽略车辆列。
- 流程：两套数据 + 汽车公司——下载模板 → 填 → 导入 → 回读。验收：岳父用自己的车辆清单试一份。

### 12. 要问用户的点
| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 要不要独立「内部编码」列 | 要：期初、盘点两张都加，下载时填内部编码（没有就空），导入时内部编码优先；原「编码」列改成系统编码只读参考 | 不加则用户不知道「编码」那格该填哪个码，下载与导回也不一致 |
| 2 | 盘点 Excel 要不要支持按台（车架号 / 发动机号 / 颜色一台一行） | 要：汽车 / 电器公司模板按台展开（一台一行、预填在册的台），实盘填「在 / 不在」或 1 / 0；普通公司不出这些列 | 不做则按台商品只能手机逐台扫，Excel 填了整表失败（现在就是） |
| 3 | 期初 Excel 要不要并入按台（不再单独走「按台期初登记」） | 要：同一张期初表，按台商品一台一行；单独的按台期初登记保留给已有库存开按台的场景 | 不并则汽车公司开账要两张表 |
| 4 | 盘点 Excel 里填了未知的车架号怎么办 | 报出这一行（不当场建台）；建台走进货 / 入库单 | 当场建台会绕过进货成本 |

### 13. 工作量
- 三步，可分开交付：① 两张模板加内部编码列 + 修整表失败（按台行先逐行报出不中断）+ 公司过滤；② 盘点 Excel 按台；③ 期初 Excel 并入按台。
  机器耗时：① 约 2 小时、② 约 3 小时、③ 约 2 小时；每步流程约 30 分钟。①不依赖问题 2、3 的答复，可先做。
