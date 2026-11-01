# 表单排版全量过一遍（2026-11-01）

主会话 11-01 派单：10-31 晚用户截图发火（个人信息页「姓」落在「访问域名」下面、添加用户弹窗名姓分开、新建公司拉成五栏、
新建物料两栏一长一短——「横竖七八」）。那四处已由主会话修好上线（`62a752ce`）；本任务把 ERP / Cowork / DMS 三个产品和控制面
管理后台里**所有**新建 / 编辑页面与弹窗过一遍，排不齐的一次改齐，并加检查防再乱。不碰发号页 `pearnly-issue.html`、
发号单 `Pearnly Issue` 与 Keycloak 登录页。

分支 `pearnly/form-layout-audit-86ed56`（基线 `origin/main` `99ac4898`）。

## 规矩（用户 10-31）

1. **每栏放一类**：是什么（名称、编码、类型）/ 怎么计量和卖 / 联系与收款 / 设置；同一类的格不拆到两栏。
2. **各栏长度接近**：两栏以上的一节里，最长一栏比最短一栏多出一行半以上就要重排。
3. **成对字段同一行**：名 | 姓、用户名 | 密码、起 | 止；分栏的一节里两格同高，单栏里上下挨着。
4. **同一份资料新建与详情同一套分栏**（新建公司页与公司信息页、新建与编辑弹窗）。
5. 一节最多三栏。照 Frappe 原生 Section / Column Break 写，不自造控件，不加说明文案。

根因：加新字段时直接塞进当前那一栏、不重排。所以「最近加过字段」的表单同样要查（团队弹窗加姓名、公司加 Express / 公司类型、
物料加内部编码、单据加仓库库位都是这样进来的）。

## 检查（防再乱）

- 静态闸门 `scripts/check-form-layout.py`（CI `Development checks` 同名一步，测试 `tests/test_check_form_layout.py`）：
  - 前端：`apps/**/public/**/*.js` 里带分栏的字段数组（`p.layout` / `p.dialog` / `frappe.ui.Dialog` / `FieldGroup`，含
    `f("名", "标签", "Data")`、`u.field(…)`、`u.column()` 这类小工具写法）；
  - DocType：控制面的原生表单，和除系统管理员外还给了产品角色读权限的 DocType（产品业务单据的 DocType 只开给 System Manager，
    产品用户打不开原生表单，走的是上面的 Desk 页面与弹窗）。
  - 按节算：隐藏字段不算；行高按字段类型估（多行文本 2 行、表格 3 行、勾选 0.6 行，带说明加 0.4）；`hidden: editable` /
    `...(is_new ? [...] : [])` 这类简单开关分「真 / 假」各算一遍（新建与只读详情显隐不同）；
  - 报三类：`columns`（一节超过三栏）、`balance`（栏长差超过 1.5 行）、`pair`（成对字段不在同一行）。
  - 存量登记 `scripts/form-layout-baseline.json`，每条写原因，**只许减少**（`--update` 只删已修好的，拒绝新增）。
  - `--list` 打出全部带分栏的表单与每栏字段，即本文的静态清单。
- 实测巡检 `scripts/regression/form_layout_shots.py`（不进 CI，改表单时给人看）：在回归实验室逐页打开「本页 / 新建 / 第一行」，
  电脑宽 1280、手机宽 375 各截一张整表，并实测每个分栏节的各栏像素高度与成对字段的行差（`measure.json`）。
  `ui_steps.mjs` 为它加了两个可选项：`width`（手机宽度，默认仍 390）、`full`（截图时把整张表单截全）。

## 影响评估（按模板压缩）

- 范围：只动字段的先后与分栏（Section / Column Break 的位置），不增删字段、不改文案、不改接口与存储；
  控制面 DocType 只改 `field_order` 与新增一个无标题 Section Break（无列、无数据迁移，`migrate` 同步）。
