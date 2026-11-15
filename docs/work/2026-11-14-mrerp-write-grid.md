# MR.ERP 写账四格验齐（11-14）

分支 `pearnly/mrerp-write-grid-99fa21`，基线 main a838abe54。范围：CC 写账总表里 MR.ERP 一侧四格（Cowork / ERP × 直写 / 代录）验齐、缺的补上，
让 `MRERP_LEDGER_ENABLED` 打开时写账路径是验过的。开关本身由公司接入窗口（`pearnly/company-onboarding-b2d996` 第 3 批）打开，本分支不改它。
进度与单号台账的实时版本在主会话共享笔记 `MR.ERP写账四格-进度-11-14.md`；这里留结论。

## 环境

- 本机 colima 栈 `pearnly-mrerp-lab`（`infrastructure/compose/regression-lab.yml`，端口 18841–18843），站点 `cowork.regression.localhost`、
  `erp.regression.localhost`，挂本分支源码。真写只写 mrerp4sme.com · test01 · TEST2019（6:1）；test01 只看得到 TEST2019 / TEST2020。
- MR.ERP 连接凭据在实验栈后台写入（不经测试机）；导入菜单 / 主档默认值按 CC「选一次」的同一接口记。
- TEST2019 登记税号是样板值（无效），方向按工作空间税号判（实验栈合成 0105989581512）。

## 第 2 步：Cowork 直写 MR.ERP

入口：`pearnly_cowork.assistant.tools._push_one`（CC 推送工具对每张票走的函数）→ `push.mrerp.runner.run`。每张单读回列表与单据查看页
（`allform.php?id=N&status=vie`）逐格比对。

| 单号 | 单型 | 结果 |
|---|---|---|
| PG691114-379821 | 采购货品 68 | ✅ 供应商、商品自建；7 (แยก) 105.00；1,605.00 |
| PN691114-425545 | 采购费用 454 | ✅ นอกระบบ 单行含税 856.00 |
| SI691114-986168 | 赊销 119 | ✅ 客户、商品自建；140.00；2,140.00 |
| SE691114-426386 | 现销 126 | ✅ 现金栏 321.00 |
| SI691114-865946 | 赊销（行折扣，修前） | ❌ 票 288.90 → 321.00 |
| PG691114-730046 | 采购货品（无税卖家，修前） | ❌ 票 500.00 → 535.00 |
| SI691114-762644 | 赊销（整单折扣，修前） | ❌ 票 963.00 → 1,070.00 |
| SI691114-551405 / PG691114-856585 / SI691114-537862 / PG691114-995716 | 上面三种 + 采购整单折扣（修后） | ✅ 288.90 / 500.00 / 963.00 / 963.00，写后核对 ok |
| SE691114-621451 | 现销（删票路径） | ✅ 排队中删票被跳过、照常写入；写成后删票只删 Pearnly 一侧 |

### 修复（共用层，直写与代录、Cowork 与 ERP 同一份）

1. 明细金额：`mrerp.push.xlsx.fmt.line_amount` 依次认 `amount` / `subtotal` / `total` / `line_total`。Cowork 识别的明细金额键是 `subtotal`
   （同 Express 读法），此前生成器只读 `amount`，行金额被改成「数量 × 单价」。
2. 税率：`fmt.mrerp_tax_label`（原销售判定 `mrerp_sales_tax_label`，旧名留作别名）采购货品也用，不再写死「7 (แยก)」。
3. 折扣：`fmt.allocate_discount`（原现销 `_cash_import_details` 的摊分）赊销、现销、采购货品同一做法；对不上账时照票写表头折扣。
   代录网页表单行金额不能另填，行折扣与整单折扣都放表头折扣（`operator_draft._form_discount`）。
4. 写后核对：`mrerp.push.readback.amount_check` 拿列表金额比票面总额，`execute` 回执带 `meta.verify`（同 Express 形状，
   `written_check` 白名单加 `amount`）；Cowork `push.native.service._written_check` 不再跳过 MR.ERP，记 `verify_state`；
   ERP `mrerp.contracts.settle` 核不上记 `mismatch`。只记不拦，单号照记。

