# 需求影响评估：打印第二批（采购订单、报价单、销售单、收银小票、交班小结、盘点表）

> 按「需求影响评估」模板填写，交主会话审定后施工。基线 `origin/main` `4561884f`，分支 `pearnly/print-batch2`。
> `P/` = `apps/pearnly_erp_portal/pearnly_erp_portal/`，`D/` = `P/pearnly_erp_portal/doctype/`。事实来自 10-26 全仓只读排查（带行号的都核过）。
> 第一批（领料单、收货单、带入库采购单的收货单）见 `2026-10-26-print-batch1.md`；总评估见 `2026-10-25-print-formats-评估.md` 第 3、4 节。

## 0. 需求原文与理解

- 原话：岳父「照纸质样板 1:1」打印；用户（10-25）「涉及到打印的地方，用官方原生打印功能……排查会涉及到打印的地方，都加上」「按照老丈人的模版1:1做，
  不要有任何不一样的地方，logo默认pearnly 但公司可自定义」。第二批清单来自总评估第 3 节 #4、#5、#6、#8、#9、#10。
- 理解：这六类单据各加一份原生 Print Format，在现有页面上能「打印」（原生 `/printview` 预览 → 打印 / 存 PDF）；版面照第一批同一风格
  （绿色、泰英双语、抬头三段、信息框、明细表、签字框），收银小票和交班小结是热敏 80 mm 窄版。
- **六类都没有岳父样板**：仓库、`~/pearnly-devtools`、共享笔记里只有 17（领料单）、18（收货单）两张。所以第二批是「照第一批同风格」，不是 1:1；
  样张出来后要交用户和岳父看一次。
- 不在范围：给单据补业务字段（PO 有效期、报价有效期、付款条件等，没有就留空线）；恢复 ERP 开税票（用户 09-23「开税务发票这件事也不要」，
  `2026-09-23-erp-sales-records.md` D1）；Express 那边的打印；邮件 / LINE 发 PDF；费用 / 服务采购的代收据、预扣税证明（维持现状）。

## 1. 现有同类与复用

| 现有 | 位置 | 第二批怎么用 |
| --- | --- | --- |
| 打印取数（Jinja 方法 `pearnly_print_context`） | `P/printing/context.py:26`，只认 `ERP Stock Entry`、`Purchase Document` | 扩成按单据类型分派；越权规则照原样（见第 5 节） |
| 公共版面（抬头三段、信息框、明细、签字框、水印、字体内嵌） | `P/printing/templates/_parts.html` | 复用；A4 单据直接用，热敏另做一套窄版宏 |
| 纯规则（分页 10 行、数量 / 金额 / 公历日期、勾选映射） | `P/printing/contracts.py` | 复用；加小票 / 交班的行折算、退货负号 |
| 打印按钮 `p.trade.print(doctype, name, format)` | `P/public/desk/transactions.js:142–146`（`window.open('/printview?…&no_letterhead=1')`） | 所有新入口都走它 |
| 公司 Logo（只认挂在本公司上的图） | `P/masters/company.py:551–561`、`P/printing/context.py:61–75` | 照用 |
| 收银台「打印收据」 | `P/public/desk/pos/pearnly-pos-receipt.js:43–48`（开 `/printview`，**不带 format = 原生默认格式**，:9 注释「小票模板还没做」） | 带上新小票格式 |

结论：扩展第一批的打印层，不新建第二套。

## 2. Frappe 原生能力

- Frappe 16.33.0 标准 Print Format（Jinja，`standard: Yes`，随代码放 `P/pearnly_erp_portal/print_format/`），原生 `/printview`、`download_pdf`（wkhtmltopdf 0.12.6）。
- 热敏 80 mm：Print Format 的 CSS 写 `@page { size: 80mm auto; margin: 0 }`，**浏览器打印**（`/printview?trigger_print=1`，收银台现在就这么开）按 80 mm 出；
  PDF 生成器要固定页高，原生做法是在 `.print-format` 上声明 `page-width` / `page-height`（`frappe.utils.pdf.read_options_from_html` 读这几项），
  按行数算页高。实验栈要实测：热敏打印机（驱动纸宽 80 mm）出纸不截断、PDF 一张不分页。