- 同类复用：同一份资料（公司、供应商 / 客户、采购 / 销售单头、Cowork 识别结果的对方一节、团队成员）在各入口用同一套分栏。
- 上下游 / Express / CC 桌面版：不涉及（不碰数据与载荷）。隔离权限：不涉及。
- 入口：电脑宽与手机宽都截图；手机上各栏按顺序纵排，所以每栏内部的先后也按「一类」排。
- 共用件：不改 `product.js` 的 `p.dialog` / `p.layout`；改的是各产品自己的页面文件。
- 文案：无新增、无改动（`check-copy.py` 照跑）。
- 成本、删除作废：不涉及。

## 清单

全仓表单入口：45 个前端文件里 118 处 `p.dialog` / `p.layout` / `frappe.ui.Dialog` / `FieldGroup` / `frappe.prompt`
（`grep` 可复现），加控制面原生表单。其中**带分栏的 48 张**由静态闸门逐张算（下表），其余是单栏弹窗（一行一格，
只看分组先后，不存在栏长与成对问题）。

### 实测巡检（回归实验室 `pearnly-form-lab`，老数据集 `old`，电脑宽 1280 / 手机宽 375）

每个产品页面打开「本页 / 点新建 / 点第一行」三屏、两种宽度各截一张整表（43 个页面 × 3 屏 × 2 宽度）。全量截图（改前 / 改后 /
改后手机宽复扫）放在共享笔记目录 `表单排版截图-11-01/`；下面「改了哪些」里每一处的改前改后对照放在本记录同名子目录。

| 产品 | 页面（本页 / 新建 / 第一行） | 结论 |
| --- | --- | --- |
| ERP | 首页（充值、套餐三栏网格） | 齐，不改 |
| ERP | 公司（新建公司页 / 公司信息页） | **改**：左七格右五格、新建与详情地址备注位置不一 |
| ERP | 仓库（新建 / 编辑弹窗，单栏） | 齐，不改 |
| ERP | 团队管理（添加用户 / 编辑用户） | **改**：编辑时左三格右一格 |
| ERP | 物料（新建 / 编辑） | 齐（主会话 10-31 已改，365 / 460px），不改 |
| ERP | 供应商、客户（新建 / 编辑，单栏） | **改**：电话夹在名称与税号之间 |
| ERP | 采购（新建手工录入 / 详情） | **改**：供应商一节左三格右一格；税费一节左三格右五格 |
| ERP | 销售（新建手工录入 / 详情） | **改**：税费一节左二格右五格、税率单独落在下一节；只读详情收款几格左三右五 |
| ERP | 入库、出库、调拨（新建弹窗） | **改**：单头六格竖成一条（调出仓 \| 调入仓没并排）；明细三栏把物料、仓位、成本混放 |
| ERP | 收发存报表、盘点、收银记录、班次记录、库存 | 齐（收发存是用户 09-25 定的一排四格网格），不改 |
| ERP / Cowork / DMS | 个人信息（原生 User 表单） | 齐（主会话 10-31 已改，三栏 198 / 221 / 209px；姓在名下面是用户定的） |
| Cowork | 团队管理（添加 / 编辑用户） | **改**：同 ERP |
| Cowork | 录入工作台（复核页） | **改**：对方一节左三格右一格；税费一节左二格右四格（实验室不跑付费识别，复核页由新流程 `cowork.intake.review` 用手工票打开实测） |
| Cowork | 银行 / 收入 / 销项对账（新建任务两栏对称）、库存盘点、任务看板 | 齐，不改 |
| DMS | 连接 DMS、推送记录、操作员（单栏） | 齐，不改 |
| 控制面 | 租户 | **改**：左二格右四格（126 / 415px） |
| 控制面 | 功能目录、部署、LINE 频道 | **改**：新建时左三格右一格 |
| 控制面 | 充值审核 | **改**：两个多行备注挤在右栏（127 / 370px） |
| 控制面 | 钱包 | **改**：按一类分（余额 \| 本月用量） |
| 控制面 | 开户目标、开通进度、订阅、台账 | 齐，不改 |
| 共用 | 手机宽弹窗（三栏、两栏都有） | **改**：中间栏两边缩 15px、第一栏右边短 15px（Frappe 手机规则只照顾两栏；真机验收记的「左栏输入框比右栏窄 15px」就是它） |