测试：`tests/test_mrerp_import_amounts.py`（修前红 9 条，修后 10 条全绿，进 development.yml）；`golden-rules.json`、
`erp-direct-golden.json` 只多 `meta.verify: ok`，导入表逐字节不变（假站点列表金额改成可配，ERP 样本按 1,605.00）。

### 事实（只记录）

- MR.ERP 采购导入把「เงินสด」一律勾上（`impaptran/component/importpc.php` 写死 `cash=1`）；记账按菜单关联科目、付款按未付余额，导入单仍是应付。
- 销售在 MR.ERP 里的发票号是「SI + 我方号」，票面发票号不进 MR.ERP。
- 费用档按 2026-07-09 口径记 นอกระบบ、含税全额进费用、不抵进项。
- 自建供应商应付科目跟「现有档案最常用」走；TEST2019 现在是 1111-02（เงินสดย่อย），是测试账套数据所致。
- Cowork 没有「撤回已放行推送」（A 线 11-13 评估，口径已定未施工），MR.ERP 直写排队中的单同样撤不回。

## 发布说明（金额修复：df55c23f7、a357c146e、3be093a5c）

**影响**：Cowork 推到 MR.ERP 的单（网页推送、CC 推送、定时任务、自动推都算），以下三种在 11-14 之前写进 MR.ERP 的金额与票面不符，推送却显示「已写入」：

1. 明细有行折扣（某行金额不等于数量 × 单价）：MR.ERP 按「数量 × 单价」记，多记了行折扣。
2. 卖家不收增值税的商品采购：MR.ERP 多加了 7% 增值税。
3. 有整单折扣的赊销、商品采购：折扣没写进 MR.ERP，多记了折扣额（现销不受影响）。

费用采购（菜单 454 那类）、现销、没有折扣且带 7% 税的单都不受影响。ERP 直写 MR.ERP 在生产上没开，不受影响。

**上线后**：

- 新推的单按票面金额写；写进去后读回 MR.ERP 金额核对，对不上就不显示「已写入」，转「要处理」，并给出 MR.ERP 单号。
- 已写错的旧单不会自动改（MR.ERP 只读、我们不改已导入的单）。

**用户要做什么**：

- 按主会话发的清单（只读脚本 `mrerp-amount-scope-1114` 的结果：租户、单据号、MR.ERP 单号、票面金额、MR.ERP 里的金额），由会计在 MR.ERP 里打开那张单，把明细金额、折扣或税率改成与票面一致（Pearnly 上这张单已是「已写入」，不要再推一次）。
- 清单为空时无需操作。

**线上影响范围（11-14 只读，两次 SSH）**：生产 Cowork 容器只有 ccdemo / erpcowork / testcowork 三个站点，推送记录全部是 Express（success 43 / 121 / 148 等），adapter=mrerp 的记录 0 条；ERP 的 MR.ERP 总开关在生产关着。线上没有被这三处写错的 MR.ERP 单，**用户无需处理**。脚本与说明在 `~/pearnly-devtools/mrerp-amount-scope-1114/`。

## 第 3 步：ERP 直写 MR.ERP（开关只在实验进程里打补丁）

基线 PG691114-058815 / PN691114-339343 / SI691114-488198 / SE691114-336246 全过；转账收款进现金栏（SE691114-861564）、没税号客户的未收款销售被改成现销（SE691114-614656）修后复验 SE691114-208305、SI691114-395622。
写后核对核不上：Cowork 转人工、原因与记录带 MR.ERP 单号（真站点 SI691114-416323）；ERP 记 `mismatch` 带单号。

## 现购（分支 pearnly/mrerp-cash-purchase，主会话 11-14 定做法）

MR.ERP 导入采购不看「เงินสด」勾；应付那笔贷方取菜单关联科目，没设取**记账用供应商**（导入第 9 列）档案的科目
（`aptran/component/aptranpc.php insacglhd`）；进项税记录挂**开票用供应商**（第 10 列，`impaptran/component/importpc.php:909`）。
所以现购 = 第 9 列放一个科目是现金类的供应商、第 10 列照写真实卖家，纯导入，不要 CC 桌面版。

