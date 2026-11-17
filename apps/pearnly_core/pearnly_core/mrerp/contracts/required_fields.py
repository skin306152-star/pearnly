"""MR.ERP 必填字段登记表（多产品共用，纯契约；照 Express 那张 ``express_rules/contracts/required_fields.py`` 的形状）。

原则同 Express（用户 11-12）：写一张单、建一条主档时 MR.ERP 导入表要有值的每一列，只能是三种之一——**后台自动定**（有依据）、
**引导卡问**、**复核卡可改**。「缺了就静默写个默认值」「缺了直接失败让人看不懂」都算缺口。

**结构是约定**（验收体系 ``docs/development/acceptance-testing.md`` §6）：顶层常量 :data:`REQUIRED_FIELDS` 是一个**字典字面量**，
键 ``"mrerp:<单据>"``，值是字段行列表。验收的检查按语法树读它、不导入，所以键与每行的 ``ask`` 必须写成字符串字面量；``ask`` 是题目
代号（与 ``write_precheck.contracts.KINDS`` 一致，不是文案），MR.ERP 用到的是 ``mrerp_menu``（导入菜单）与 ``mrerp_default``
（主档默认值，`pearnly_erp_portal.mrerp.contracts.needed` / `pearnly_core.mrerp.needs`），**改名要先告诉主会话转验收窗口**。

**MR.ERP 按位置登记**：导入表是泰文表头、而且有重名（采购表头两个「วันที่」、主档两个「คำนำหน้า」），所以 ``field`` 写
``"<第几张表>.<列字母>"``（``"1.A"``、``"2.F"``），另加 ``label`` = 那一列的泰文表头原文（含原样的空格与错字，如 ``"ที่อยู่ 3 "``、
现销的 ``"พื้นทีการขาย"``）。表的序号取生成器里表头列表名末尾的数字（``_HEADERS_2`` = 第 2 张），没有数字的是第 1 张。

每个字段行的其余键同 Express：``auto``（来源，取值与 Express 那张的 :data:`AUTO_SOURCES` 逐个相同——照抄而不导入，免得纯契约依赖 express_rules；测试断言两边相等）、``ask``、``review``、
``default``（缺了就写的值与依据文档，``docs/`` 开头）、``gap``、``note``。空着不写的列登记为 ``constant`` 并在 note 写「留空」。

- ``mrerp:PURCHASE``：采购 impaptran（菜单 67 货品 / 453 费用共用一张表，费用档的差别写在各行 note）；
- ``mrerp:SALES_CREDIT``：赊销 impartran；``mrerp:SALES_CASH``：现销 imparse；
- ``mrerp:APMAS`` / ``mrerp:ARMAS`` / ``mrerp:STKMAS``：自建供应商 impapmas / 客户 imparmas / 物料 impstkmas。

:data:`WRITERS` 登记哪个导入表生成器写哪几个键；测试按语法树取出生成器的表头列表，第 i 张表第 j 列没登记就红，登记了生成器里没有的列
也红。:data:`OPERATOR_KEYS` 登记 CC 桌面版 MR.ERP 代录草稿的键（``companion/src/companion/mrerp_operator/recipe.py`` 的
``DRAFT_KEYS``，服务端组装在 ``mrerp/push/operator_draft.py`` 的 ``block()``）落到哪个键的哪一列；落不到导入表列的写进
:data:`NOT_COLUMNS` 并写明原因。

不依赖 Frappe、不读库、不做 IO。
"""
from __future__ import annotations

from typing import Dict, List, Optional

# 与 `express_rules.contracts.required_fields.AUTO_SOURCES` 同一组（含义见那边）；tests/test_mrerp_required_coverage.py 断言相等
AUTO_SOURCES = frozenset({"answer", "book_mode", "book_setting", "computed", "constant", "face", "history", "machine", "master", "remembered", "sequence", "session", "source_code"})