没截到的：Cowork 任务看板电脑宽两次都卡住超时（页面不含分栏表单，静态清单里它的弹窗都齐或已登记）；
LINE 绑定块（实验室没接 LINE 频道，不出二维码）；收银台 POS 的官方弹窗（框架自带，未改）。

### 静态清单（`python3 scripts/check-form-layout.py --list`，改后）

| 文件:行 | 表单（第一个字段 / DocType） | 节数 | 最多栏数 | 状态 |
| --- | --- | --- | --- | --- |
| `pearnly_core/pearnly_core/public/desk/home.js:76` | used | 3 | 3 | 通过 |
| `pearnly_cowork/pearnly_cowork/public/desk/intake-folders.js:183` | title | 7 | 2 | 通过 |
| `pearnly_cowork/pearnly_cowork/public/desk/intake-folders.js:513` | remind_day_${i} | 1 | 2 | 通过 |
| `pearnly_cowork/pearnly_cowork/public/desk/intake-upload.js:282` | notice | 5 | 2 | 通过 |
| `pearnly_cowork/pearnly_cowork/public/desk/intake-upload.js:440` | name | 2 | 2 | 通过 |
| `pearnly_cowork/pearnly_cowork/public/desk/line-binding.js:29` | qr | 1 | 2 | 基线登记（见 `form-layout-baseline.json`） |
| `pearnly_cowork/pearnly_cowork/public/desk/line-binding.js:41` | line_name | 2 | 2 | 通过 |
| `pearnly_cowork/pearnly_cowork/public/desk/recon.js:261` | title | 2 | 2 | 通过 |
| `pearnly_cowork/pearnly_cowork/public/desk/team.js:128` | first_name | 1 | 2 | 通过 |
| `pearnly_cowork/pearnly_cowork/public/desk/work.js:749` | start_date | 1 | 2 | 通过 |
| `pearnly_cowork/pearnly_cowork/public/desk/work.js:1137` | cover_preview | 9 | 2 | 通过 |
| `pearnly_cowork/pearnly_cowork/public/desk/work.js:1504` | title | 2 | 3 | 通过 |
| `pearnly_cowork/pearnly_cowork/public/desk/work.js:1793` | board_list | 1 | 4 | 基线登记（见 `form-layout-baseline.json`） |
| `pearnly_cowork/pearnly_cowork/public/desk/work.js:1882` | rows | 2 | 3 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/company.js:176` | company_name | 6 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/company.js:286` | express_book | 2 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/company.js:344` | company_name#2 | 5 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/line-binding.js:34` | qr | 1 | 2 | 基线登记（见 `form-layout-baseline.json`） |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/line-binding.js:46` | line_name | 2 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/pos/pearnly-pos-shift.js:158` | sales_count | 2 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/pos-shifts.js:44` | terminal | 2 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/products.js:93` | product_name | 2 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/products.js:293` | q | 2 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/purchase-fields.js:41` | qty | 3 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/purchase-fields.js:79` | attachment_kind | 10 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/sales-fields.js:38` | qty | 3 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/sales-fields.js:64` | attachment_kind | 7 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/stock-entry.js:59` | doc_no | 4 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/stock-opening.js:152` | product | 2 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/stock-opening.js:284` | company | 4 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/team.js:201` | first_name | 2 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/transactions.js:429` | product | 2 | 3 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/work.js:787` | start_date | 1 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/work.js:1175` | cover_preview | 9 | 2 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/work.js:1542` | title | 2 | 3 | 通过 |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/work.js:1831` | board_list | 1 | 4 | 基线登记（见 `form-layout-baseline.json`） |
| `pearnly_erp_portal/pearnly_erp_portal/public/desk/work.js:1920` | rows | 2 | 3 | 通过 |
| `pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_credit_transaction/pearnly_credit_transaction.json` | Pearnly Credit Transaction | 2 | 2 | 通过 |
| `pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_deployment/pearnly_deployment.json` | Pearnly Deployment | 4 | 2 | 通过 |
| `pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_feature/pearnly_feature.json` | Pearnly Feature | 2 | 2 | 通过 |
| `pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_line_channel/pearnly_line_channel.json` | Pearnly LINE Channel | 3 | 2 | 通过 |
| `pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_provisioning_request/pearnly_provisioning_request.json` | Pearnly Provisioning Request | 3 | 2 | 通过 |
| `pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_subscription/pearnly_subscription.json` | Pearnly Subscription | 2 | 2 | 通过 |
| `pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_target/pearnly_target.json` | Pearnly Target | 6 | 2 | 通过 |
| `pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_tenant/pearnly_tenant.json` | Pearnly Tenant | 2 | 2 | 通过 |
| `pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_topup_request/pearnly_topup_request.json` | Pearnly Topup Request | 3 | 2 | 通过 |
| `pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_wallet/pearnly_wallet.json` | Pearnly Wallet | 1 | 2 | 通过 |
| `pearnly_core/pearnly_core/pearnly_core/doctype/pearnly_erp_endpoint/pearnly_erp_endpoint.json` | Pearnly ERP Endpoint | 7 | 2 | 通过 |