- 判定：采购票 `payment_is_paid` 为真（入口选现金、ERP 已付都写成 `posting_payment_manual=cash`）。
- 第 9 列（`mrerp/push/defaults.py cash_supplier`）：依次 MR.ERP 初始库自带的 `CODE_NOAP`、`เงินสด`，科目（`apmas/component/dupdata.php`
  第 14 列）是参考科目「เงินสด」或这份账套选过的银行科目才用；都没有 → 自建 `เงินสด`、科目 = 参考科目「เงินสด」；`เงินสด` 已有但科目
  不是现金类 → 只挡现购票（`ERR_MRERP_CASH_SUPPLIER_NOT_CASH`），同批赊购照写，不改别人的档案。
- 入口：共用契约、Cowork 推送准备、录入弹窗、LINE、定时任务（网页与 CC 前端）、CC 工具说明、ERP 已付采购原来的「只能赊购」都去掉。
- 代录：现购在 `operator_draft.stage` 转人工（`ERR_MRERP_OPERATOR_CASH_PURCHASE`）——配方没有开票用供应商（txtapmas_inv）一格。
- 自建「เงินสด」没建成：现购停下 `ERR_MRERP_CASH_SUPPLIER_NOT_CREATED`（导入预检兜底同一原因）。
- 同一做法前一窗口也提交过一份（`pearnly/mrerp-write-grid-99fa21` 92d6d0bad，未推、全闸门不过：入口仍只给赊账、ERP 无词条）；
  合并成本分支一份：取它的「建不成」原因码与导入表测试（TEST2019 PG691114-571007：借进项税 105 / 借采购 1,500 / 贷 1111-02 1,605），
  那条提交不再推。

真写 test01 · TEST2019（实验栈挂本分支源码）：

| 单号 | 入口 | 读回 |
|---|---|---|
| PG691114-123835 | Cowork 直写，现购货品 749.00 | 记账用 CODE_NOAP、开票用真实卖家；日记账 借 5110-01 700 / 借 1161-11 49 / 贷 1111-02 749 |
| PN691114-563544 | Cowork 直写，现购费用 321.00 | 同两列；นอกระบบ；借 5390-09 / 贷 1111-02 |
| PG691114-575570 | ERP 直写，已付采购 321.00 | 账本栏 written；借 5110-01 300 / 借 1161-11 21 / 贷 1111-02 321 |
| PG691114-394983 | 定时任务推送（`folder_intake.push.push_one`），现购 428.00 | 借 5110-01 400 / 借 1161-11 28 / 贷 1111-02 428 |
| （无） | 实验进程把候选换成挂 2120-03 的真实供应商 | 推送记录 manual `ERR_MRERP_CASH_SUPPLIER_NOT_CASH`，MR.ERP 无单 |

与 Express HP 对照：同样借采购、借进项税、贷现金，进项税挂卖家；差别是 MR.ERP 单据列表与应付按记账用供应商显示「ยังไม่ระบุผู้จำหน่าย」。

共用件连带（`scripts/check-impact.py`：mrerp-rules、express-rules、erp-transactions）：express-rules 只删了 MR.ERP 现购那一条拒绝，
Express 分支没动（`test_cowork_push_contract` 覆盖）；erp-transactions 只删了 `transactions.js` 原因表里 `mrerp.purchase_cash` 一项，
单据页其余逻辑没动——各入口没逐个实测，由本地全闸门与候选离线覆盖，开关打开前的 testerp 验收（清单 B 段）再点 ERP 采购 / 销售页。

## 公司接入接缝

ERP 单据写进 MR.ERP（含写入核不上）后调一次 `pearnly_erp_portal.mrerp.masters.after_write(company)`（公司接入第 3 批窗口提供：去重、
排进主档队列整读一次）；导入失败不调；它出错只记日志、写账结论不变；那边没合入前 import 不到就跳过（`mrerp/push.py _after_write`）。

## ERP 单据页账本弹窗认 MR.ERP（验收清单 B13，主会话定「乙」）