REQUIRED_FIELDS = {
    "mrerp:PURCHASE": [
        {"field": "1.A", "label": "เลขที่", "auto": ["computed"], "note": "单据日期（佛历 YYMMDD）+ 票据 id 算出的 6 位序号，同一张重推同号；MR.ERP 拼菜单前缀"},  # 单号：generator.derive_mrerp_invoice_no
        {"field": "1.B", "label": "วันที่", "auto": ["face"], "note": "单据日期同时决定写进哪本年度账套（mrerp.catalog.pick，账套由会话给定；同期两本出 account_set 题），账套本身不是导入表的一列"},  # 票面开票日：fmt.fmt_date(history['invoice_date'])
        {"field": "1.C", "label": "อัตราภาษี", "auto": ["computed", "book_setting"], "note": "货品：fmt.mrerp_tax_kind(purchase=True) 判价内 / 价外 / 无税（นอกระบบ）；费用档固定「不进税报告」那一档；名称按这家公司的税率表（_mrerp_vat_rates），读不到用初始库名"},  # 税率：fmt.mrerp_tax_label / purchase.expense_label
        {"field": "1.D", "label": "สาขา", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 分店：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['branch']
        {"field": "1.E", "label": "แผนก", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 部门：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['dept']
        {"field": "1.F", "label": "งาน", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 工作：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['job']
        {"field": "1.G", "label": "พนักงาน", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 员工（写名称）：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['employee']
        {"field": "1.H", "label": "กำหนดส่งสินค้า", "auto": ["computed"], "note": "交货日 = 单据日期"},  # 交货日：同第 B 列日期
        {"field": "1.I", "label": "รหัสผู้จำหน่าย", "auto": ["computed", "book_setting", "source_code", "face"], "note": "记账用供应商：现购（express_rules.push.payment.payment_is_paid）= 准备时定的现金供应商（CODE_NOAP / เงินสด，_mrerp_cash_supplier）；赊购 = 同第 J 列真实卖家"},  # purchase.posting_supplier
        {"field": "1.J", "label": "รหัสผู้จำหน่าย (บิล)", "auto": ["source_code", "face", "computed", "constant"], "default": {"value": "เงินสด", "basis": "docs/work/2026-11-14-mrerp-write-grid.md"}, "note": "开票用供应商（进项税挂它）：映射表按卖方税号 / client_id → 卖方税号 → 名称推导 → V+client_id；卖方完全没身份时用现金供应商 เงินสด"},  # purchase._supplier_code
        {"field": "1.K", "label": "เลขที่บิล", "auto": ["face", "remembered", "computed"], "ask": "mrerp_menu", "note": "票面发票号原样；票面没有时 = 所选导入菜单的前缀（_mrerp_menu_prefix）+ 单号；超过 20 字预检拦下不截"},  # 税务发票号：fmt.mrerp_bill_no
        {"field": "1.L", "label": "วันที่", "auto": ["computed"], "note": "发票日 = 单据日期"},  # 发票日：同第 B 列日期
        {"field": "1.M", "label": "หักส่วนลด", "auto": ["computed"], "note": "货品同赊销、现销（fmt.allocate_discount）；费用档单行含税全额，写 0"},  # 表头折扣：generate_xlsx_purchase documents[...][0]
        {"field": "1.N", "label": "หมายเหตุ 1", "auto": ["face", "constant"], "note": "费用档写明细名（缺了卖方名，≤60 字）作人读线索；货品单留空"},  # purchase._expense_note
        {"field": "1.O", "label": "หมายเหตุ 2", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；generate_xlsx_purchase 只写到第 13 / 14 列
        {"field": "1.P", "label": "หมายเหตุ 3", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；generate_xlsx_purchase 只写到第 13 / 14 列
        {"field": "2.A", "label": "เลขที่", "auto": ["computed"]},  # 同表 1 单号：generator.derive_mrerp_invoice_no
        {"field": "2.B", "label": "รหัสสินค้า", "auto": ["source_code", "master", "book_setting", "constant"], "note": "物料码：映射表按行名对上 → 自建物料的码（autocreate.provision_products 生成前已建好）→ 通用物料码（_generic_product_code / 费用档 _mrerp_expense_item_code，默认 EXPENSE）；对不上留空让 MR.ERP 明确拒掉"},  # lookups._resolve_product_code / purchase._expense_item_code
        {"field": "2.C", "label": "แผนก", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 部门：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['dept']
        {"field": "2.D", "label": "งาน", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 工作：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['job']
        {"field": "2.E", "label": "คลัง", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 仓库：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['store']
        {"field": "2.F", "label": "จำนวน", "auto": ["face", "computed"], "note": "票面数量；缺了由金额 / 单价反推，再缺按 1"},  # 行数量：purchase._detail_rows / sales_credit.build_sales_credit_detail_rows
        {"field": "2.G", "label": "ราคา/หน่วย", "auto": ["face", "computed"], "note": "票面单价；缺了由金额 / 数量反推；费用档 = 含税总额"},  # 行单价：同上
        {"field": "2.H", "label": "จำนวนเงิน", "auto": ["face", "computed"], "note": "行金额读 amount / subtotal / total / line_total，折扣摊进行里；没有明细按小计（缺了含税额 ÷ 1.07）单行"},  # 行金额：fmt.line_amount + fmt.allocate_discount
        {"field": "3.A", "label": "เลขที่", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；押金 / 收货页只写表头
        {"field": "3.B", "label": "เลขที่เงินมัดจำ", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；押金 / 收货页只写表头
        {"field": "3.C", "label": "ออกใบรับสินค้า", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；押金 / 收货页只写表头
    ],
    "mrerp:SALES_CREDIT": [
        {"field": "1.A", "label": "เลขที่", "auto": ["computed"], "note": "单据日期（佛历 YYMMDD）+ 票据 id 算出的 6 位序号，同一张重推同号；MR.ERP 拼菜单前缀"},  # 单号：generator.derive_mrerp_invoice_no
        {"field": "1.B", "label": "วันที่", "auto": ["face"], "note": "单据日期同时决定写进哪本年度账套（mrerp.catalog.pick，账套由会话给定；同期两本出 account_set 题），账套本身不是导入表的一列"},  # 票面开票日：fmt.fmt_date(history['invoice_date'])
        {"field": "1.C", "label": "อัตราภาษี", "auto": ["computed", "book_setting"], "note": "fmt.mrerp_tax_kind 判价内 / 价外 / 零税率；名称按这家公司的税率表（_mrerp_vat_rates），读不到用初始库名"},  # 税率：fmt.mrerp_tax_label
        {"field": "1.D", "label": "สาขา", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 分店：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['branch']
        {"field": "1.E", "label": "แผนก", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 部门：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['dept']
        {"field": "1.F", "label": "งาน", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 工作：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['job']
        {"field": "1.G", "label": "พนักงานขาย", "auto": ["master", "remembered", "book_setting"], "ask": "mrerp_default", "note": "客户档里有员工的用客户档（_mrerp_customer_header，同 MR.ERP 网页选客户后带出），否则同表头默认；写名称"},  # 销售员：defaults.header(mappings, 客户码)['employee']
        {"field": "1.H", "label": "กำหนดส่งสินค้า", "auto": ["computed"], "note": "交货日 = 单据日期"},  # 交货日：同第 B 列日期
        {"field": "1.I", "label": "รหัสลูกค้า", "auto": ["source_code", "face", "constant"], "default": {"value": "เงินสด", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "关联客户的映射码 → 票面买方税号 → 完全没有买方时现金客户 เงินสด；有 client_id 缺映射时留空交自建 / 预检"},  # 客户码：lookups.resolve_customer_code
        {"field": "1.J", "label": "รหัสลูกค้า (บิล)", "auto": ["source_code", "face", "constant"], "note": "开票用客户 = 第 I 列同一个客户"},  # sales_credit.build_sales_credit_row['customer_bill']
        {"field": "1.K", "label": "เลขที่บิล", "auto": ["face", "remembered", "computed"], "ask": "mrerp_menu", "note": "票面发票号原样；票面没有时 = 所选导入菜单的前缀（_mrerp_menu_prefix）+ 单号；超过 20 字预检拦下不截"},  # 税务发票号：fmt.mrerp_bill_no
        {"field": "1.L", "label": "วันที่", "auto": ["computed"], "note": "发票日 = 单据日期"},  # 发票日：同第 B 列日期
        {"field": "1.M", "label": "พื้นที่การขาย", "auto": ["master", "remembered", "book_setting"], "ask": "mrerp_default", "note": "客户档里有销售区的用客户档，否则同表头默认；写名称"},  # defaults.header(mappings, 客户码)['area']
        {"field": "1.N", "label": "ประเภทขนส่ง", "auto": ["master", "remembered", "book_setting"], "ask": "mrerp_default", "note": "客户档里有运输的用客户档，否则同表头默认；写名称"},  # defaults.header(mappings, 客户码)['delivery']
        {"field": "1.O", "label": "หักส่วนลด", "auto": ["computed"], "note": "表头折扣：行折扣与整单折扣摊进明细后剩下的部分（对不上账时照票面写）"},  # fmt.allocate_discount 的第一项
        {"field": "1.P", "label": "หมายเหตุ 1", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；_generate_xlsx_sales_credit_korn_clone 写空 cell
        {"field": "1.Q", "label": "หมายเหตุ 2", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；同上
        {"field": "1.R", "label": "หมายเหตุ 3", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；同上
        {"field": "2.A", "label": "เลขที่", "auto": ["computed"]},  # 同表 1 单号：generator.derive_mrerp_invoice_no
        {"field": "2.B", "label": "รหัสสินค้า", "auto": ["source_code", "master", "book_setting", "constant"], "note": "物料码：映射表按行名对上 → 自建物料的码（autocreate.provision_products 生成前已建好）→ 通用物料码（_generic_product_code / 费用档 _mrerp_expense_item_code，默认 EXPENSE）；对不上留空让 MR.ERP 明确拒掉"},  # lookups._resolve_product_code / purchase._expense_item_code
        {"field": "2.C", "label": "แผนก", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 部门：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['dept']
        {"field": "2.D", "label": "งาน", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 工作：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['job']
        {"field": "2.E", "label": "คลัง", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 仓库：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['store']
        {"field": "2.F", "label": "จำนวน", "auto": ["face", "computed"], "note": "票面数量；缺了由金额 / 单价反推，再缺按 1"},  # 行数量：purchase._detail_rows / sales_credit.build_sales_credit_detail_rows
        {"field": "2.G", "label": "ราคา/หน่วย", "auto": ["face", "computed"], "note": "票面单价；缺了由金额 / 数量反推；费用档 = 含税总额"},  # 行单价：同上
        {"field": "2.H", "label": "จำนวนเงิน", "auto": ["face", "computed"], "note": "行金额读 amount / subtotal / total / line_total，折扣摊进行里；没有明细按小计（缺了含税额 ÷ 1.07）单行"},  # 行金额：fmt.line_amount + fmt.allocate_discount
        {"field": "3.A", "label": "เลขที่", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；尾页只写表头
        {"field": "3.B", "label": "เลขที่เงินมัดจำ", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；尾页只写表头
        {"field": "3.C", "label": "ออกใบขาย", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；尾页只写表头
    ],
    "mrerp:SALES_CASH": [
        {"field": "1.A", "label": "เลขที่", "auto": ["computed"], "note": "单据日期（佛历 YYMMDD）+ 票据 id 算出的 6 位序号，同一张重推同号；MR.ERP 拼菜单前缀"},  # 单号：generator.derive_mrerp_invoice_no
        {"field": "1.B", "label": "วันที่", "auto": ["face"], "note": "单据日期同时决定写进哪本年度账套（mrerp.catalog.pick，账套由会话给定；同期两本出 account_set 题），账套本身不是导入表的一列"},  # 票面开票日：fmt.fmt_date(history['invoice_date'])
        {"field": "1.C", "label": "อัตราภาษี", "auto": ["computed", "book_setting"], "note": "fmt.mrerp_tax_kind 判价内 / 价外 / 零税率；名称按这家公司的税率表（_mrerp_vat_rates），读不到用初始库名"},  # 税率：fmt.mrerp_tax_label
        {"field": "1.D", "label": "สาขา", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 分店：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['branch']
        {"field": "1.E", "label": "แผนก", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 部门：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['dept']
        {"field": "1.F", "label": "งาน", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 工作：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['job']
        {"field": "1.G", "label": "พนักงานขาย", "auto": ["master", "remembered", "book_setting"], "ask": "mrerp_default", "note": "客户档里有员工的用客户档（_mrerp_customer_header，同 MR.ERP 网页选客户后带出），否则同表头默认；写名称"},  # 销售员：defaults.header(mappings, 客户码)['employee']
        {"field": "1.H", "label": "กำหนดส่งสินค้า", "auto": ["computed"], "note": "交货日 = 单据日期"},  # 交货日：同第 B 列日期
        {"field": "1.I", "label": "รหัสลูกค้า", "auto": ["source_code", "face", "constant"], "default": {"value": "เงินสด", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "关联客户的映射码 → 票面买方税号 → 完全没有买方时现金客户 เงินสด；有 client_id 缺映射时留空交自建 / 预检"},  # 客户码：lookups.resolve_customer_code
        {"field": "1.J", "label": "รหัสลูกค้า (บิล)", "auto": ["source_code", "face", "constant"], "note": "开票用客户 = 第 I 列同一个客户"},  # sales_credit.build_sales_credit_row['customer_bill']
        {"field": "1.K", "label": "เลขที่บิล", "auto": ["face", "remembered", "computed"], "ask": "mrerp_menu", "note": "票面发票号原样；票面没有时 = 所选导入菜单的前缀（_mrerp_menu_prefix）+ 单号；超过 20 字预检拦下不截"},  # 税务发票号：fmt.mrerp_bill_no
        {"field": "1.L", "label": "วันที่", "auto": ["computed"], "note": "发票日 = 单据日期"},  # 发票日：同第 B 列日期
        {"field": "1.M", "label": "พื้นทีการขาย", "auto": ["master", "remembered", "book_setting"], "ask": "mrerp_default", "note": "客户档里有销售区的用客户档，否则同表头默认；写名称"},  # defaults.header(mappings, 客户码)['area']
        {"field": "1.N", "label": "ประเภทขนส่ง", "auto": ["master", "remembered", "book_setting"], "ask": "mrerp_default", "note": "客户档里有运输的用客户档，否则同表头默认；写名称"},  # defaults.header(mappings, 客户码)['delivery']
        {"field": "1.O", "label": "หักส่วนลด", "auto": ["computed"], "note": "表头折扣：行折扣与整单折扣摊进明细后剩下的部分（对不上账时照票面写）"},  # fmt.allocate_discount 的第一项
        {"field": "1.P", "label": "จำนวนเงินสดรับ", "auto": ["face", "computed"], "note": "现金付（sales_cash._is_cash）= 收讫总额（票面合计，缺了小计 + 税额）；转账 / 刷卡写 0"},  # sales_cash._cash_received
        {"field": "1.Q", "label": "รับอื่นๆ 1", "auto": ["book_setting", "remembered"], "ask": "mrerp_default", "note": "现金收款 = 参考科目 2「เงินสด」（_mrerp_receipt_account_cash）；转账 / 刷卡 = 这份账套选的银行科目（_mrerp_receipt_account_bank）"},  # sales_cash._receipt_account
        {"field": "1.R", "label": "จำนวนเงินอื่นๆ 1", "auto": ["face", "computed"], "note": "转账 / 刷卡 = 收讫总额；现金付写 0"},  # sales_cash._cash_received
        {"field": "1.S", "label": "รับอื่นๆ 2", "auto": ["book_setting", "remembered"], "ask": "mrerp_default", "note": "不用的槽沿用同一个科目（MR.ERP 要求填存在的科目码），金额 0 不产生分录"},  # sales_cash._receipt_account（同 Q 列）
        {"field": "1.T", "label": "จำนวนเงินอื่นๆ 2", "auto": ["constant"], "note": "写 0"},  # generate_xlsx_sales_cash col_vals[20]
        {"field": "1.U", "label": "รับอื่นๆ 3", "auto": ["book_setting", "remembered"], "ask": "mrerp_default", "note": "不用的槽沿用同一个科目（MR.ERP 要求填存在的科目码），金额 0 不产生分录"},  # sales_cash._receipt_account（同 Q 列）
        {"field": "1.V", "label": "จำนวนเงินอื่นๆ 3", "auto": ["constant"], "note": "写 0"},  # generate_xlsx_sales_cash col_vals[22]
        {"field": "1.W", "label": "หมายเหตุ 1", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；generate_xlsx_sales_cash 只写到第 22 列
        {"field": "1.X", "label": "หมายเหตุ 2", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；同上
        {"field": "1.Y", "label": "หมายเหตุ 3", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；同上
        {"field": "2.A", "label": "เลขที่", "auto": ["computed"]},  # 同表 1 单号：generator.derive_mrerp_invoice_no
        {"field": "2.B", "label": "รหัสสินค้า", "auto": ["source_code", "master", "book_setting", "constant"], "note": "物料码：映射表按行名对上 → 自建物料的码（autocreate.provision_products 生成前已建好）→ 通用物料码（_generic_product_code / 费用档 _mrerp_expense_item_code，默认 EXPENSE）；对不上留空让 MR.ERP 明确拒掉"},  # lookups._resolve_product_code / purchase._expense_item_code
        {"field": "2.C", "label": "แผนก", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 部门：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['dept']
        {"field": "2.D", "label": "งาน", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 工作：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['job']
        {"field": "2.E", "label": "คลัง", "auto": ["remembered", "book_setting"], "ask": "mrerp_default"},  # 仓库：这份账套选过的（_mrerp_defaults）→ 没选过按 MR.ERP 网页新建默认（总店 / 按代码第一条）；defaults.header()['store']
        {"field": "2.F", "label": "จำนวน", "auto": ["face", "computed"], "note": "票面数量；缺了由金额 / 单价反推，再缺按 1"},  # 行数量：purchase._detail_rows / sales_credit.build_sales_credit_detail_rows
        {"field": "2.G", "label": "ราคา/หน่วย", "auto": ["face", "computed"], "note": "票面单价；缺了由金额 / 数量反推；费用档 = 含税总额"},  # 行单价：同上
        {"field": "2.H", "label": "จำนวนเงิน", "auto": ["face", "computed"], "note": "行金额读 amount / subtotal / total / line_total，折扣摊进行里；没有明细按小计（缺了含税额 ÷ 1.07）单行"},  # 行金额：fmt.line_amount + fmt.allocate_discount
        {"field": "3.A", "label": "เลขที่", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；尾页只写表头
        {"field": "3.B", "label": "เลขที่เงินมัดจำ", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；尾页只写表头
        {"field": "3.C", "label": "ออกใบขาย", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；尾页只写表头
        {"field": "4.A", "label": "เลขที่", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；支票页只写表头
        {"field": "4.B", "label": "เลขที่เช็ค", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；支票页只写表头
        {"field": "4.C", "label": "เช็คลงวันที่", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；支票页只写表头
        {"field": "4.D", "label": "ธนาคาร", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；支票页只写表头
        {"field": "4.E", "label": "จำนวนเงิน", "auto": ["constant"], "note": "留空"},  # 只写表头、没有数据行 / 不写这一格；支票页只写表头
    ],
    "mrerp:APMAS": [
        {"field": "1.A", "label": "รหัสผู้จำหน่าย", "auto": ["face", "computed"], "note": "自建码：税号（13 位）→ 名称推导 / client_id；与单据上的对方码同源"},  # supplier code：autocreate 生成，supplier.build_supplier_row
        {"field": "1.B", "label": "ประเภทผู้จำหน่าย", "auto": ["remembered", "book_mode"], "ask": "mrerp_default", "note": "这份账套选过的 → 这家公司现有档案最常用的那组（没有明显多数才问）"},  # 类型：_mrerp_supplier_type（defaults.suppliers）
        {"field": "1.C", "label": "คำนำหน้า", "auto": ["computed", "master"], "note": "称谓：名称开头对这家公司的称谓表（masters.split_prefix），对不上用称谓表第一项"},  # supplier.build_supplier_row prefix
        {"field": "1.D", "label": "ชื่อผู้จำหน่าย", "auto": ["face"], "default": {"value": "-", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "票面名称去掉称谓（≤100 字），缺了写 -"},  # supplier.build_supplier_row name
        {"field": "1.E", "label": "ที่อยู่ 1", "auto": ["face", "constant"], "default": {"value": "-", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "票面地址前 100 字，缺了写 -"},  # supplier.build_supplier_row address[:100]
        {"field": "1.F", "label": "ที่อยู่ 2", "auto": ["face", "constant"], "default": {"value": "-", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "票面地址第 101–200 字，缺了写 -"},  # supplier.build_supplier_row address[100:200]
        {"field": "1.G", "label": "ที่อยู่ 3 ", "auto": ["constant"], "note": "固定写 -（MR.ERP 空值报 เป็นค่าว่าง）"},  # supplier.build_supplier_row _PLACEHOLDER
        {"field": "1.H", "label": "ที่อยู่ 4", "auto": ["constant"], "note": "固定写 -"},  # supplier.build_supplier_row _PLACEHOLDER
        {"field": "1.I", "label": "รหัสไปรษณีย์", "auto": ["face", "constant"], "default": {"value": "-", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "票面邮编，缺了写 -"},  # supplier.build_supplier_row postcode
        {"field": "1.J", "label": "เบอร์โทรศัพท์", "auto": ["face", "constant"], "default": {"value": "-", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "票面电话，缺了写 -"},  # supplier.build_supplier_row phone
        {"field": "1.K", "label": "อีเมล์", "auto": ["face", "constant"], "default": {"value": "noemail@pearnly.app", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "票面邮箱格式合法才用，否则写占位（MR.ERP 必填且查格式）"},  # supplier.build_supplier_row _email
        {"field": "1.L", "label": "ประเทศ", "auto": ["constant"], "note": "固定 TH"},  # _DEFAULTS['country']
        {"field": "1.M", "label": "รหัสบัญชี", "auto": ["book_setting", "remembered", "book_mode"], "ask": "mrerp_default", "note": "现购记账用的现金供应商带自己的科目（参考科目「เงินสด」）；其余 = 这份账套选过的应付科目 → 现有档案最常用的那组"},  # supplier.build_supplier_row：sup['account'] 或 _mrerp_supplier_account
        {"field": "1.N", "label": "คำนำหน้า", "auto": ["computed", "master"], "note": "联系人称谓 = 第 C 列"},  # supplier.build_supplier_row prefix
        {"field": "1.O", "label": "ชื่อผู้ติดต่อ", "auto": ["face"], "note": "联系人 = 名称前 50 字"},  # supplier.build_supplier_row name[:50]
        {"field": "1.P", "label": "เครดิตเทอม", "auto": ["constant"], "note": "固定写：信用期 0"},  # supplier.build_supplier_row 16–20
        {"field": "1.Q", "label": "ส่วนลด", "auto": ["constant"], "note": "固定写：折扣 0"},  # supplier.build_supplier_row 16–20
        {"field": "1.R", "label": "วงเงินเครดิต", "auto": ["constant"], "note": "固定写：信用额 0"},  # supplier.build_supplier_row 16–20
        {"field": "1.S", "label": "จัดอันดับผู้จำหน่าย", "auto": ["constant"], "note": "固定写：评级 0"},  # supplier.build_supplier_row 16–20
        {"field": "1.T", "label": "อัตราแลกเปลี่ยน", "auto": ["constant"], "note": "固定写：汇率 1"},  # supplier.build_supplier_row 16–20
        {"field": "1.U", "label": "เลขประจำตัวผู้เสียภาษี", "auto": ["face", "constant"], "default": {"value": "-", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "票面税号（数字，≤13 位），缺了写 -"},  # supplier.build_supplier_row tax
        {"field": "1.V", "label": "รหัสสาขาผู้จำหน่าย", "auto": ["face", "master", "constant"], "note": "票面分店号补成 5 位，在这家公司分店表里有才用，否则 00000 / 第一项"},  # defaults._branch_for
    ],
    "mrerp:ARMAS": [
        {"field": "1.A", "label": "รหัสลูกค้า", "auto": ["face", "computed"], "note": "自建码：税号（13 位）→ 名称推导 / client_id；与单据上的对方码同源"},  # customer code：autocreate 生成，customer.build_customer_row
        {"field": "1.B", "label": "ประเภทลูกค้า", "auto": ["remembered", "book_mode"], "ask": "mrerp_default", "note": "这份账套选过的 → 这家公司现有档案最常用的那组（没有明显多数才问）"},  # 类型：_mrerp_customer_type（defaults.customers）
        {"field": "1.C", "label": "คำนำหน้า", "auto": ["computed", "master"], "note": "称谓：名称开头对这家公司的称谓表（masters.split_prefix），对不上用称谓表第一项"},  # customer.build_customer_row prefix
        {"field": "1.D", "label": "ชื่อลูกค้า", "auto": ["face"], "default": {"value": "-", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "票面名称去掉称谓（≤100 字），缺了写 -"},  # customer.build_customer_row name
        {"field": "1.E", "label": "ที่อยู่ 1", "auto": ["face", "constant"], "default": {"value": "-", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "票面地址前 100 字，缺了写 -"},  # customer.build_customer_row address[:100]
        {"field": "1.F", "label": "ที่อยู่ 2", "auto": ["face", "constant"], "default": {"value": "-", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "票面地址第 101–200 字，缺了写 -"},  # customer.build_customer_row address[100:200]
        {"field": "1.G", "label": "ที่อยู่ 3 ", "auto": ["constant"], "note": "固定写 -（MR.ERP 空值报 เป็นค่าว่าง）"},  # customer.build_customer_row _PLACEHOLDER
        {"field": "1.H", "label": "ที่อยู่ 4", "auto": ["constant"], "note": "固定写 -"},  # customer.build_customer_row _PLACEHOLDER
        {"field": "1.I", "label": "รหัสไปรษณีย์", "auto": ["face", "constant"], "default": {"value": "-", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "票面邮编，缺了写 -"},  # customer.build_customer_row postcode
        {"field": "1.J", "label": "เบอร์โทรศัพท์", "auto": ["face", "constant"], "default": {"value": "-", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "票面电话，缺了写 -"},  # customer.build_customer_row phone
        {"field": "1.K", "label": "อีเมล์", "auto": ["face", "constant"], "default": {"value": "noemail@pearnly.app", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "票面邮箱格式合法才用，否则写占位（MR.ERP 必填且查格式）"},  # customer.build_customer_row _email
        {"field": "1.L", "label": "ประเทศ", "auto": ["constant"], "note": "固定 TH"},  # _DEFAULTS['country']
        {"field": "1.M", "label": "รหัสบัญชี", "auto": ["remembered", "book_mode"], "ask": "mrerp_default", "note": "这份账套选过的应收科目 → 现有档案最常用的那组"},  # _mrerp_customer_account（defaults.customers）
        {"field": "1.N", "label": "คำนำหน้า", "auto": ["computed", "master"], "note": "联系人称谓 = 第 C 列"},  # customer.build_customer_row prefix
        {"field": "1.O", "label": "ชื่อผู้ติดต่อ", "auto": ["face"], "note": "联系人 = 名称前 50 字"},  # customer.build_customer_row name[:50]
        {"field": "1.P", "label": "รหัสพนักงานขาย", "auto": ["remembered", "book_setting"], "ask": "mrerp_default", "note": "同表头员工（写代码）"},  # _mrerp_customer_salesman
        {"field": "1.Q", "label": "รหัสพื้นที่การขาย", "auto": ["remembered", "book_setting"], "ask": "mrerp_default", "note": "同表头销售区（写代码）"},  # _mrerp_customer_area
        {"field": "1.R", "label": "รหัสประเภทขนส่ง", "auto": ["remembered", "book_setting"], "ask": "mrerp_default", "note": "同表头运输（写代码）"},  # _mrerp_customer_shipping
        {"field": "1.S", "label": "เครดิตเทอม", "auto": ["constant"], "note": "固定写：信用期 0"},  # customer.build_customer_row 19–23
        {"field": "1.T", "label": "ส่วนลด", "auto": ["constant"], "note": "固定写：折扣 0"},  # customer.build_customer_row 19–23
        {"field": "1.U", "label": "วงเงินเครดิต", "auto": ["constant"], "note": "固定写：信用额 0"},  # customer.build_customer_row 19–23
        {"field": "1.V", "label": "จัดอันดับลูกค้า", "auto": ["constant"], "note": "固定写：评级 0"},  # customer.build_customer_row 19–23
        {"field": "1.W", "label": "อัตราแลกเปลี่ยน", "auto": ["constant"], "note": "固定写：汇率 1"},  # customer.build_customer_row 19–23
        {"field": "1.X", "label": "เลขประจำตัวผู้เสียภาษี", "auto": ["face", "constant"], "default": {"value": "-", "basis": "docs/work/2026-10-08-mrerp-master-defaults.md"}, "note": "票面税号（数字，≤13 位），缺了写 -"},  # customer.build_customer_row tax
        {"field": "1.Y", "label": "รหัสสาขาลูกค้า", "auto": ["face", "master", "constant"], "note": "票面分店号补成 5 位，在这家公司分店表里有才用，否则 00000 / 第一项"},  # defaults._branch_for
    ],
    "mrerp:STKMAS": [
        {"field": "1.A", "label": "ประเภทสินค้า", "auto": ["constant"], "note": "货品固定「จัดทำสต๊อค」（库存型）；费用物料由 autocreate 换成费用类型"},  # product._DEFAULTS['type'] / autocreate._EXPENSE_ITEM_TYPE
        {"field": "1.B", "label": "รหัสหมวดสินค้า", "auto": ["remembered", "book_mode"], "ask": "mrerp_default", "note": "这份账套选过的分类 → 现有物料最常用的"},  # _mrerp_product_category
        {"field": "1.C", "label": "รหัสสินค้า", "auto": ["computed", "constant"], "note": "货品按行名推导（autocreate._product_code_from_name）；费用物料 EXPENSE（可覆盖）"},  # product.build_product_row code
        {"field": "1.D", "label": "ชื่อสินค้า", "auto": ["face", "constant"], "note": "票面行名（≤50 字），缺了写 -；费用物料 ค่าใช้จ่าย"},  # product.build_product_row name
        {"field": "1.E", "label": "รหัสสินค้าเพื่อซื้อ", "auto": ["computed"], "note": "采购码 = 第 C 列"},  # product.build_product_row
        {"field": "1.F", "label": "ชื่อสินค้าเพื่อซื้อ", "auto": ["computed"], "note": "采购名 = 第 D 列"},  # product.build_product_row
        {"field": "1.G", "label": "รหัสสินค้าเพื่อขาย", "auto": ["computed"], "note": "销售码 = 第 C 列"},  # product.build_product_row
        {"field": "1.H", "label": "ชื่อสินค้าเพื่อขาย", "auto": ["computed"], "note": "销售名 = 第 D 列"},  # product.build_product_row
        {"field": "1.I", "label": "ราคาขาย", "auto": ["face", "constant"], "note": "票面单价，缺了 0；费用物料 0"},  # product.build_product_row price
        {"field": "1.J", "label": "วิธีคำนวณต้นทุนสินค้า", "auto": ["constant"], "note": "固定 Average（MR.ERP 系统表，各公司相同）"},  # product._DEFAULTS['cost_method']
        {"field": "1.K", "label": "รหัสจำนวนนับ", "auto": ["remembered", "book_mode"], "ask": "mrerp_default", "note": "计数单位：这份账套选过的单位 → 现有物料最常用的；四个单位同一个"},  # _mrerp_product_unit_*
        {"field": "1.L", "label": "รหัสหน่วยนับมาตรฐาน", "auto": ["remembered", "book_mode"], "ask": "mrerp_default", "note": "标准单位：这份账套选过的单位 → 现有物料最常用的；四个单位同一个"},  # _mrerp_product_unit_*
        {"field": "1.M", "label": "หน่วยนับซื้อ", "auto": ["remembered", "book_mode"], "ask": "mrerp_default", "note": "采购单位：这份账套选过的单位 → 现有物料最常用的；四个单位同一个"},  # _mrerp_product_unit_*
        {"field": "1.N", "label": "จำนวนต่อหน่วยนับซื้อ", "auto": ["constant"], "note": "固定 1"},  # product.build_product_row
        {"field": "1.O", "label": "หน่วยนับขาย", "auto": ["remembered", "book_mode"], "ask": "mrerp_default", "note": "销售单位：这份账套选过的单位 → 现有物料最常用的；四个单位同一个"},  # _mrerp_product_unit_*
        {"field": "1.P", "label": "จำนวนต่อหน่วยนับขาย", "auto": ["constant"], "note": "固定 1"},  # product.build_product_row
        {"field": "1.Q", "label": "ต้นทุนมาตรฐาน", "auto": ["constant"], "note": "标准成本固定 0"},  # product.build_product_row
        {"field": "1.R", "label": "อายุ-วัน", "auto": ["constant"], "note": "寿命固定 365 天"},  # product.build_product_row
        {"field": "1.S", "label": "จำนวนสินค้าต่ำสุด", "auto": ["constant"], "note": "最小库存固定 0"},  # product.build_product_row
        {"field": "1.T", "label": "จำนวนสินค้าสูงสุด", "auto": ["constant"], "note": "最大库存固定 0"},  # product.build_product_row
        {"field": "1.U", "label": "รหัสบัญชีรายได้-ขายเชื่อ", "auto": ["book_setting", "remembered"], "ask": "mrerp_default", "note": "收入-赊销：货品取参考科目；费用物料取这份账套选的费用科目（expense_account）"},  # defaults.products → _mrerp_product_acc_*
        {"field": "1.V", "label": "รหัสบัญชีรายได้-รับคืน", "auto": ["book_setting", "remembered"], "ask": "mrerp_default", "note": "收入-退回：货品取参考科目；费用物料取这份账套选的费用科目（expense_account）"},  # defaults.products → _mrerp_product_acc_*
        {"field": "1.W", "label": "รหัสบัญชีรายได้-ส่วนลดจ่าย", "auto": ["book_setting", "remembered"], "ask": "mrerp_default", "note": "收入-折扣：货品取参考科目；费用物料取这份账套选的费用科目（expense_account）"},  # defaults.products → _mrerp_product_acc_*
        {"field": "1.X", "label": "รหัสบัญชีซื้อสินค้า", "auto": ["book_setting", "remembered"], "ask": "mrerp_default", "note": "采购：货品取参考科目；费用物料取这份账套选的费用科目（expense_account）"},  # defaults.products → _mrerp_product_acc_*
        {"field": "1.Y", "label": "รหัสบัญชีซื้อ-ส่งคืน", "auto": ["book_setting", "remembered"], "ask": "mrerp_default", "note": "采购-退回：货品取参考科目；费用物料取这份账套选的费用科目（expense_account）"},  # defaults.products → _mrerp_product_acc_*
        {"field": "1.Z", "label": "รหัสบัญชีซื้อ-ส่วนลดรับ", "auto": ["book_setting", "remembered"], "ask": "mrerp_default", "note": "采购-折扣：货品取参考科目；费用物料取这份账套选的费用科目（expense_account）"},  # defaults.products → _mrerp_product_acc_*
        {"field": "1.AA", "label": "รหัสบัญชีสินค้า", "auto": ["book_setting", "remembered"], "ask": "mrerp_default", "note": "存货：货品取参考科目；费用物料取这份账套选的费用科目（expense_account）"},  # defaults.products → _mrerp_product_acc_*
        {"field": "1.AB", "label": "รหัสบัญชีต้นทุนขาย", "auto": ["book_setting", "remembered"], "ask": "mrerp_default", "note": "销售成本：货品取参考科目；费用物料取这份账套选的费用科目（expense_account）"},  # defaults.products → _mrerp_product_acc_*
        {"field": "1.AC", "label": "รหัสผู้จำหน่าย", "auto": ["master"], "note": "默认供应商：这家公司有 เงินสด 用它，否则按代码第一个供应商（MR.ERP 必填且要存在）"},  # defaults._product_supplier
    ],
}

# 导入表生成器 → 它写哪几个键（路径相对仓库根）。新增生成器要在这里登记，否则它写的列没人管。
WRITERS = {
    "apps/pearnly_core/pearnly_core/mrerp/push/xlsx/purchase.py": ["mrerp:PURCHASE"],
    "apps/pearnly_core/pearnly_core/mrerp/push/xlsx/sales_credit.py": ["mrerp:SALES_CREDIT"],
    "apps/pearnly_core/pearnly_core/mrerp/push/xlsx/sales_cash.py": ["mrerp:SALES_CASH"],
    "apps/pearnly_core/pearnly_core/mrerp/push/xlsx/supplier.py": ["mrerp:APMAS"],
    "apps/pearnly_core/pearnly_core/mrerp/push/xlsx/customer.py": ["mrerp:ARMAS"],
    "apps/pearnly_core/pearnly_core/mrerp/push/xlsx/product.py": ["mrerp:STKMAS"],
}

# CC 桌面版 MR.ERP 代录草稿的键 → 落到哪个键的哪一列 [键, 列]（网页表单的格子与导入表同源，`operator_draft.block`）
OPERATOR_KEYS = {
    "docnum": [["mrerp:PURCHASE", "1.A"], ["mrerp:SALES_CREDIT", "1.A"], ["mrerp:SALES_CASH", "1.A"]],
    "date": [["mrerp:PURCHASE", "1.B"], ["mrerp:SALES_CREDIT", "1.B"], ["mrerp:SALES_CASH", "1.B"]],
    # 采购的现金勾 = 现购（记账用供应商换成现金供应商，导入表第 9 列）；现销的现金勾由配方的单种给（form.cash），收款落第 16 列
    "cash": [["mrerp:PURCHASE", "1.I"], ["mrerp:SALES_CASH", "1.P"]],
    "vat_rate": [["mrerp:PURCHASE", "1.C"], ["mrerp:SALES_CREDIT", "1.C"], ["mrerp:SALES_CASH", "1.C"]],
    "party_code": [["mrerp:PURCHASE", "1.I"], ["mrerp:SALES_CREDIT", "1.I"], ["mrerp:SALES_CASH", "1.I"]],
    "invoice_party_code": [["mrerp:PURCHASE", "1.J"]],  # 开票用供应商 txtapmas_inv（销售的开票客户页面随客户带出）
    "ref_no": [["mrerp:PURCHASE", "1.K"], ["mrerp:SALES_CREDIT", "1.K"], ["mrerp:SALES_CASH", "1.K"]],
    "ref_date": [["mrerp:PURCHASE", "1.L"], ["mrerp:SALES_CREDIT", "1.L"], ["mrerp:SALES_CASH", "1.L"]],
    "delivery_date": [["mrerp:PURCHASE", "1.H"], ["mrerp:SALES_CREDIT", "1.H"], ["mrerp:SALES_CASH", "1.H"]],
}
# 代录草稿里落不到导入表列的键：写明原因
NOT_COLUMNS = {
    # 赊期 txttopcode：导入表的单据表头没有这一列（MR.ERP 导入按对方档带出）；block() 给空串，页面选了对方后自己带出
    "credit_days": "no import column: the credit term comes from the party record (block() sends an empty value)",
}


def keys() -> List[str]:
    return list(REQUIRED_FIELDS)


def rows(key: str) -> List[Dict]:
    return list(REQUIRED_FIELDS.get(key) or ())


def names(key: str) -> frozenset:
    return frozenset(row["field"] for row in rows(key))


def row(key: str, field: str) -> Optional[Dict]:
    return next((r for r in rows(key) if r["field"] == field), None)


def problems(kinds) -> list:
    """登记表自身不合规的地方（空 = 合规）。``kinds``：引导卡题类全集（``write_precheck.contracts.KINDS``）。"""
    out = []
    for key, fields in REQUIRED_FIELDS.items():
        if not key.startswith("mrerp:"):
            out.append(f"{key}: key must be mrerp:<document>")
        seen = set()
        for r in fields:
            where = f"{key}.{r.get('field')}"
            if r.get("field") in seen:
                out.append(f"{where}: registered twice")
            seen.add(r.get("field"))
            if not str(r.get("label") or "").strip():
                out.append(f"{where}: no header label")
            if not (r.get("auto") or r.get("ask") or r.get("review")):
                out.append(f"{where}: none of auto / ask / review")
            bad = [s for s in r.get("auto") or () if s not in AUTO_SOURCES]
            if bad:
                out.append(f"{where}: unknown sources {bad}")
            if "ask" in r and r["ask"] not in kinds:
                out.append(f"{where}: question id {r['ask']!r} is not a guide-card kind")
            if r.get("default") is not None and not str(r["default"].get("basis") or "").startswith("docs/"):
                out.append(f"{where}: default without a basis document")
    for path, targets in WRITERS.items():
        for key in targets:
            if key not in REQUIRED_FIELDS:
                out.append(f"WRITERS.{path}: key {key} not registered")
    for name, targets in OPERATOR_KEYS.items():
        if name in NOT_COLUMNS:
            out.append(f"OPERATOR_KEYS.{name}: also listed in NOT_COLUMNS")
        for key, field in targets:
            if row(key, field) is None:
                out.append(f"OPERATOR_KEYS.{name}: target {key}.{field} not registered")
    return out


__all__ = ["AUTO_SOURCES", "NOT_COLUMNS", "OPERATOR_KEYS", "REQUIRED_FIELDS", "WRITERS", "keys", "names", "problems", "row",
           "rows"]