- 不用 Letter Head（同第一批：抬头整条画在 Print Format 里，`no_letterhead=1`）。
- 第一批踩过的坑照避开：内嵌字体 `src:` 换行写（Frappe `scrub_urls` 会给 `: url(` 补 `!important`，10-26 修，见 batch1 记录「字体换 Prompt」）。

## 3. 按类通用

属于「所有可打印单据」一类。第二批覆盖总评估第 3 节剩下的六项，覆盖后 ERP 有业务意义的单据都能打：

| # | 单据 | DocType / 条件 | 打印成 | 纸 |
| --- | --- | --- | --- | --- |
| 4 | 采购订单 | `Purchase Document` doc_kind=purchase_order，submitted / void | ใบสั่งซื้อ / PURCHASE ORDER | A4 横（同第一批） |
| 5 | 报价单 | `ERP Sales Document` doc_kind=quotation，submitted / void | ใบเสนอราคา / QUOTATION | A4 横 |
| 6 | 销售单 | `ERP Sales Document` doc_kind=receipt（非 POS 来源），submitted / void | 按第 4 节税票口径定标题 | A4 横 |
| 8 | 收银小票（含退货） | `ERP Sale` sale_type=sale / refund，completed / void | 销售：ใบเสร็จรับเงิน / RECEIPT；退货：ใบรับคืนสินค้า / RETURN RECEIPT | 热敏 80 mm |
| 9 | 交班小结 | `ERP Shift` status=closed（开着的不打） | สรุปการปิดกะ / SHIFT SUMMARY | 热敏 80 mm |
| 10 | 盘点表 | `ERP Stock Count`：进行中打「点数表」，已过账打「盘点结果」 | ใบตรวจนับสินค้า / STOCK COUNT SHEET | A4 竖（明细列多、行多） |

不覆盖：费用 / 服务采购（维持代收据、预扣税证明现状，总评估 #7）；Cowork、DMS（无业务单据）。
POS 来源的 `ERP Sales Document`（`source=pos`）在销售记录页本来就打开小票查看器（`P/public/desk/sales.js:54–60`），按小票打，不另出 A4。

## 4. 上下游数据

**字段来源（单上有的填，没有的留空线，不冒充）**

| 单据 | 抬头公司 | 对方 | 单号 / 日期 | 明细 | 合计 |
| --- | --- | --- | --- | --- | --- |
| 采购订单 | `company`（D/purchase_document.json:100） | `supplier_name` / `supplier_tax_id` / `supplier_address` / 分店 `supplier_branch_type`·`no`（:108–390）；编码、电话取供应商档 | `system_no`（PE…，:130）/ `doc_date`；`due_date` 印「กำหนดส่ง」 | Purchase Line：编码、品名、数量、单位、单价、折扣、金额（`line_total`） | `subtotal`、`discount_total`、`vat_amount`、`grand_total`（:191–228）；`wht_amount` 有才印 |
| 报价单 / 销售单 | `company`（D/erp_sales_document.json:77） | `buyer_name`（:120）、`address`（:135）；税号 / 分店见第 12 节 #3 | `system_no`（SI…，:102）/ `doc_date` | ERP Sales Document Line：品名、数量、单位、单价、折扣（`discount` / `discount_pct`）、金额 | `subtotal`、`discount_total`、`header_discount`、`vat_amount`、`grand_total`（:164–188）；销售单另印已收 `paid_amount`、付款方式 |
| 收银小票 | 单上**没有公司**：经 `terminal` → `ERP POS Terminal.company`（`P/cashier/access.py:9–10`） | `customer`（有才印） | `receipt_no`（SI…，:46）/ `sold_at`；收银台、收银员、班次 | ERP Sale Line：品名、数量×单价、行金额；含税行标记 | `subtotal`、`discount_total`、`vat_amount`（按 7/107 拆，`P/cashier/vat.py`）、`grand_total`、付款明细 `payments`、`change_amount`；退货印原单号 `return_against` 的 `receipt_no` 与原因 `note` |
| 交班小结 | 同上经 `terminal` | — | 班次 `shift_seq`、开 / 交班时间、收银员 | — | `P/cashier/shifts.py:167–205` `summary()` 实时算：笔数、退货笔数、毛额、退款、净额、按付款方式、找零、应有现金；`balances` 子表的应有 / 实点 / 差异 |
| 盘点表 | `warehouse` → 仓库的公司 | — | **没有单号**，用 `name`（STC-…）+ `title`；状态 Active / Posted；过账时间 | ERP Stock Count Item（独立表）：编码、品名、单位、库位；点数表：空白「实点」栏；结果：账面 `book_qty`、实点 `counted_qty`、差异 `difference` | 品项数、已点数、差异项数（:59–71） |