同一个弹窗按账本来源取文案与调用（`express/api.py doc_ledger`：单据记的账本，没记过看公司 `mrerp.push.handles`；公司接入第 3 批
加来源字段后只改这一处）。MR.ERP 的单：标题与单号栏写 MR.ERP；停在要选导入菜单 / 默认值的出选项（`mrerp.push.answer`）；
失败或停下（非结果不明）的「再写一次」走 `mrerp.push.send(manual=True)`（同 CC 推送，清被踢标记）。Express 的单行为不变
（`test_erp_express_direct` 195 条照过，另加 3 条）。四语新词条沿用 Cowork 现成的 MR.ERP 菜单 / 设置文案。
真验：SD-00039 先因被踢标记失败，弹窗 `can_retry`；点再写一次 → 写成 SI691114-190493（列表 107.00），弹窗显示 MR.ERP 单号、不再给重推。

## 单号与税务发票号、税率按 MR.ERP 的规则（用户：不要写死）

MR.ERP 源码：导入第 1 列 = 单号序号（MR.ERP 拼菜单前缀，同菜单与税库不重号）；第 11 列「เลขที่บิล」= 税务发票号（单据 vatnum、
销项 / 进项税记录 svatno / pvatno，税报告「ใบกำกับภาษี เลขที่」取它，≤20 字、不查重）；网页不填的默认 = 前缀 + 单号。
税率表 `tbl_othvatrate`（名称、税率、价内外、throwacvat=1 不进税记录）。

- 第 11 列：票面发票号原样；没有时 = 所选菜单前缀（读该菜单新单表单 `txtdoctype`）+ 单号；超过 20 字预检拦下，不截。
  原来销售写死「SI」+ 我方号（现销前缀是 SE 也写 SI）、采购截 20 字。Cowork 销售票没有发票号本来就被 `ERR_NO_INVOICE_NO` 挡下。
- 税率名称按这家公司的税率表（单据接口 `txtothvatrate`），读不到才用初始库名。
- 无税卖家的采购：「นอกระบบ」（不进进项税记录，同 Express 税额 0 不写 ISVAT），不再写零税率「0」（主会话 11-14 定）。
- 第 1 列保留自造唯一号（MR.ERP 允许外部给定；用票面号会撞、用网页流水号会和以后手录撞）。

真写：SI691114-906964 txtvatnum = 票面 IV6911-0001；SE691114-435400 = RC6911-0002；PG691114-061053（无税卖家）税率 นอกระบบ、
txtvatnum = 供应商票号 NV-6911-77、税额 0；ERP 销售 SI691114-190493 = ERP 发票号 SI2569-000008。
税报告（report 232/45、226/43）只列已归入报税期（vatdatepass）的记录，要人在 MR.ERP 点使用权后才有，本窗口没替人做归期；
发票号以单据页 vatnum 读回为准（导入同一列同时写税记录，`impartran/component/importpc.php:868, 978`）。

## 文案改动（主会话转拼车窗口用词盘点，11-14）

| 键 | 原（本分支 11-14 新加） | 改为 | 依据 |
|---|---|---|---|
| `mrerp.default.store` | 仓库 / Warehouse / คลังสินค้า / 倉庫 | 仓位 / Location / ตำแหน่ง / 保管場所 | 全产品统一叫「仓位」（用户 11-06），四语沿用 ERP 现成 `trade.location` |
| `mrerp.default.product_group` | 商品分类 / หมวดสินค้า / 商品分類 | 物料分类 / กลุ่มสินค้า / 商品グループ | 界面统称「物料」（用户 11-06），四语沿用 ERP 现成 `express.error.ITEM_GROUP_NOT_FOUND` |
| `Not Permitted`（`mrerp/connection.py` 三处、`express/api.py` 新加一处） | Not Permitted | Not permitted | Frappe 原生词条 |

## 待办（不在这批）

- 公司接入第 3 批与本分支都进 main 后：去掉 `mrerp/push.py _after_write` 的 ImportError / getattr 兜底，测试换成真调用（拼车窗口在集成分支补跨边测试）。
- 「多年度账套按单据日期选当期」（`mrerp.catalog.pick(company, day, wanted)`）：`mrerp/push.py prepare`、`mrerp/choices.py`、`mrerp/ledger_check.py`、
  `express/cc_tools.py` 读公司 `mrerp_account_set` 的地方改调它；另开分支，基于公司接入第 3 批分支头，它先合。

