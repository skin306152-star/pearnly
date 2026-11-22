"""Express 必填字段登记表（多产品共用，纯契约；CC 引导式录入第 1 期，覆盖表 ``docs/work/2026-11-12-cc-guided-entry-express-coverage.md`` 第 6 节）。

原则（用户 11-12）：写一张单、建一条主档时 Express 要有值的每个字段，只能是三种之一——**后台自动定**（有依据）、**引导卡问**、
**复核卡可改**。「缺了就静默写个默认值」「缺了直接失败让人看不懂」都算缺口。

**结构是约定**（验收体系 ``docs/development/acceptance-testing.md`` §6，主会话 11-12 转定）：顶层常量 :data:`REQUIRED_FIELDS` 是一个**字典字面量**，
键 ``"<账本>:<单据>"``（``express:RR`` …；以后 ``mrerp:purchase``），值是字段行列表。验收的检查按语法树读它、不导入，所以：
键与每行的 ``ask`` 必须写成字符串字面量；``ask`` 是题目代号（稳定 id，与 ``write_precheck.contracts.KINDS`` 一致，不是文案），
**改名要先告诉主会话转验收窗口**；不出题的字段不写 ``ask``。验收登记只引用键（``fields = "express:RR"``），不抄字段。

每个字段行：

- ``field``：Express 字段名（DBF 列名）；
- ``auto``：自动定的来源，按先后顺序（取值见 :data:`AUTO_SOURCES`）；
- ``ask``：定不了时引导卡出哪一类题；
- ``review``：复核卡的列名与能不能就地改。**第 1 期只登记列名，「列在复核契约里存在、可改列能触发重判」的断言待第 2 期**（复核契约在 ``pearnly/cc-review-card``）；
- ``default``：允许缺了就写的默认值与依据文档（``docs/`` 开头）；
- ``gap`` / ``note``：覆盖表缺口编号与说明。

:data:`WRITERS` 登记 CC 桌面版**哪个写入模块写哪几种单据**；测试从这些模块取出实际写的全部字段，没登记就红。载荷 21 的新键逐个登记到它落的字段
（:data:`PAYLOAD_KEYS`）。:data:`DEFAULT_KEYS` 是写入与映射代码里允许「缺了就填一个值」的载荷键（其余由 ``scripts/check-express-defaults.py`` 按基线拦）。
:data:`MODE_GAPS` 如实登记**某种录入方式下还没覆盖**的字段（例：代录 × 新物料），不许把它们当成已覆盖。

收款单 RE 随第 3 期（收款接进引导）登记；库存单、调拨、退货、POS 日汇总不经引导卡，不在本表。

不依赖 Frappe、不读库、不做 IO。
"""
from __future__ import annotations

from typing import Dict, List, Optional

# ── 自动定的来源（按含义，不按产品）──
AUTO_SOURCES = frozenset({
    "constant",       # Express 原生记法里这一栏的固定值（单别、记账类型、状态位）
    "computed",       # 由同一张单的字段算出（金额合计、余额、税额、行号）
    "sequence",       # 单据簿 / 主档的下一个号
    "face",           # 票面（识别结果或来源单）
    "session",        # 本对话账套（会话给定，后面任何一步不重判）
    "master",         # Express 主档读回（对方档、物料档、账套设置）
    "remembered",     # 记住的选择（账套 / 公司 / 租户级）
    "source_code",    # 来源单带的 Express 编码（ERP 物料 / 供应商上记的码）
    "book_setting",   # 账套设置（ISINFO：现金科目、主仓、默认科目）
    "book_mode",      # 这本账套里最常用的值（众数）——只作预选 / 收起的已定项，引导卡上可改
    "history",        # Express 历史一贯（同一对方近期同口径 / 同科目）
    "machine",        # 机器标记 / 写入人 / 时间戳
    "answer",         # 引导卡上的答案（经载荷键带到 CC 桌面版）
})