**按台商品**（车辆，`2026-10-26-vehicle-units-设计.md` 第 4 节第 5 条）：采购订单、销售单、小票在按台商品行下印 车架号 / 发动机号 / 颜色，
取单上 `serials`（ERP Line Serial：`serial_no`、`secondary_no`、`color`）。报价单阶段还没选台，不印。

**多仓库**：另一个开发窗口要给采购 / 销售单头加仓库栏。模板现在不读单头仓库，明细仓库（行上的 `warehouse`）也不印；那边定了字段名再按主会话口径加。

**税票口径（照总评估第 4 节写死，不做选项）**

- ERP 不开税票（用户 09-23，D1 已删税票类型、税票号、买方税票要素）。所以六类模板一律**不印「ใบกำกับภาษี」字样**。
- 销售单：接了 Express 的公司（`linked(company)`，`P/express/push.py:41–44`；公司页按 `express_book`），正式税票由 Express 出、以 Express 单号为准，
  ERP 打 **ใบส่งของ / DELIVERY NOTE**，单号框下再印 Express 单号 `express_docnum`（有才印）；没接的打 **ใบเสร็จรับเงิน / RECEIPT**（非税收据）。
- 收银小票：印 **ใบเสร็จรับเงิน**，不印「ใบกำกับภาษีอย่างย่อ」——接了 Express 的公司，POS 每天由 CC 在 Express 记一张 HS「ใบกำกับภาษีอย่างย่อ 起-止」
  （`2026-10-12-cc-closed-loop.md:320–321`），税务凭证在那边；小票单独充当简易税票还要收银机向税务局备案（ภ.พ.06），没有记录说客户备案过。
  公司登记了增值税（`vat_registered=yes`）时小票照印「ราคารวมภาษีมูลค่าเพิ่มแล้ว」和拆出的税额（陈述事实，不是税票）。
- 退货小票印 **ใบรับคืนสินค้า**，不叫贷项单（ใบลดหนี้，ม.86/10）；接 Express 的退货在 Express 出 SR。
- 报价单、采购订单本来就不是税务凭证。

**Express**：打印只读 ERP，不写 Express；销售单 / 采购订单印 Express 单号只读 `express_docnum`。**CC 桌面版**：载荷不变。**Cowork / DMS**：不涉及
（打印层在 ERP App 里，不是 `pearnly_core` 共用层）。

## 5. 隔离与权限

全部走原生打印权限：Frappe 16 `validate_print_permission` 是「读或打印任一就放行」，各单的 `has_permission` / `permission_query_conditions` 已按公司、本人、
仓库、收银台过滤（`P/hooks.py:271–274, 512–518`），能读这张单的人才能打。第一批修过的两处越权规则照搬到每一种新单据：

1. **只打库里已有、当前用户读得到的单**：`pearnly_print_context` 把白名单从两种扩到八种（加 ERP Sales Document、ERP Sale、ERP Shift、ERP Stock Count），
   一律按 `name` 从库里重取、`has_permission("read")` 再算；页面传来的整份 JSON 一个字段都不用。