- 代录现购（⑥⑧）：先转人工；要支持得在 CC 桌面版配方加开票用供应商 txtapmas_inv 一格并在 TEST2019 探路。总表
  （`pearnly/cc-coverage-matrix-eccdec` 上的 `2026-11-12-cc-coverage-matrix.md`，未合入）合入时补这一行。

- 一张票里部分行免税、部分行带税：现在被「税率异常」预检挡在写入前；11-09 定的「按税率拆两张同号单」没施工。
- ERP 单据页「再写一次」对 MR.ERP 的单走不到（做法见开关前验收清单 B13）；代录两格（⑥ 只做了格子 id 只读核对，⑧ 没实现）；定时任务电脑读文件夹那一半。
- 给用户定：销售在 MR.ERP 里的发票号是「SI + 我方号」，票面发票号不进 MR.ERP，要不要改。

## 自建客户 / 供应商的称谓与名称（11-15）
- 核对依据：MR.ERP 源码 `impapmas/component/importpc.php`、`imparmas/component/importpc.php` 把第 3 列称谓（须是 `tbl_prefix` 里已有的非空项）与第 4 列名称分别存进 `idprefix`、`name`；它自带的样例账套是「นาย」+「อนุรักษ์ ทดสอบ」；单据打印拼「称谓 + 空格 + 名称」（如 `report/115/all/260.php`），销项 / 进项税报告（232/45、226/43）只印 `name`。
- 之前：称谓按这家公司自己的称谓表取（`armas/allform.php` 的下拉，不用 Express 的称谓表），名称栏却写整个票面名，打印成「บริษัท บริษัท ทดสอบ จำกัด」。
- 现在：`masters.split_prefix` 把开头匹配上的称谓（取最长）从名称里拿掉；不是称谓开头（称谓用第一项，MR.ERP 网页默认）或拿掉后为空，名称原样。Express 那张称谓表（11-15 prename-shop 改的 `express_rules/push/common.py`）MR.ERP 推送不用，也不照搬。
- 影响面：只改自建客户 / 供应商的导入表；客户 / 供应商代码仍按税号或原票面名推导，已有主档不改。黄金样本只变推送过程中客户 / 供应商主档那一次上传（摘要与大小，少了称谓那几个字节）。
- 留给真写：TEST2019 新供应商「บริษัท …」一张，读回 `apmas` 名称栏不带称谓、单据打印只出现一次称谓。
- 11-15 真写复验后补：同一种法人 / 称呼的几种写法算同一项（`masters._SAME_FORM`：บริษัท…จำกัด (มหาชน) = บมจ.、บริษัท…จำกัด = บจก.、ห้างหุ้นส่วนจำกัด = หจก.、ห้างหุ้นส่วนสามัญ = หสม.、นางสาว = น.ส.），称谓取这家公司表里有的那种写法，简称自带的「จำกัด / จำกัด (มหาชน)」换写法时跟着补上或去掉。MR.ERP 自带的称谓表（`stderp2020.sql` tbl_prefix）只有 บริษัท、หจก.、หสม、น.ส.、นาย、นาง、คุณ 与空项，其余由各公司在网页里自己加。TEST2019：SI691115-723568 修前建成「บริษัท」+整名（测试数据留着），修后 SI691115-636349 是「หจก.」+「ทดสอบคำนำหน้า สาม」。
- 口径（主会话 11-15 定）：按 MR.ERP 自己的存法，称谓、名称分两栏。

### 上线说明（MR.ERP 写账，随开关打开时发）
- MR.ERP 税报告（销项 / 进项）上的对方名称不带称谓（如「ทดสอบ หนึ่ง จำกัด」不带 บริษัท），与 MR.ERP 原生一致：MR.ERP 的税报告只印名称栏，称谓另存一栏，单据打印才把两者拼在一起。
- 现购在 MR.ERP 单据列表与应付里显示记账用供应商「ยังไม่ระบุผู้จำหน่าย」（CODE_NOAP），真实卖家在开票用供应商与进项税记录里——已知并经用户确认的表现（用户 11-15「现购显示未指定供应商也可以接受」，主会话转）。
- Pearnly 导入的税记录在 MR.ERP 里是「未归期」，月底在 MR.ERP 归期后才出现在税报告里。