REQUIRED_FIELDS = {
    # 赊购（APTRN / ISVAT / GLJNL / GLJNLIT / APBAL）
    "express:RR": [
        {"field": "DOCNUM", "auto": ["sequence"], "review": {"column": "docnum", "editable": False}},  # 单别前缀 + 佛历日期 + 当日序号（销售用票面号，见 IV / HS）
        {"field": "DOCDAT", "auto": ["face"], "review": {"column": "date", "editable": True}, "gap": "G-R1"},
        {"field": "VATDAT", "auto": ["face"], "review": {"column": "date", "editable": True}, "gap": "G-R1"},  # 同单据日
        {"field": "VATPRD", "auto": ["face"], "default": {"value": "document_month", "basis": "docs/work/2026-11-12-cc-guided-entry-express-coverage.md#section-1"}},  # 与 Express 手录一致：税期默认单据月
        {"field": "VOUCHER", "auto": ["computed"]},  # 分录凭证号 = 单号
        {"field": "VOUDAT", "auto": ["computed"]},  # 分录日期 = 单据日
        {"field": "DESCRP", "auto": ["computed", "face"]},  # 分录摘要：对方名 + 票号
        {"field": "REFNUM", "auto": ["face"], "review": {"column": "ref", "editable": True}, "gap": "G-R1"},  # 采购：供应商发票号，缺了不拒收（D-5，CC 桌面版那一批）；销售：票面号
        {"field": "DEPCOD", "auto": ["remembered", "book_setting"], "default": {"value": "book_default", "basis": "docs/work/2026-11-12-cc-guided-entry-express-coverage.md#section-9.2"}},  # 部门不问不显示（D-1）；原生 76–94% 由单据簿默认带
        {"field": "FLGVAT", "auto": ["computed"]},  # 含税 / 不含税 / 无税：按单别与有无税额（C/dbf_fields.py）
        {"field": "VATRAT", "auto": ["constant"]},  # 法定 7%
        {"field": "VATREC", "auto": ["constant"]},  # 进项 P / 销项 S
        {"field": "VATCOD", "auto": ["computed"]},  # 行税码：免税行 0，其余抄物料
        {"field": "AMOUNT", "auto": ["face"], "review": {"column": "amount", "editable": True}, "gap": "G-R1"},  # 税前额
        {"field": "AFTDISC", "auto": ["computed"]},  # 折扣摊进行金额后的税前额
        {"field": "TOTAL", "auto": ["computed"]},
        {"field": "NETVAL", "auto": ["computed"]},
        {"field": "NETAMT", "auto": ["computed"]},
        {"field": "VATAMT", "auto": ["face"], "review": {"column": "vat", "editable": True}, "gap": "G-R1"},
        {"field": "VAT01", "auto": ["computed"]},
        {"field": "AMT01", "auto": ["computed"]},
        {"field": "AMTRAT0", "auto": ["computed"]},  # 免税额
        {"field": "REMAMT", "auto": ["computed"]},  # 赊的未付 / 未收 = 合计，现的 = 0
        {"field": "PAYTRM", "auto": ["master", "answer"], "review": {"column": "due_date", "editable": True}, "gap": "G-R2"},  # 到期日（载荷 21 ``due_date_be``）；没给时对方档账期；复核卡第 2 期露出
        {"field": "DUEDAT", "auto": ["master", "answer"], "review": {"column": "due_date", "editable": True}, "gap": "G-R2"},  # 赊的 = 单据日 + 账期 / 载荷 ``due_date_be``；现的 = 单据日
        {"field": "ORGNUM", "auto": ["face", "master"]},  # 对方分店号
        {"field": "TAXID", "auto": ["face", "master"], "review": {"column": "tax_id", "editable": True}},
        {"field": "PRENAM", "auto": ["face", "master"]},
        {"field": "ACCNUM", "auto": ["master", "remembered", "history"], "ask": "account", "review": {"column": "account", "editable": True}},  # 分录科目：费用 / 收入科目走科目题；应付 / 应收取对方档；现金科目见 cash_account
        {"field": "TRNTYP", "auto": ["constant"]},  # 借 0 / 贷 1
        {"field": "JNLTYP", "auto": ["constant"]},  # 采购 04 / 销售 03
        {"field": "SEQIT", "auto": ["computed"]},  # 分录行序
        {"field": "NXTSEQ", "auto": ["computed"]},
        {"field": "RECTYP", "auto": ["constant"]},  # 单别码
        {"field": "SRV_VATTYP", "auto": ["history", "remembered", "answer"], "ask": "service_vat", "gap": "G-A3"},  # 服务品税口径：按账套历史定，定不了出题、按账套 × 对方记住；销项以前写死 1（主会话 11-12 并入第 1 期）
        {"field": "TRNSTAT", "auto": ["constant"]},
        {"field": "DOCSTAT", "auto": ["constant"]},
        {"field": "BILNUM", "auto": ["constant"]},
        {"field": "CMPLAPP", "auto": ["constant"]},
        {"field": "CMPLDAT", "auto": ["computed"]},  # 完成日 = 单据日
        {"field": "CREBY", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "USERID", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "SUPCOD", "auto": ["source_code", "remembered", "master"], "ask": "party", "review": {"column": "party", "editable": True}},  # ERP 记的编号 → 记住的 → 同税号唯一档；新建见 APMAS
        {"field": "PAYAMT", "auto": ["computed", "answer"], "ask": "payment", "review": {"column": "payment", "editable": True}, "gap": "G-A1"},  # 现购 HP / 赊购 RR：ERP「已付」→ 单张人定 → 记住 → 票面 → 漏斗，判不出出题
        {"field": "CSHPAY", "auto": ["computed", "answer"], "ask": "cash_account", "gap": "G-A2"},  # 现购贷现金科目（载荷 21 ``cash_account``）；现金科目不能记账时出题，不再静默改记应付
        {"field": "PVATPRORAT", "auto": ["constant"]},
    ],
    # 现购（APTRN / ISVAT / GLJNL / GLJNLIT）
    "express:HP": [
        {"field": "DOCNUM", "auto": ["sequence"], "review": {"column": "docnum", "editable": False}},  # 单别前缀 + 佛历日期 + 当日序号（销售用票面号，见 IV / HS）
        {"field": "DOCDAT", "auto": ["face"], "review": {"column": "date", "editable": True}, "gap": "G-R1"},
        {"field": "VATDAT", "auto": ["face"], "review": {"column": "date", "editable": True}, "gap": "G-R1"},  # 同单据日
        {"field": "VATPRD", "auto": ["face"], "default": {"value": "document_month", "basis": "docs/work/2026-11-12-cc-guided-entry-express-coverage.md#section-1"}},  # 与 Express 手录一致：税期默认单据月
        {"field": "VOUCHER", "auto": ["computed"]},  # 分录凭证号 = 单号
        {"field": "VOUDAT", "auto": ["computed"]},  # 分录日期 = 单据日
        {"field": "DESCRP", "auto": ["computed", "face"]},  # 分录摘要：对方名 + 票号
        {"field": "REFNUM", "auto": ["face"], "review": {"column": "ref", "editable": True}, "gap": "G-R1"},  # 采购：供应商发票号，缺了不拒收（D-5，CC 桌面版那一批）；销售：票面号
        {"field": "DEPCOD", "auto": ["remembered", "book_setting"], "default": {"value": "book_default", "basis": "docs/work/2026-11-12-cc-guided-entry-express-coverage.md#section-9.2"}},  # 部门不问不显示（D-1）；原生 76–94% 由单据簿默认带
        {"field": "FLGVAT", "auto": ["computed"]},  # 含税 / 不含税 / 无税：按单别与有无税额（C/dbf_fields.py）
        {"field": "VATRAT", "auto": ["constant"]},  # 法定 7%
        {"field": "VATREC", "auto": ["constant"]},  # 进项 P / 销项 S
        {"field": "VATCOD", "auto": ["computed"]},  # 行税码：免税行 0，其余抄物料
        {"field": "AMOUNT", "auto": ["face"], "review": {"column": "amount", "editable": True}, "gap": "G-R1"},  # 税前额
        {"field": "AFTDISC", "auto": ["computed"]},  # 折扣摊进行金额后的税前额
        {"field": "TOTAL", "auto": ["computed"]},
        {"field": "NETVAL", "auto": ["computed"]},
        {"field": "NETAMT", "auto": ["computed"]},
        {"field": "VATAMT", "auto": ["face"], "review": {"column": "vat", "editable": True}, "gap": "G-R1"},
        {"field": "VAT01", "auto": ["computed"]},
        {"field": "AMT01", "auto": ["computed"]},
        {"field": "AMTRAT0", "auto": ["computed"]},  # 免税额
        {"field": "REMAMT", "auto": ["computed"]},  # 赊的未付 / 未收 = 合计，现的 = 0
        {"field": "PAYTRM", "auto": ["master", "answer"], "review": {"column": "due_date", "editable": True}, "gap": "G-R2"},  # 到期日（载荷 21 ``due_date_be``）；没给时对方档账期；复核卡第 2 期露出
        {"field": "DUEDAT", "auto": ["master", "answer"], "review": {"column": "due_date", "editable": True}, "gap": "G-R2"},  # 赊的 = 单据日 + 账期 / 载荷 ``due_date_be``；现的 = 单据日
        {"field": "ORGNUM", "auto": ["face", "master"]},  # 对方分店号
        {"field": "TAXID", "auto": ["face", "master"], "review": {"column": "tax_id", "editable": True}},
        {"field": "PRENAM", "auto": ["face", "master"]},
        {"field": "ACCNUM", "auto": ["master", "remembered", "history"], "ask": "account", "review": {"column": "account", "editable": True}},  # 分录科目：费用 / 收入科目走科目题；应付 / 应收取对方档；现金科目见 cash_account
        {"field": "TRNTYP", "auto": ["constant"]},  # 借 0 / 贷 1
        {"field": "JNLTYP", "auto": ["constant"]},  # 采购 04 / 销售 03
        {"field": "SEQIT", "auto": ["computed"]},  # 分录行序
        {"field": "NXTSEQ", "auto": ["computed"]},
        {"field": "RECTYP", "auto": ["constant"]},  # 单别码
        {"field": "SRV_VATTYP", "auto": ["history", "remembered", "answer"], "ask": "service_vat", "gap": "G-A3"},  # 服务品税口径：按账套历史定，定不了出题、按账套 × 对方记住；销项以前写死 1（主会话 11-12 并入第 1 期）
        {"field": "TRNSTAT", "auto": ["constant"]},
        {"field": "DOCSTAT", "auto": ["constant"]},
        {"field": "BILNUM", "auto": ["constant"]},
        {"field": "CMPLAPP", "auto": ["constant"]},
        {"field": "CMPLDAT", "auto": ["computed"]},  # 完成日 = 单据日
        {"field": "CREBY", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "USERID", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "SUPCOD", "auto": ["source_code", "remembered", "master"], "ask": "party", "review": {"column": "party", "editable": True}},  # ERP 记的编号 → 记住的 → 同税号唯一档；新建见 APMAS
        {"field": "PAYAMT", "auto": ["computed", "answer"], "ask": "payment", "review": {"column": "payment", "editable": True}, "gap": "G-A1"},  # 现购 HP / 赊购 RR：ERP「已付」→ 单张人定 → 记住 → 票面 → 漏斗，判不出出题
        {"field": "CSHPAY", "auto": ["computed", "answer"], "ask": "cash_account", "gap": "G-A2"},  # 现购贷现金科目（载荷 21 ``cash_account``）；现金科目不能记账时出题，不再静默改记应付
        {"field": "PVATPRORAT", "auto": ["constant"]},
    ],
    # 赊销（ARTRN / ISVAT / GLJNL / GLJNLIT / ARBAL）
    "express:IV": [
        {"field": "DOCNUM", "auto": ["sequence"], "review": {"column": "docnum", "editable": False}},  # 单别前缀 + 佛历日期 + 当日序号（销售用票面号，见 IV / HS）
        {"field": "DOCDAT", "auto": ["face"], "review": {"column": "date", "editable": True}, "gap": "G-R1"},
        {"field": "VATDAT", "auto": ["face"], "review": {"column": "date", "editable": True}, "gap": "G-R1"},  # 同单据日
        {"field": "VATPRD", "auto": ["face"], "default": {"value": "document_month", "basis": "docs/work/2026-11-12-cc-guided-entry-express-coverage.md#section-1"}},  # 与 Express 手录一致：税期默认单据月
        {"field": "VOUCHER", "auto": ["computed"]},  # 分录凭证号 = 单号
        {"field": "VOUDAT", "auto": ["computed"]},  # 分录日期 = 单据日
        {"field": "DESCRP", "auto": ["computed", "face"]},  # 分录摘要：对方名 + 票号
        {"field": "REFNUM", "auto": ["face"], "review": {"column": "ref", "editable": True}, "gap": "G-R1"},  # 采购：供应商发票号，缺了不拒收（D-5，CC 桌面版那一批）；销售：票面号
        {"field": "DEPCOD", "auto": ["remembered", "book_setting"], "default": {"value": "book_default", "basis": "docs/work/2026-11-12-cc-guided-entry-express-coverage.md#section-9.2"}},  # 部门不问不显示（D-1）；原生 76–94% 由单据簿默认带
        {"field": "FLGVAT", "auto": ["computed"]},  # 含税 / 不含税 / 无税：按单别与有无税额（C/dbf_fields.py）
        {"field": "VATRAT", "auto": ["constant"]},  # 法定 7%
        {"field": "VATREC", "auto": ["constant"]},  # 进项 P / 销项 S
        {"field": "VATCOD", "auto": ["computed"]},  # 行税码：免税行 0，其余抄物料
        {"field": "AMOUNT", "auto": ["face"], "review": {"column": "amount", "editable": True}, "gap": "G-R1"},  # 税前额
        {"field": "AFTDISC", "auto": ["computed"]},  # 折扣摊进行金额后的税前额
        {"field": "TOTAL", "auto": ["computed"]},
        {"field": "NETVAL", "auto": ["computed"]},
        {"field": "NETAMT", "auto": ["computed"]},
        {"field": "VATAMT", "auto": ["face"], "review": {"column": "vat", "editable": True}, "gap": "G-R1"},
        {"field": "VAT01", "auto": ["computed"]},
        {"field": "AMT01", "auto": ["computed"]},
        {"field": "AMTRAT0", "auto": ["computed"]},  # 免税额
        {"field": "REMAMT", "auto": ["computed"]},  # 赊的未付 / 未收 = 合计，现的 = 0
        {"field": "PAYTRM", "auto": ["master", "answer"], "review": {"column": "due_date", "editable": True}, "gap": "G-R2"},  # 到期日（载荷 21 ``due_date_be``）；没给时对方档账期；复核卡第 2 期露出
        {"field": "DUEDAT", "auto": ["master", "answer"], "review": {"column": "due_date", "editable": True}, "gap": "G-R2"},  # 赊的 = 单据日 + 账期 / 载荷 ``due_date_be``；现的 = 单据日
        {"field": "ORGNUM", "auto": ["face", "master"]},  # 对方分店号
        {"field": "TAXID", "auto": ["face", "master"], "review": {"column": "tax_id", "editable": True}},
        {"field": "PRENAM", "auto": ["face", "master"]},
        {"field": "ACCNUM", "auto": ["master", "remembered", "history"], "ask": "account", "review": {"column": "account", "editable": True}},  # 分录科目：费用 / 收入科目走科目题；应付 / 应收取对方档；现金科目见 cash_account
        {"field": "TRNTYP", "auto": ["constant"]},  # 借 0 / 贷 1
        {"field": "JNLTYP", "auto": ["constant"]},  # 采购 04 / 销售 03
        {"field": "SEQIT", "auto": ["computed"]},  # 分录行序
        {"field": "NXTSEQ", "auto": ["computed"]},
        {"field": "RECTYP", "auto": ["constant"]},  # 单别码
        {"field": "SRV_VATTYP", "auto": ["history", "remembered", "answer"], "ask": "service_vat", "gap": "G-A3"},  # 服务品税口径：按账套历史定，定不了出题、按账套 × 对方记住；销项以前写死 1（主会话 11-12 并入第 1 期）
        {"field": "TRNSTAT", "auto": ["constant"]},
        {"field": "DOCSTAT", "auto": ["constant"]},
        {"field": "BILNUM", "auto": ["constant"]},
        {"field": "CMPLAPP", "auto": ["constant"]},
        {"field": "CMPLDAT", "auto": ["computed"]},  # 完成日 = 单据日
        {"field": "CREBY", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "USERID", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "CUSCOD", "auto": ["source_code", "remembered", "master"], "ask": "party", "review": {"column": "party", "editable": True}},  # 没有买方的票记现金客户
        {"field": "YOUREF", "auto": ["face"], "review": {"column": "ref", "editable": True}},
        {"field": "SLMCOD", "auto": ["master"]},  # 取客户档销售员，没有留空；不问不显示（D-6，CC 桌面版那一批）
        {"field": "RCVAMT", "auto": ["computed", "answer"], "ask": "payment", "review": {"column": "payment", "editable": True}, "gap": "G-A12"},  # 现销 HS / 赊销 IV：没有付款信号时出题，不再静默默认赊销
        {"field": "CSHRCV", "auto": ["computed", "answer"], "ask": "cash_account", "gap": "G-A13"},  # 现销借方科目：现金 / 银行科目（载荷 21 ``cash_account``），不再一律借现金
        {"field": "IVCAMT", "auto": ["computed"]},
    ],
    # 现销（ARTRN / ISVAT / GLJNL / GLJNLIT）
    "express:HS": [
        {"field": "DOCNUM", "auto": ["sequence"], "review": {"column": "docnum", "editable": False}},  # 单别前缀 + 佛历日期 + 当日序号（销售用票面号，见 IV / HS）
        {"field": "DOCDAT", "auto": ["face"], "review": {"column": "date", "editable": True}, "gap": "G-R1"},
        {"field": "VATDAT", "auto": ["face"], "review": {"column": "date", "editable": True}, "gap": "G-R1"},  # 同单据日
        {"field": "VATPRD", "auto": ["face"], "default": {"value": "document_month", "basis": "docs/work/2026-11-12-cc-guided-entry-express-coverage.md#section-1"}},  # 与 Express 手录一致：税期默认单据月
        {"field": "VOUCHER", "auto": ["computed"]},  # 分录凭证号 = 单号
        {"field": "VOUDAT", "auto": ["computed"]},  # 分录日期 = 单据日
        {"field": "DESCRP", "auto": ["computed", "face"]},  # 分录摘要：对方名 + 票号
        {"field": "REFNUM", "auto": ["face"], "review": {"column": "ref", "editable": True}, "gap": "G-R1"},  # 采购：供应商发票号，缺了不拒收（D-5，CC 桌面版那一批）；销售：票面号
        {"field": "DEPCOD", "auto": ["remembered", "book_setting"], "default": {"value": "book_default", "basis": "docs/work/2026-11-12-cc-guided-entry-express-coverage.md#section-9.2"}},  # 部门不问不显示（D-1）；原生 76–94% 由单据簿默认带
        {"field": "FLGVAT", "auto": ["computed"]},  # 含税 / 不含税 / 无税：按单别与有无税额（C/dbf_fields.py）
        {"field": "VATRAT", "auto": ["constant"]},  # 法定 7%
        {"field": "VATREC", "auto": ["constant"]},  # 进项 P / 销项 S
        {"field": "VATCOD", "auto": ["computed"]},  # 行税码：免税行 0，其余抄物料
        {"field": "AMOUNT", "auto": ["face"], "review": {"column": "amount", "editable": True}, "gap": "G-R1"},  # 税前额
        {"field": "AFTDISC", "auto": ["computed"]},  # 折扣摊进行金额后的税前额
        {"field": "TOTAL", "auto": ["computed"]},
        {"field": "NETVAL", "auto": ["computed"]},
        {"field": "NETAMT", "auto": ["computed"]},
        {"field": "VATAMT", "auto": ["face"], "review": {"column": "vat", "editable": True}, "gap": "G-R1"},
        {"field": "VAT01", "auto": ["computed"]},
        {"field": "AMT01", "auto": ["computed"]},
        {"field": "AMTRAT0", "auto": ["computed"]},  # 免税额
        {"field": "REMAMT", "auto": ["computed"]},  # 赊的未付 / 未收 = 合计，现的 = 0
        {"field": "PAYTRM", "auto": ["master", "answer"], "review": {"column": "due_date", "editable": True}, "gap": "G-R2"},  # 到期日（载荷 21 ``due_date_be``）；没给时对方档账期；复核卡第 2 期露出
        {"field": "DUEDAT", "auto": ["master", "answer"], "review": {"column": "due_date", "editable": True}, "gap": "G-R2"},  # 赊的 = 单据日 + 账期 / 载荷 ``due_date_be``；现的 = 单据日
        {"field": "ORGNUM", "auto": ["face", "master"]},  # 对方分店号
        {"field": "TAXID", "auto": ["face", "master"], "review": {"column": "tax_id", "editable": True}},
        {"field": "PRENAM", "auto": ["face", "master"]},
        {"field": "ACCNUM", "auto": ["master", "remembered", "history"], "ask": "account", "review": {"column": "account", "editable": True}},  # 分录科目：费用 / 收入科目走科目题；应付 / 应收取对方档；现金科目见 cash_account
        {"field": "TRNTYP", "auto": ["constant"]},  # 借 0 / 贷 1
        {"field": "JNLTYP", "auto": ["constant"]},  # 采购 04 / 销售 03
        {"field": "SEQIT", "auto": ["computed"]},  # 分录行序
        {"field": "NXTSEQ", "auto": ["computed"]},
        {"field": "RECTYP", "auto": ["constant"]},  # 单别码
        {"field": "SRV_VATTYP", "auto": ["history", "remembered", "answer"], "ask": "service_vat", "gap": "G-A3"},  # 服务品税口径：按账套历史定，定不了出题、按账套 × 对方记住；销项以前写死 1（主会话 11-12 并入第 1 期）
        {"field": "TRNSTAT", "auto": ["constant"]},
        {"field": "DOCSTAT", "auto": ["constant"]},
        {"field": "BILNUM", "auto": ["constant"]},
        {"field": "CMPLAPP", "auto": ["constant"]},
        {"field": "CMPLDAT", "auto": ["computed"]},  # 完成日 = 单据日
        {"field": "CREBY", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "USERID", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "CUSCOD", "auto": ["source_code", "remembered", "master"], "ask": "party", "review": {"column": "party", "editable": True}},  # 没有买方的票记现金客户
        {"field": "YOUREF", "auto": ["face"], "review": {"column": "ref", "editable": True}},
        {"field": "SLMCOD", "auto": ["master"]},  # 取客户档销售员，没有留空；不问不显示（D-6，CC 桌面版那一批）
        {"field": "RCVAMT", "auto": ["computed", "answer"], "ask": "payment", "review": {"column": "payment", "editable": True}, "gap": "G-A12"},  # 现销 HS / 赊销 IV：没有付款信号时出题，不再静默默认赊销
        {"field": "CSHRCV", "auto": ["computed", "answer"], "ask": "cash_account", "gap": "G-A13"},  # 现销借方科目：现金 / 银行科目（载荷 21 ``cash_account``），不再一律借现金
        {"field": "IVCAMT", "auto": ["computed"]},
    ],
    # 明细行（STCRD / STLOC）
    "express:LINE": [
        {"field": "STKCOD", "auto": ["source_code", "remembered", "master"], "ask": "item", "review": {"column": "item", "editable": True}},  # 已有 / 新建（见 STMAS）/ 记费用
        {"field": "STKDES", "auto": ["face", "master"], "review": {"column": "item", "editable": True}},
        {"field": "LOCCOD", "auto": ["face", "remembered", "book_setting"], "ask": "location", "review": {"column": "location", "editable": True}, "gap": "G-A6"},  # 行上的 → 记住 → 默认仓 → 主仓；在用仓 ≥2 没记过出题；只列已有仓
        {"field": "TRNQTY", "auto": ["face"], "review": {"column": "qty", "editable": True}, "gap": "G-R3"},
        {"field": "XTRNQTY", "auto": ["computed", "answer"], "ask": "unit", "gap": "G-A5"},  # 库存单位数量 = 数量 × 系数
        {"field": "TQUCOD", "auto": ["face", "master", "answer"], "ask": "unit", "review": {"column": "unit", "editable": True}, "gap": "G-A5"},  # 票面单位对不上物料单位时出单位题（载荷 21 ``unit_code`` / ``factor``）
        {"field": "TFACTOR", "auto": ["master", "answer"], "ask": "unit", "gap": "G-A5"},
        {"field": "UNITPR", "auto": ["face", "computed"], "review": {"column": "price", "editable": True}},  # 缺了用金额 ÷ 数量
        {"field": "XUNITPR", "auto": ["computed"]},
        {"field": "TRNVAL", "auto": ["face"], "review": {"column": "amount", "editable": True}},
        {"field": "XTRNVAL", "auto": ["computed"]},
        {"field": "XSALVAL", "auto": ["computed"]},
        {"field": "DISCAMT", "auto": ["constant"]},  # 整单折扣摊进行金额，折扣栏恒写 0
        {"field": "SEQNUM", "auto": ["computed"]},
        {"field": "POSOPR", "auto": ["constant"]},
        {"field": "PEOPLE", "auto": ["computed"]},  # 对方码
        {"field": "ACCNUMDR", "auto": ["master", "computed"]},
        {"field": "ACCNUMCR", "auto": ["master", "computed"]},
        {"field": "MREMBAL", "auto": ["computed"]},
        {"field": "MREMVAL", "auto": ["computed"]},
        {"field": "TOTBAL", "auto": ["computed"]},
        {"field": "TOTVAL", "auto": ["computed"]},
        {"field": "LASUPD", "auto": ["computed"]},  # 主档最后变动日 = 单据日（原生进货 / 销货，11-22 实录）
        {"field": "OPNBAL", "auto": ["computed"]},
        {"field": "OPNVAL", "auto": ["computed"]},
        {"field": "LOCBAL", "auto": ["computed"]},  # 各仓结存 STLOC
        {"field": "STKCLASS", "auto": ["master"]},
        {"field": "STATUS", "auto": ["constant"]},
        {"field": "DOCNUM", "auto": ["computed"]},  # = 表头单号
        {"field": "DOCDAT", "auto": ["computed"]},  # = 表头日期
        {"field": "DEPCOD", "auto": ["computed"]},  # = 表头部门
        {"field": "REFNUM", "auto": ["computed"]},  # = 表头参考号
        {"field": "NETVAL", "auto": ["computed"]},
        {"field": "CMPLDAT", "auto": ["computed"]},  # 完成日 = 单据日
        {"field": "SLMCOD", "auto": ["master"]},  # 销售行的销售员 = 表头（取客户档，D-6）；不问不显示
        # 凭证稽核栏（dbf_schema 写 GLJNL 头 / GLJNLIT 分录，同原生记法，1.12.23）
        {"field": "CREDAT", "auto": ["machine"]},  # 凭证头建立日 = 录入当天
        {"field": "CHGDAT", "auto": ["machine"]},  # 凭证头 / 分录改动日 = 录入当天
        {"field": "CHGTIM", "auto": ["machine"]},  # 分录改动时刻 HHMM
        {"field": "USERID", "auto": ["machine"]},  # 凭证头录入人（同单头 USERID）
    ],
    # 新供应商（APMAS）
    "express:APMAS": [
        {"field": "SUPCOD", "auto": ["sequence"]},  # 名称首字 + 3 位序号
        {"field": "SUPNAM", "auto": ["face"], "ask": "new_party", "review": {"column": "party_name", "editable": True}, "gap": "G-A8"},
        {"field": "SUPTYP", "auto": ["book_mode", "answer"], "ask": "new_party", "gap": "G-A8"},  # 预选众数、收起；载荷 21 对方块 ``type``
        {"field": "PRENAM", "auto": ["face"], "review": {"column": "party_name", "editable": True}, "gap": "G-A8"},
        {"field": "TAXID", "auto": ["face"], "review": {"column": "tax_id", "editable": True}, "gap": "G-A8"},  # 原生供应商 31%、客户 72% 没税号：空着不拦
        {"field": "ORGNUM", "auto": ["face"], "review": {"column": "branch", "editable": True}, "gap": "G-A8"},
        {"field": "ADDR01", "auto": ["face"]},
        {"field": "ADDR02", "auto": ["face"]},
        {"field": "ADDR03", "auto": ["face"]},
        {"field": "ACCNUM", "auto": ["book_setting", "book_mode"], "ask": "new_party", "gap": "G-A8"},  # 应付 / 应收科目：预选账套默认，收起可改
        {"field": "PAYTRM", "auto": ["answer"], "default": {"value": "0", "basis": "docs/work/2026-11-12-cc-guided-entry-express-coverage.md#section-9.2"}, "gap": "G-R2"},  # 账期：原生供应商档 91%、客户档 70% 是 0；单上的到期日另由 due_date_be 给
        {"field": "FLGVAT", "auto": ["book_mode"]},
        {"field": "VATRAT", "auto": ["constant"]},
        {"field": "TABPR", "auto": ["constant"]},
        {"field": "STATUS", "auto": ["constant"]},
        {"field": "BALANCE", "auto": ["computed"]},
        {"field": "LASRCV", "auto": ["computed"]},
        {"field": "CREBY", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "CREDAT", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "CHGDAT", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "USERID", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
    ],
    # 新客户（ARMAS）
    "express:ARMAS": [
        {"field": "CUSCOD", "auto": ["sequence"]},  # 名称首字 + 3 位序号
        {"field": "CUSNAM", "auto": ["face"], "ask": "new_party", "review": {"column": "party_name", "editable": True}, "gap": "G-A8"},
        {"field": "CUSTYP", "auto": ["book_mode", "answer"], "ask": "new_party", "gap": "G-A8"},  # 预选众数、收起；载荷 21 对方块 ``type``
        {"field": "PRENAM", "auto": ["face"], "review": {"column": "party_name", "editable": True}, "gap": "G-A8"},
        {"field": "TAXID", "auto": ["face"], "review": {"column": "tax_id", "editable": True}, "gap": "G-A8"},  # 原生供应商 31%、客户 72% 没税号：空着不拦
        {"field": "ORGNUM", "auto": ["face"], "review": {"column": "branch", "editable": True}, "gap": "G-A8"},
        {"field": "ADDR01", "auto": ["face"]},
        {"field": "ADDR02", "auto": ["face"]},
        {"field": "ADDR03", "auto": ["face"]},
        {"field": "ACCNUM", "auto": ["book_setting", "book_mode"], "ask": "new_party", "gap": "G-A8"},  # 应付 / 应收科目：预选账套默认，收起可改
        {"field": "PAYTRM", "auto": ["answer"], "default": {"value": "0", "basis": "docs/work/2026-11-12-cc-guided-entry-express-coverage.md#section-9.2"}, "gap": "G-R2"},  # 账期：原生供应商档 91%、客户档 70% 是 0；单上的到期日另由 due_date_be 给
        {"field": "FLGVAT", "auto": ["book_mode"]},
        {"field": "VATRAT", "auto": ["constant"]},
        {"field": "TABPR", "auto": ["constant"]},
        {"field": "STATUS", "auto": ["constant"]},
        {"field": "BALANCE", "auto": ["computed"]},
        {"field": "LASRCV", "auto": ["computed"]},
        {"field": "CREBY", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "CREDAT", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "CHGDAT", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "USERID", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
    ],
    # 新库存品（STMAS）
    "express:STMAS": [
        {"field": "STKCOD", "auto": ["sequence"]},  # PN + 5 位序号
        {"field": "STKDES", "auto": ["face"], "ask": "new_item", "review": {"column": "item", "editable": True}, "gap": "G-A10"},
        {"field": "STKDES2", "auto": ["face"]},
        {"field": "STKTYP", "auto": ["answer"], "ask": "goods_posting"},  # 库存品 0 / 服务品 3，由「走库存 / 记费用」定
        {"field": "VATCOD", "auto": ["master", "computed"]},  # 抄模板；免税行写 0
        {"field": "STATUS", "auto": ["constant"]},
        {"field": "CREBY", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "CREDAT", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "CHGDAT", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "USERID", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "QUCOD", "auto": ["face", "book_mode", "answer"], "ask": "new_item", "review": {"column": "unit", "editable": True}, "gap": "G-A10"},  # 库存单位：票面有单位就用票面的并露在外面；票面没有才预选众数、要点了才算（D-4）；载荷 21 行键 unit_code
        {"field": "STKGRP", "auto": ["book_mode", "answer"], "ask": "new_item", "gap": "G-A10"},  # 分类：预选众数、收起（载荷 21 ``stkgrp``）
        {"field": "ACCCOD", "auto": ["book_mode", "answer"], "ask": "new_item", "gap": "G-A10"},  # 存货科目组：预选众数、收起；账套第一件库存品时必问（载荷 21 ``acccod``）
        {"field": "CFACTOR", "auto": ["constant"], "gap": "G-B1"},  # 新建四个单位列同码，系数写 1（CC 桌面版那一批修）
        {"field": "PFACTOR", "auto": ["constant"], "gap": "G-B1"},
        {"field": "SFACTOR", "auto": ["constant"], "gap": "G-B1"},
        {"field": "NEGALLOW", "auto": ["answer"], "gap": "G-B6"},  # 留空 = 跟账套，收在「新物料」已定里可改（载荷 21 ``negallow``）
        {"field": "METHOD", "auto": ["master"]},
        {"field": "MREMBAL", "auto": ["computed"]},
        {"field": "MREMVAL", "auto": ["computed"]},
        {"field": "TOTBAL", "auto": ["computed"]},
        {"field": "TOTVAL", "auto": ["computed"]},
    ],
    # 新服务品（STMAS / ISACC）
    "express:STMAS_SERVICE": [
        {"field": "STKCOD", "auto": ["sequence"]},  # PN + 5 位序号
        {"field": "STKDES", "auto": ["face"], "ask": "new_item", "review": {"column": "item", "editable": True}, "gap": "G-A10"},
        {"field": "STKDES2", "auto": ["face"]},
        {"field": "STKTYP", "auto": ["answer"], "ask": "goods_posting"},  # 库存品 0 / 服务品 3，由「走库存 / 记费用」定
        {"field": "VATCOD", "auto": ["master", "computed"]},  # 抄模板；免税行写 0
        {"field": "STATUS", "auto": ["constant"]},
        {"field": "CREBY", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "CREDAT", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "CHGDAT", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "USERID", "auto": ["machine"]},  # 写入人 / 时间戳 / 机器标记
        {"field": "ACCNUM01", "auto": ["remembered", "history"], "ask": "account"},  # 服务品挂的费用科目
        {"field": "ACCNUM03", "auto": ["master", "computed"]},
        {"field": "ACCTYP", "auto": ["constant"]},
        {"field": "ACCDES", "auto": ["face", "computed"]},
        {"field": "ACCDES2", "auto": ["face", "computed"]},
        {"field": "METHOD", "auto": ["constant"]},
    ],
}

# CC 桌面版写入模块 → 它写哪几种单据（路径相对仓库根）。新增写入模块要在这里登记，否则它写的字段没人管。
WRITERS = {
    "companion/src/companion/dbf_writer.py": ["express:RR", "express:HP"],
    "companion/src/companion/dbf_sales.py": ["express:IV", "express:HS"],
    "companion/src/companion/dbf_fields.py": ["express:RR", "express:HP", "express:IV", "express:HS", "express:LINE"],
    "companion/src/companion/dbf_schema.py": ["express:LINE"],
    "companion/src/companion/stock_purchase.py": ["express:LINE"],
    "companion/src/companion/stock_sale.py": ["express:LINE"],
    "companion/src/companion/stock_location.py": ["express:LINE"],
    "companion/src/companion/master_create.py": ["express:APMAS", "express:ARMAS"],
    "companion/src/companion/product_master.py": ["express:STMAS", "express:STMAS_SERVICE"],
}
# 写入模块里出现、但不是 Express 字段的大写串（表名等）：写明原因
NOT_FIELDS = {
    "APMAS": "table name (master_create dispatches by table)",
    "ARMAS": "table name (master_create dispatches by table)",
}

# 载荷 21 的新键（``push.common.GUIDED_KEYS``）各落到哪个字段 [键, 字段]；``guided`` 是开关，不落字段
PAYLOAD_KEYS = {
    "guided": [],
    "due_date_be": [["express:RR", "DUEDAT"], ["express:IV", "DUEDAT"], ["express:RR", "PAYTRM"], ["express:IV", "PAYTRM"]],
    "cash_account": [["express:HP", "CSHPAY"], ["express:HS", "CSHRCV"]],
    "unit_code": [["express:LINE", "TQUCOD"], ["express:STMAS", "QUCOD"]],
    "acccod": [["express:STMAS", "ACCCOD"]],
    "stkgrp": [["express:STMAS", "STKGRP"]],
    "cost_choice": [["express:LINE", "XTRNVAL"]],
    "cost": [["express:LINE", "XTRNVAL"]],
    "negallow": [["express:STMAS", "NEGALLOW"]],
    "type": [["express:APMAS", "SUPTYP"], ["express:ARMAS", "CUSTYP"]],
    "payterm": [["express:APMAS", "PAYTRM"], ["express:ARMAS", "PAYTRM"]],
}

# 写入与映射代码里允许「缺了就填一个值」的载荷键与依据（其余由 check-express-defaults 按基线拦，只许减少）
DEFAULT_KEYS = {
    "vat_period_be": {"value": "document_month", "basis": "docs/work/2026-11-12-cc-guided-entry-express-coverage.md#section-1"},
    "depcod": {"value": "book_default", "basis": "docs/work/2026-11-12-cc-guided-entry-express-coverage.md#section-9.2"},
    "payterm": {"value": "0", "basis": "docs/work/2026-11-12-cc-guided-entry-express-coverage.md#section-9.2"},
    "negallow": {"value": "", "basis": "docs/work/2026-11-12-cc-guided-entry-express-coverage.md#section-2.4"},
}

# 某种录入方式下还没覆盖的字段（如实登记，不许当成已覆盖）：方式 → 键 → {fields, reason, fix}
MODE_GAPS = {
    "operator": {
        # 代录 × 新物料（单位码 / 科目组 / 分类 / 负库存）11-13 已关：CC 桌面版 296f02c9c 代录建主档传四个行键，服务端代录单带这四键照 21 派
        # A5 单位换算、A11 出库成本、A13 现销收款科目：代录由 Express 界面自己拦 / 弹（主会话 11-12 定这期不出题）
        "express:LINE": {
            "fields": ["XTRNQTY", "TFACTOR", "XTRNVAL"],
            # 代录录单时 Express 界面自己处理单位与成本（主会话 11-12 定第 1 期不出题）；测试机探明代录表单后另排
            "reason": "Express handles units and cost on screen in operator entry (phase 1 asks nothing)",
            "fix": "probe the operator form on the test PC first",
        },
        "express:HS": {
            "fields": ["CSHRCV"],
            # 代录现销的收款科目由 Express 界面定（主会话 11-12 定第 1 期不出题）；测试机探明那一格后另排
            "reason": "Express picks the cash-sale account on screen in operator entry (phase 1 asks nothing)",
            "fix": "probe the cash-sale receipt field on the test PC first",
        },
    },
}


# 还没覆盖的必填字段（所有录入方式，如实登记，不许当成已覆盖；主会话 11-13）：键 → 字段 → 缺什么、等什么
OPEN_GAPS = {
    # A8：新建对方的类型、应付 / 应收科目——服务端拿不到账套的对方类型表与众数，CC 桌面版写入时照旧取众数。
    # 等 CC 桌面版主档读回加 reported_party_types（约定草稿：共享笔记「引导式-CC读回四表-约定草稿-11-13.md」）
    "express:APMAS": {"fields": ["SUPTYP", "ACCNUM"], "waiting": "reported_party_types"},
    "express:ARMAS": {"fields": ["CUSTYP", "ACCNUM"], "waiting": "reported_party_types"},
    # A10：新物料的单位、存货科目组、分类——同上；等 reported_units / reported_item_groups / reported_stock_acc_groups
    "express:STMAS": {"fields": ["QUCOD", "ACCCOD", "STKGRP"], "waiting": "reported_units reported_item_groups reported_stock_acc_groups"},
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
        if not key.startswith(("express:", "mrerp:")):
            out.append(f"{key}: key must be <ledger>:<document>")
        seen = set()
        for r in fields:
            where = f"{key}.{r.get('field')}"
            if r.get("field") in seen:
                out.append(f"{where}: registered twice")
            seen.add(r.get("field"))
            if not (r.get("auto") or r.get("ask") or r.get("review")):
                out.append(f"{where}: none of auto / ask / review")
            bad = [s for s in r.get("auto") or () if s not in AUTO_SOURCES]
            if bad:
                out.append(f"{where}: unknown sources {bad}")
            if "ask" in r and r["ask"] not in kinds:
                out.append(f"{where}: question id {r['ask']!r} is not a guide-card kind")
            if r.get("default") is not None and not str(r["default"].get("basis") or "").startswith("docs/"):
                out.append(f"{where}: default without a basis document")
    for name, default in DEFAULT_KEYS.items():
        if not str(default.get("basis") or "").startswith("docs/"):
            out.append(f"DEFAULT_KEYS.{name}: default without a basis document")
    for name, targets in PAYLOAD_KEYS.items():
        for key, field in targets:
            if row(key, field) is None:
                out.append(f"PAYLOAD_KEYS.{name}: target {key}.{field} not registered")
    for key, gap in OPEN_GAPS.items():
        for field in gap["fields"]:
            if row(key, field) is None:
                out.append(f"OPEN_GAPS.{key}.{field}: not registered")
    for mode, gaps in MODE_GAPS.items():
        for key, gap in gaps.items():
            for field in gap["fields"]:
                if row(key, field) is None:
                    out.append(f"MODE_GAPS.{mode}.{key}.{field}: not registered")
    return out


__all__ = ["AUTO_SOURCES", "DEFAULT_KEYS", "MODE_GAPS", "NOT_FIELDS", "OPEN_GAPS", "PAYLOAD_KEYS", "REQUIRED_FIELDS", "WRITERS", "keys",
           "names", "problems", "row", "rows"]