2. **关联资料按库里那张单取，且不出公司边界**：
   - 抬头公司：采购 / 销售单用单上 `company`；小票 / 交班用 `terminal` → 收银台的公司；盘点用 `warehouse` → 仓库的公司。不读当前用户的默认公司。
   - 客户 / 供应商档（编码、电话、税号）：只在档属于本单公司或是本地共用档时读（`P/masters/access.py:84–102` 的口径：有 `express_company` 的档只在那家公司可见），
     否则只印单上自己存的名称 / 地址。
   - 退货小票的原单号：原单要同一收银台、当前用户读得到才印。
   - 盘点明细是独立表 ERP Stock Count Item：先核盘点单读权限（`P/inventory/access.py:73–126`，要 `count.read` 且仓库公司可用），再按 `count = 本单` 取明细。
   - 交班汇总用 `shifts.summary()`，它按班次取数；打印前核 `ERP Shift` 读权限（`cashier.read` / `shifts.read` + 收银台公司 + 非全记录角色只看本人）。
   - Logo：只读挂在该公司上的文件（第一批规则不变）。
- 不加 print 权限位（第一批已复核：读权限就能打，不无谓放宽）。

## 6. 入口：网页、手机、LINE、拍照

| 单据 | 在哪点打印（现状 → 加什么） |
| --- | --- |
| 采购订单 | 详情页 `P/public/desk/purchase-form.js:263–265` 的菜单「Print」现在只给入了库的单开收货单格式 → doc_kind=purchase_order 且 submitted / void 时开采购订单格式；入了库的照旧开收货单 |
| 报价单 / 销售单 | 详情页 `P/public/desk/sales-form.js:214–224`（现在只有 Express、收款、Cancel）→ 菜单加「Print」，submitted / void 可打，按 doc_kind 选格式 |
| 收银小票 | ① 收银台结账后「打印收据」`p.pos_receipt.print`（`pearnly-pos-receipt.js:43–48`）带上小票格式；② 收银记录页小票查看器 `p.trade.receipt_view`（`transactions.js:188–226`，现在只有退货 / 作废 / Express）加「Print」；③ 退货完成后同一按钮打退货小票 |
| 交班小结 | 交班记录页汇总弹窗 `P/public/desk/pos-shifts.js:34`、收银台交班弹窗 `pearnly-pos-shift.js:158` 加「Print」（只对已交班） |
| 盘点表 | 盘点页 `P/public/desk/stocktake.js:190–212`：进行中加「打印点数表」，已过账加「打印结果」——都用原生「Print」词条 + 格式区分（见第 10 节） |

- 手机：同一页面窄屏照样有这些按钮，打开原生打印预览、可存 PDF；热敏打印要接收银台那台电脑的打印机，手机不涉及。
- LINE、拍照、CC 对话：不涉及。

## 7. 共用件与连带回归

- `scripts/impact/shared-parts.toml` 现在**没有打印条目**。第二批会改：`P/printing/`（第一批三张单也在用）、`transactions.js` 的 `p.trade.print` 与小票查看器
  （已登记共用件 `erp-transactions`，入口 inventory / stock-in / stock-out / stock-card / stock-count / purchase / sales / pos-receipts）、
  `pearnly-pos-receipt.js`（登记在 `erp.page.pos-receipts`）、`pearnly-pos-shift.js`（`erp.page.pos-shifts`）。
- 施工同一提交里新登记共用件「单据打印」（`P/printing/**`、`print_format/**`、`p.trade.print`），入口 = 9 个打印按钮（第一批 3 + 第二批 6 类的入口）；
  `check-impact.py` 出清单，每个入口在实验栈点一次记「通过 / 不通过 / 没测到」。改 `erp-transactions` 要连带回归它登记的 8 个页面。
- 冒烟：每种格式取一张单，原生 `get_print` 出 HTML 含单号、PDF 页数符合（A4 一页 / 热敏一张）；非管理员岗位账号各跑一遍；
  越权：伪造 JSON、别公司的单、别人的小票（非全记录角色）拒打。

## 8. 存储与成本

不落盘（原生即时生成），无外部调用、无月费。PDF 在 web 进程里生成，单张秒级；热敏 HTML 小。字体内嵌后每张页面多约 46 万字符（两份 Prompt），
Frappe 出 PDF 时 cssutils 解析一遍（第一批测 Sarabun 版约 1 秒），施工时在实验栈量一次 Prompt 版耗时。

## 9. 删除、作废、追溯

作废单（status=void）照样能打，加第一批同款「ยกเลิก / CANCELLED」水印（热敏版用一行横幅代替斜水印）。不保存打印件，没有要删的。
已写进 Express 的单作废照现有流程（打印不参与）。