## 改了哪些（只动先后与分栏，不增删字段、不改文案）

实测栏高是电脑宽 1280 下各栏内容的像素高（`measure.json`）。截图：`2026-11-01-form-layout-audit/<表单>-before|after-desktop|mobile.jpg`。

| 表单 | 文件 | 改前 | 改后 |
| --- | --- | --- | --- |
| ERP 新建公司 / 公司信息 | `pearnly_erp_portal/public/desk/company.js` | 左七格右五格 453 / 309px；新建页地址、备注单栏落在最底下，详情页是基本信息下面两栏 | 左公司与税务（名称 / 简称 / 税号 / 查询 / 分支 / 增值税），右联系与收款再是公司类型、接 Express（详情页是 Logo）382 / 380px；地址 \| 备注两页都紧跟在下面 |
| ERP 公司 Express 一节 | 同上 | 账套、同步时间 \| 状态、原因、待写退货（最多二比四） | 账套 \| 同步时间，状态 \| 原因；待写退货放短的那一栏 |
| ERP / Cowork 添加 / 编辑用户 | `pearnly_erp_portal/public/desk/team.js`、`pearnly_cowork/public/desk/team.js` | 编辑时左名 / 用户名 / 邮箱、右只有姓 199 / 55px；手机宽叠成「名 / 用户名 / 邮箱 / 姓 / 密码」（11-01 真机验收 `2026-11-01-post-deploy-device-tests.md` 第 4 节） | 每一对各占一节（无边框）：名 \| 姓、用户名 \| 初始密码（编辑时 用户名 \| 邮箱）、邮箱；各行 55 / 55px，手机宽是 名、姓、用户名、密码、邮箱 |
| ERP 供应商、客户弹窗 | `suppliers.js`、`clients.js` | 名称、电话、税号…（电话夹在名称与税号之间） | 名称（类型）/ 税号 / 查询 / 分支，再电话 / 地址 / 备注 |
| ERP 采购单 供应商一节 | `purchase-fields.js` | 左供应商 / 税号 / 地址、右分支类型 209 / 55px | 左供应商 / 地址，右税号 / 分支 138 / 126px |
| ERP 采购单 税费一节 | 同上 | 左三格右五格 167 / 344px | 左（含税）/ 净额 / 折扣 / 尾差，右税额 / 预扣 / 合计 / 应付 239 / 272px |
| ERP 销售单 税费一节 | `sales-fields.js` | 左二格右五格 65 / 343px，税率单独落在下一节 | 左（含税 / 价含税）/ 税率 / 净额 / 单头折扣，右折扣合计 / 税额 / 合计 279 / 200px |
| ERP 销售单 详细信息（只读） | 同上 | 左三格、右收款五格 | 收款状态落到左栏单据下面，右栏收款明细 |
| ERP 入库 / 出库 / 调拨 单头 | `stock-entry.js` | 单号、日期、仓库、库位（调拨六格）竖成一长条 | 单号 \| 日期；仓库 \| 库位；调拨左调出、右调入 126 / 126px；用途 \| 备注 |
| ERP 入库 / 出库 / 调拨 明细行 | `transactions.js` | 三栏混放（物料 / 内部编码 / 仓库 \| 仓位 / 数量 / 单位 \| 成本 / 说明 / 删除） | 物料是什么（物料 / 内部编码 / 说明）\| 放哪（仓库 / 仓位 / 调入仓位）\| 多少（数量 / 单位 / 成本），删除单独一行；197 / 197 / 197px |
| Cowork 录入复核 对方一节 | `pearnly_cowork/public/desk/intake-upload.js` | 左名称 / 税号 / 地址、右只有分店 | 左名称 / 地址、右税号 / 分店（同 ERP 采购单） |
| Cowork 录入复核 税费一节 | 同上 | 左折扣 / 币种、右净额 / 税额 / 预扣 / 合计 | 左币种 / 净额 / 折扣、右税额 / 预扣 / 合计；物料行的删除单独一行 |
| 控制面 租户 | `pearnly_tenant.json` | 左名称 / 状态、右联系人 / 电话 / 识别引擎 / 备注 126 / 415px | 左名称 / 状态 / 识别引擎、右联系人 / 电话，备注单独一节 222 / 126px |
| 控制面 钱包 | `pearnly_wallet.json` | 左租户 / 余额、右用量三格与更新时间 | 左租户 / 余额 / 更新时间、右本月页数 / 月份 / 核对日 |
| 控制面 功能目录 | `pearnly_feature.json` | 左产品 / 代码 / 名称、右默认开 222 / 45px | 左产品 / 名称、右代码 / 默认开 126 / 141px |
| 控制面 部署 | `pearnly_deployment.json` | 左状态 / 产品 / 租户、右站点名 197 / 80px；站点一节左四格右一格 | 左租户 / 产品 / Bench 组、右站点名 / 状态 126 / 151px；站点一节左应用 / 端口 / 地址、右登录客户端 ID / 密钥 |
| 控制面 LINE 频道 | `pearnly_line_channel.json` | 左状态 / 部署 / 产品、右频道 ID 197 / 55px | 左部署 / 产品、右状态 / 频道 ID 126 / 126px；凭据一节左密钥 / 令牌、右 LIFF / Webhook |
| 控制面 充值审核 | `pearnly_topup_request.json` | 凭证一节右栏两个多行备注 127 / 370px | 凭证：转账单 / 付款人 \| 租户备注 127 / 177px；审核：时间 / 审核人 \| 审核备注 / 删除记录 |
| 手机宽弹窗（全站） | `pearnly_core/public/desk/product.css` | 三栏弹窗中间栏两边缩 15px、第一栏右边短 15px | 每一栏都贴齐（补全 Frappe `modal.scss` 那条只照顾两栏的手机规则） |

附带变化：控制面 DocType 的字段顺序也决定原生列表列的先后——功能目录列表「代码 / 名称」对调、部署列表「状态」挪到最后、
LINE 频道列表「状态」挪到产品后面（各人存过列表设置的照旧按设置）。控制面 DocType 的 `modified` 已更新，`migrate` 同步，无数据迁移。

## 验证（分支 `pearnly/form-layout-audit-86ed56`，变基到 `origin/main` `53acebda`）

- 静态闸门：`python3 scripts/check-form-layout.py` 通过（48 张带分栏的表单，基线 4 条：两个产品的 LINE 绑定块二维码、两个产品的看板规则行，
  原因写在 `scripts/form-layout-baseline.json`）；`tests/test_check_form_layout.py` 9 项通过；`development.yml` 加同名一步。
- 本地闸门 `~/pearnly-devtools/gate.sh`：153 项通过；3 项（吞错、三元混用、只读 Autocomplete）不通过全是本机为实验栈构建的
  `public/pearnly-app/assets`（不进 git）——在 `git archive HEAD` 的干净树上这 3 项通过。分支 CI「Development checks」通过。
- 受影响流程（`run.py --changed --base origin/main --jobs 3`，本机栈 `pearnly-form-lab`）：14 条 × 两套数据 × 电脑 / 手机，首轮 47/54；
  不通过 7 格中 4 格是新写的 `cowork.intake.review` 选择器只认 `input`（产品里 Data 格是会长高的 `textarea`，已修），3 格是无头浏览器
  卡住 900 秒（`erp.company.create` 手机两套、`erp.purchase.create` 老数据电脑），单独重跑 7 格全部通过。新流程 `erp.party.dialogs`、
  `cowork.intake.review` 四格都过（电脑宽断言左右栏同行，手机宽断言叠列顺序，改分店号保存落库）。