## 10. 文案与官方 UI

- 版面文字是模板内容（泰英双语，照第一批措辞风格），不进界面词条。新增的单据标题与栏位名清单（泰 / 英）随样张一起交主会话看。
- 界面只加按钮：用 Frappe 自带「Print」词条；盘点两种打印若需要区分，用菜单项「Print」+ 打开时格式下拉（原生 `/printview` 自带格式选择），不新增词条。
  所以界面文案新增 0、改动 0（`check-copy.py` 照跑）。
- 官方组件：原生 Print Format、`/printview`、页面菜单 `add_menu_item`、Dialog 自带按钮；样式只在 Print Format 内。

## 11. 测试与验收

- 单元（`tests/test_erp_printing.py`，纯规则开发检查跑、版面在生产镜像跑）：每种格式渲染出标题 / 单号 / 合计；缺字段留空不报错；超过 10 行（A4）续页；
  小票多行不分页、退货负号与原单号；交班汇总各数；盘点点数表不印账面数（见第 12 节 #5）、结果表印差异；作废水印；按台商品行下印车号；
  销售单接 / 没接 Express 的标题。
- 越权（`tests/test_erp_print_security.py`，生产镜像）：八种单据伪造 JSON、读不到的单拒打；客户 / 供应商档跨公司不读；退货原单读不到不印。
- 实验栈（本机一次性，线上同一镜像）：六类各出一张 PDF + 预览截图；热敏版用浏览器打印预览量宽度；非管理员岗位点一次每个入口。
- 验收：样张交主会话转用户和岳父看（没有样板，要人看过一次）；收银小票最好在真热敏打印机上打一张（测试机若有，动之前先问）。

## 12. 要问用户 / 客户的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 六类都没有岳父样板，照第一批风格做、样张出来再给岳父看，行吗？ | 行；岳父有纸质样板就拍照给我们，到时改成 1:1 | 返工量 |
| 2 | 销售单标题：接 Express 的打「ใบส่งของ / DELIVERY NOTE」、没接的打「ใบเสร็จรับเงิน / RECEIPT」，都不印税票字样 | 照此写死（ERP 不开税票，09-23 定） | 税务风险 |
| 3 | 报价单 / 销售单要不要印客户税号、分店号？（单上 09-23 删了这两栏，客户档上有） | 印：从客户档取，只在档属于本单公司或是共用档时取，没有就留空 | 版面一栏 |
| 4 | 收银小票印「ใบเสร็จรับเงิน」而不是简易税票，行吗？ | 行（Express 每天记 HS 简易税票；小票当税票要收银机备案 ภ.พ.06） | 税务风险 |
| 5 | 盘点点数表要不要印账面数？ | 不印（盲盘，防照抄）；结果表印账面 / 实点 / 差异 | 盘点准确性 |
| 6 | 交班小结要不要打印？ | 要，热敏一张（交班时对钱用） | 范围 |
| 7 | 热敏小票页眉页脚（店名、地址、电话、税号、「ขอบคุณ」之类）用什么？ | 只印公司抬头资料（名称、分店、地址、电话、税号）；不加感谢语等说明文字（文案口径） | 版面 |
| 8 | 小票打印方式：浏览器直接打到热敏打印机（现在的做法），还是出 PDF？ | 浏览器打印为主，PDF 作存档 | 实现细节 |

## 13. 工作量与排期（机器耗时）

1. 打印层扩展：`pearnly_print_context` 分派 + 八种越权规则 + 单元 / 越权测试 —— 约 1 小时。
2. A4 三份（采购订单、报价单、销售单）+ 两个入口 —— 约 1.5 小时。
3. 热敏两份（小票含退货、交班）+ 三个入口 + 浏览器 80 mm 实测 —— 约 2 小时。
4. 盘点两种版面 + 入口 —— 约 1 小时。
5. 共用件登记、check-impact、实验栈出样张与逐入口实测、全闸门、候选离线检查 —— 约 1.5 小时（含机器跑）。
依赖：1 → 2、3、4（可并行写，同一工作树一个写者）→ 5 → 交样张等用户 / 岳父看。第 12 节 #2、#4、#5 定了才能定稿对应模板。