- 实测巡检（`form_layout_shots.py`）：改前 / 改后 / 改后手机宽复扫各 43 页 × 本页 / 新建 / 第一行，页面无报错；
  控制面「订阅」列表手机宽两次卡住超时，没测到（表单未改）。
- 文案：`check-copy.py` 新增 0、改动 0（只复用了已有的「地址与备注」节标题）。

### 共用件连带回归（改到 `product.css`、`transactions.js`、`intake-upload.js`）

`python3 scripts/smoke/run.py --target local --diff`（本机实验栈，老数据集）：

自动冒烟（local）：通过 42、不通过 0、没测到 28

| 入口 | 产品 | 方式 | 结果 | 说明 |
|---|---|---|---|---|
| `erp.page.home` | ERP | auto：page | 通过 | 0.8 秒 |
| `erp.page.products` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.inventory` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.stock-in` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.stock-out` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.stock-transfer` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.stock-card` | ERP | auto：page | 通过 | 0.6 秒 |
| `erp.page.stock-count` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.cashier` | ERP | auto：page | 通过 | 0.6 秒 |
| `erp.page.company` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.warehouses` | ERP | auto：page | 通过 | 0.6 秒 |
| `erp.page.team` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.purchase` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.suppliers` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.sales` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.pos-receipts` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.pos-shifts` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.clients` | ERP | auto：page | 通过 | 0.6 秒 |
| `erp.page.tasks` | ERP | auto：page | 通过 | 0.7 秒 |
| `cowork.page.home` | Cowork | auto：page | 通过 | 1.1 秒 |
| `cowork.page.intake` | Cowork | auto：page | 通过 | 0.7 秒 |
| `cowork.page.recon-bank` | Cowork | auto：page | 通过 | 1.0 秒 |
| `cowork.page.recon-income` | Cowork | auto：page | 通过 | 0.4 秒 |
| `cowork.page.recon-vat` | Cowork | auto：page | 通过 | 0.5 秒 |
| `cowork.page.stocktake` | Cowork | auto：page | 通过 | 0.4 秒 |
| `cowork.page.work` | Cowork | auto：page | 通过 | 0.6 秒 |
| `cowork.page.team` | Cowork | auto：page | 通过 | 0.4 秒 |
| `dms.page.home` | DMS | auto：page | 通过 | 1.4 秒 |
| `dms.page.connect` | DMS | auto：page | 通过 | 0.5 秒 |
| `dms.page.records` | DMS | auto：page | 通过 | 0.5 秒 |
| `dms.page.roster` | DMS | auto：page | 通过 | 0.5 秒 |
| `erp.page.never.push-logs` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.never.reconcile` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.never.guide` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.never.history` | ERP | auto：page | 通过 | 0.6 秒 |
| `erp.report.stock-count-variance` | ERP | manual | 没测到 | manual：交验收窗口按清单实测 |
| `shared.sidebar-order` | 共用 | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.company-menu` | ERP | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.detail.sales.draft` | ERP | auto：doc_detail | 没测到 | no draft ERP Sales Document |
| `erp.detail.sales.submitted` | ERP | auto：doc_detail | 通过 |  |
| `erp.detail.sales.void` | ERP | auto：doc_detail | 通过 |  |
| `erp.detail.purchase.draft` | ERP | auto：doc_detail | 没测到 | no draft Purchase Document |
| `erp.detail.purchase.submitted` | ERP | auto：doc_detail | 通过 |  |
| `erp.detail.purchase.void` | ERP | auto：doc_detail | 没测到 | no void Purchase Document |
| `erp.detail.stock-in.submitted` | ERP | auto：doc_detail | 通过 |  |
| `erp.detail.stock-out.void` | ERP | auto：doc_detail | 没测到 | no void ERP Stock Entry on the first list page |
| `erp.stock-count.switch.posted-to-active` | ERP | auto：record_switch | 通过 |  |
| `erp.stock-count.switch.active-to-posted` | ERP | auto：record_switch | 通过 |  |
| `erp.purchase.detail.not-permitted` | ERP | auto：not_permitted | 没测到 | 没配受限账号 SMOKE_USER_ERP_LIMITED |
| `erp.sales.detail.not-permitted` | ERP | auto：not_permitted | 没测到 | 没配受限账号 SMOKE_USER_ERP_LIMITED |
| `cowork.stocktake.switch` | Cowork | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.place.header` | ERP | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.products.new-dialog.opening` | ERP | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.inventory.opening.row-warehouse` | ERP | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.warehouses.express-new-hidden` | ERP | manual | 没测到 | manual：交验收窗口按清单实测 |
| `cowork.intake.upload` | Cowork | manual | 没测到 | manual：交验收窗口按清单实测 |
| `cowork.intake.viewer` | Cowork | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.stock-in.item-scan` | ERP | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.stock-out.item-scan` | ERP | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.purchase.item-scan` | ERP | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.sales.item-scan` | ERP | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.serials.dialog` | ERP | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.scan.pick-product` | ERP | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.stock-in.attach` | ERP | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.stock-out.attach` | ERP | manual | 没测到 | manual：交验收窗口按清单实测 |
| `shared.navigation` | 共用 | manual | 没测到 | manual：交验收窗口按清单实测 |
| `shared.upload.shrink-photo` | 共用 | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.print.stock-entry` | ERP | manual | 没测到 | manual：交验收窗口按清单实测 |
| `erp.products.find-by-code` | ERP | auto：find_product | 通过 |  |
| `cowork.tax-documents` | Cowork | manual | 没测到 | manual：交验收窗口按清单实测 |

手机宽弹窗内边距（`product.css`）三个产品各挑一张对照（`*-before|after-mobile.jpg`）：ERP 新建物料（左栏输入框原来比右栏短 15px，改后两栏同宽）、入库 / 调拨明细三栏（中间栏原来两边缩进，改后贴齐）、ERP / Cowork 添加用户；Cowork 银行对账新建任务（两栏，改后同宽）；DMS 操作员编辑（单栏，改前改后一致——单栏时首栏即末栏，官方本来就贴齐）。手机宽复扫 43 页无页面报错。

人工入口（22 个）与 6 个实验数据里没有的（草稿销售单、草稿 / 作废采购单、作废出库单、受限账号两项）交验收窗口；本次只动字段先后、
分栏与手机宽弹窗栏内边距，不碰扫码、上传、附件、打印、按台的逻辑。

## 收尾

- 实验栈 `pearnly-form-lab` 用完 `stop`（数据卷保留）；截图全量在共享笔记 `表单排版截图-11-01/`。
- 不部署（主会话攒批）。

