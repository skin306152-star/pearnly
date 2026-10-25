# 收银跨天退货写成 Express 退货单 SR：共用写账规则 + CC 桌面版写入（B3b 第 ① 步）

> 评估（已审定）：`docs/work/2026-10-26-stock-refund-sr-评估.md`（在 ERP 侧分支 `pearnly/stock-refund-sr`）。
> 本分支 `pearnly/stock-refund-sr-cc`，基线 main `d8d42e6f6f094819f84618bbf6fb7d1b0b718cc6`。ERP 侧（退货日汇总、`express/pos_day.py`、`push.py`、
> `prepare.py`、`contracts.py`、`api.py`）在 ERP 窗口的分支做，本分支不改 `apps/pearnly_erp_portal/` 下任何文件。
> 实录：服务器测试账套第 5 条 SR0000001（共享笔记「服务器test账套-录单结果」86–95 行；计划文档九.7c 与「现销退货、当场退现金的写法」）。

## 1. 做了什么

| 层 | 文件 | 改动 |
| --- | --- | --- |
| 共用约定 | `apps/pearnly_core/pearnly_core/express_rules/contracts/pos.py` | `refund` 块：`REFUND_KEY`、`refund_audit(against, against_total)`、`refund_facts(page)`（不是退货回 None，形状不对抛 `PosFactsError`）、`check_refund`（同 `check`，另外现金也不能为负）、`REFUND_PAYLOAD_VERSION = 10`、`REFUND_KIND = "refund"`、`REFUND_SUBJECT = "pos_refund"`、`REFUND_CNTYP = 3`、`REFUND_RETSTK = "Y"`、两个新原因码 |
| 推送选择 | `.../express_rules/push/choices.py` | `resolve(..., refund=False)`：`refund=True` 且销项时要一个退货科目（问一次、按公司记住）；`REFUND = "refund"`；**不进 `KINDS`**（Cowork 按 `KINDS` 存取、列出账套记忆，加进去会改 Cowork 现有输出） |
| 销项映射 | `.../express_rules/push/sales_mapper.py` | 只有读到 `refund` 块才走新路径（`_refund_block` / `_as_refund`）：单别 SR、分录带角色、载荷带 `refund`、版本 10 |
| 回执口径 | `apps/pearnly_core/pearnly_core/cc_desktop/wire/ack_rules.py` | 四个新拒收码进转人工清单 |
| CC 版本 | `companion/src/companion/payload_contract.py` | 支持集加 10（旧版照现有写法诚实拒收） |
| CC 校验 | `companion/src/companion/sales_adapter.py` | 只在带 `refund` 块时认 SR；`_validate_refund`；`SalesEntry` 加 `is_refund / refund_against / refund_against_total` |
| CC 写入 | `companion/src/companion/dbf_refund.py`（新） | SR 写路，由 `dbf_sales.write_sale` 分流 |
| CC 共用 | `dbf_sales.py`（分流）、`pos_journal.py`（`reverse`）、`stock_location.py`（`KIND_RETURN`）、`worker.py`（`_outcome_for`）、`table_specs.py`（合成表补列） | |
| 文案 | `apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv` + 生成的翻译包 | 7 条新原因（`intake.error.*`），无改动已有文案 |
| 登记 | `scripts/impact/shared-parts.toml`、`.github/workflows/development.yml` | 新入口 `erp.cc.pos-refund`；`dbf_refund.py` 进 `cc-dbf-write`；新测试进 CI |

没有 `refund` 块的载荷与改动前**逐字节相同**：`tests/test_express_rules_pos_refund.py::OrdinaryPayloadTests` 用改动前的共用规则（main `d8d42e6f`）
算出 ERP 收银日汇总 HS（含 PromptPay）、ERP 普通销售单、OCR 赊销票三种载荷的 SHA-256 钉住；`resolve` 不传 `refund` 时结果不变（同测试文件）。
CC 侧普通现销不会进退货写路（`test_refusals_need_a_person_and_ordinary_sales_never_reach_the_refund_writer`）。

## 2. 载荷（第 10 版）

ERP 交给写账规则的主页面：`_extraction_audit` 除 `pos` 外加 `refund` 块（ERP 侧已按此写：`contracts.pages` 调 `pos_contract.refund_audit`）：

```
"_extraction_audit": {"pos": {...金额、张数、各付款方式都记正数...},
                      "refund": {"against": "<原 HS 在 Express 的单号>", "against_total": "<原 HS 含税合计，两位小数串>"}}
```

写账规则出的载荷（与普通现销 HS 相比只多 / 改这些）：

| 键 | 值 |
| --- | --- |
| `doctype` | `"SR"` |
| `lines` | 带 `role`：`{"role": "refund", "side": "D", "acc": 退货科目, "amount": 税前}`、有税时 `{"role": "output_vat", "side": "D", ...税额}`、`{"role": "cash", "side": "C", "acc": 连接的 ar_acc（CC 换成账套现金科目 ISINFO.ACCNUM00）, "amount": 含税}` |
| `refund` | `{"against", "against_total", "cntyp": 3, "retstk": "Y"}` |
| `pos` | 同 HS：`{first, last, count, docnum: "auto", cash, transfers}`；`cash` 不能为负；`transfers` 的收款科目沿用 HS 记住的（推送选择 `receipt`，同一归类键） |
| `items` | 必须全是带存货码的库存行（`item_mode = stock_sale`、`code`）；不是 → 原因 `pos_refund_items_invalid` |
| `payload_version` | ≥ 10 |
| `doctype_src` | `"pos_refund"` |

停下的原因（映射）：没选退货科目 `pos_refund_account_missing`；明细不是全库存行 `pos_refund_items_invalid`；`refund` 块形状不对、或有 `refund` 没 `pos`
→ `pos_facts_invalid`；退货的付款方式对不上或现金为负 → `pos_payments_mismatch`；非现金退款的收款科目没记过 → `pos_receipt_account_missing`（同 HS）。

## 3. 退货科目的取法与给 ERP 的接口

- 原生取法：实录 SR0000001 借的是 `41-02-00-00`（รับคืน / ลดหนี้ 一类）。账套里没找到 Express 自己定这个科目的地方（ISRUN 的 SR 行、ISINFO 都没有这一栏；
  样本不足以按科目名猜），所以按方案走「问一次、按公司记住」：推送选择新一类 `refund`，归类键 `pos_refund`（整家公司一个）。
- **ERP 侧要改的**（ERP 窗口做）：
  1. `express/prepare.py` 调 `choice_rules.resolve(..., receipts=_receipts(doc), refund=bool(doc.get("refund")))`。
     要选时 `needed` 里是 `{"kind": "refund", "subject": "pos_refund", "label": "", "current": ""}`；选过的从 `choices.remembered(company)` 的
     `{"refund": {"pos_refund": 科目}}` 读，科目不在这份账套科目表里会重新问。applied 里是 `applied["refund"]`，随 `config["choices"]` 交给映射器。
  2. `express/api.py` 选项：`"refund"` 同 `"account"` / `"receipt"` 从这份账套的科目表取（建议收入类在前）；保存走现有 `choices.remember(company, "refund", "pos_refund", 科目)`。
  3. 派单版本：载荷 `payload_version` 是 10，`cc_desktop.jobs.enqueue(payload_version=...)` 照载荷的值传（只交给报过支持 10 的电脑）。
  4. 文案：ERP 用 `express.error.<码>` 显示 CC 回报的码，新码 `REFUND_ORIGINAL_NOT_FOUND / REFUND_ORIGINAL_CHANGED / REFUND_LINE_NOT_FOUND /
     REFUND_EXCEEDS_ORIGINAL / REFUND_INVALID` 要在 ERP 术语表加（本分支只在 Cowork 术语表加了 `intake.error.*`，中文可照抄）；映射原因
     `pos_refund_account_missing` / `pos_refund_items_invalid` 在 ERP 的停下原因里怎么显示由 ERP 侧定（`pos_refund_account_missing` 应变成选退货科目的弹窗）。
  5. 契约测试：建议在 `tests/test_companion_contract.py` 加一条把 ERP `pos_day.py` 的 `refund` 键与 `pos.refund_audit` 绑起来（本分支没碰 ERP 文件，没加）。

## 4. CC 写法（`dbf_refund.py`）与实录逐字段

| 表 | 写什么 | 实录 SR0000001 |
| --- | --- | --- |
| ARTRN | RECTYP 5、CNTYP 3、SONUM = 原单号、FLGVAT 1、AMOUNT / AFTDISC / TOTAL / NETAMT 含税、NETVAL 税前、VATAMT、RCVAMT 含税、IVCAMT 原单合计、CMPLAPP Y、CMPLDAT、DOCSTAT N、SRV_VATTYP 1、NXTSEQ 行数、CUSCOD = **原单的客户**、YOUREF = 载荷参考号；REMAMT / CSHRCV 0 | 同（CSHRCV 实录没列，按 0） |
| STCRD | POSOPR 2、FLAG 2、RETSTK Y、PEOPLE 客户、RDOCNUM「原单号左对齐补空格到 14 位 + 原单行号」、TRNQTY / UNITPR / TRNVAL **含税**（交易单位）、NETVAL / XSALVAL 税前、XTRNQTY 库存单位、XUNITPR / XTRNVAL = 原单那一行的成本、MREMBAL / MREMVAL 写后结存 | 同；MLOTNUM / LOTBAL / LOTVAL 与「旧 STCRD 行的批次链」不写（见第 6 节） |
| STMAS | MREMBAL / TOTBAL +数量、MREMVAL / TOTVAL +成本 | 同（MLOTNUM 不改） |
| STLOC | 退货回原仓：LOCBAL、QTY{期} / VAL{期} 加，只记 LMOVDAT | LOCBAL、QTY12NY、VAL12NY |
| GLJNL / GLJNLIT | JNLTYP = ISRUN 里 SR 那一行的（没有用 03）；摘要「ลดหนี้/รับคืน + 客户名」；SEQIT 1 借退货（税前）、2 借销项税、3 贷现金（ISINFO.ACCNUM00 可记账时，否则载荷给的）、8 借存货 / 9 贷销货成本（按 STMAS.ACCCOD → ISACC，成本为 0 不记） | 同 |
| ISVAT | VATREC S、RECTYP 5、AMT01 / VAT01 正数、REFNUM = SR 单号、DESCRP 客户名、TAXID、PRENAM、ORGNUM；混有免税商品时金额栏照 `vat_fields`（同 HS） | 同 |
| ISRUN | SR 计数 +1 | 同 |
| ARBAL | SR 月桶 + 退款额（期号照 ISPRD，同 HS 的 `_update_arbal`） | ARBAL.SR12NY 0 → 100 |
| ARMAS | BALANCE − 退款额（老表没这一列不动） | 160.5 → 60.5 |
| 转账凭证 | 非现金退款：每种付款方式一张普通日记账凭证「借 现金 / 贷 收款科目」（`pos_journal.write(reverse=True)`） | （实录只有退现金；方向按评估） |

单号：`SR` + 佛历年月日 + `-` + 当日序号（同 CC 写的其他单，例 `SR691026-001`）。

写前只读找原单（任何一条不过整单拒、一行不写，都转人工）：

| 码 | 情形 |
| --- | --- |
| `REFUND_ORIGINAL_NOT_FOUND` | ARTRN 里没有这张现销 HS（RECTYP 1、没作废） |
| `REFUND_ORIGINAL_CHANGED` | 原单含税合计与 ERP 记的差超过 0.01（在 Express 里被改过） |
| `REFUND_LINE_NOT_FOUND` | 原单 STCRD 里没有「这个存货码 + 这个仓」的行 |
| `REFUND_EXCEEDS_ORIGINAL` | 扣掉以前退货单（RDOCNUM 指向原单那一行）已退的库存单位数量后不够退 |
| `STOCK_ITEM_NOT_FOUND`（现有码） | 存货码在 Express 主档里没有 / 不是库存品，或有成本却没配存货科目组 |

载荷校验不过（`REFUND_INVALID`，回报码照现有写法是 `PAYLOAD_INVALID`）：没 `refund` 块却是 SR、有 `refund` 块却不是 SR、原单号不是 HS 开头或超过 12 字节、
原单合计不为正、cntyp ≠ 3 / retstk ≠ Y、没有 `pos` 块或现金为负、明细不是全带码库存行、分录角色 / 方向 / 金额不对。
写入在一份写前备份里，任何一步失败（含读回不符）整单回滚。四个新码登记进 `worker._outcome_for`（needs_review）与 `ack_rules.NEEDS_PERSON_CODES`。

## 5. 测试结果（本机 macOS，2026-10-26）

| 命令 | 结果 |
| --- | --- |
| `python3 tests/test_express_rules_pos_refund.py`（新） | 9 过 |
| `cd companion && python -m pytest -q -p no:cacheprovider tests/test_dbf_refund.py`（新） | 27 过 |
| `cd companion && python -m pytest -q -p no:cacheprovider tests`（全量） | 1079 过、50 跳过、6 失败——6 个都是 `test_write_dispatch.py` 本机没装 PySide6（`ModuleNotFoundError`），与本次无关；Windows CI 上有 PySide6 |
| `python3 tests/test_companion_contract.py` | 77 过 |
| `python3 tests/test_check_impact.py`、`python3 scripts/check-impact.py` | 过；命中 cc-desktop / express-rules / cc-dbf-write，新入口在列 |
| `python3 scripts/check-architecture.py` | PASS（0 违规） |
| `python3 scripts/check-i18n.py --strict` | 退出码 0 |
| `python3 scripts/i18n-build.py --check` | PASS |
| `python3 scripts/check-copy.py` | 新增 7、改动 0、拦截 0、标黄 0 |
| `.github/workflows/development.yml` 全部 python3 步骤 | 见第 7 节 |

新增文案清单（`check-copy.py --report` 第 2 节）：

| 键 | 中文 | English |
| --- | --- | --- |
| intake.error.REFUND_ORIGINAL_NOT_FOUND | Express 中没有原现销单 | The original cash sale is not in Express |
| intake.error.REFUND_ORIGINAL_CHANGED | 原现销单在 Express 中已被修改 | The original cash sale was changed in Express |
| intake.error.REFUND_LINE_NOT_FOUND | 原现销单上没有此商品 | This item is not on the original cash sale |
| intake.error.REFUND_EXCEEDS_ORIGINAL | 退货数量超过原单销售数量 | The returned quantity is more than the quantity sold |
| intake.error.REFUND_INVALID | 退货单数据格式不对 | The return is not in the expected form |
| intake.error.pos_refund_account_missing | 请选择销货退回科目 | Choose the sales returns account |
| intake.error.pos_refund_items_invalid | 退货明细须为 Express 库存商品 | Each returned line must be an Express stock item |

## 6. 与实录的口径

- STCRD 的 TRNVAL / UNITPR **照实录记含税**（SR 是 Express 原生单，照原生写；CC 写 HS 时这两栏记税前，不沿用）。载荷每行只带税前额，各行含税额按税前额
  占比分摊单据含税合计、尾差落最后一行（合计恒等于含税合计）。混有免税商品的退货单这样分摊会把税摊到免税行上——行上的含税额只影响 STCRD 显示，
  凭证、销项税、合计都按单据的税前 / 税额写，不受影响；要逐行准确需 ERP 交各行含税额（待定）。
- RDOCNUM 写「原单号左对齐补空格到 14 位 + 行号」，与实录一致；**行号多位（≥ 10 行）时直接接在后面**，是否如此待实录核对。
- STCRD 结存栏（MREMBAL / MREMVAL）照 CC 现有写法记写后结存；先进先出批次链（MLOTNUM / LOTBAL / LOTVAL、实录里被改的那一行旧 STCRD 的
  MLOTNUM / MREMBAL）不写，由 Express「计算成本」重排（同 CC 现有库存写法，计划文档九.7 第 3 条）。成本取原单那一行（XTRNVAL / XTRNQTY），对应原生「按原批次」。
- 客户取原单 ARTRN 的 CUSCOD（SR 挂原单，原生如此），不按载荷的客户名重新找档。
- 退货回仓的 STLOC 只记 LMOVDAT：实录退货与原单同一天，看不出 LSELDAT 动没动，不碰。
- ARMAS.BALANCE、ARBAL.SR 桶照实录冲减（实录现象，照做不修正）。
- 回执不带 `write_verify` 的「写完顺手核对」（HS 有）；SR 有自己的读回（单头、明细、凭证、销项税、各仓结存），不符整单回滚。

## 7. 没做完 / 没验证的

- 没在 C 盘 T 账套、实验栈、测试机上写过：只有合成账套用例。101 本 T 账套离线回归加 SR、实验栈冒烟、CC 测试包都是第 ③ 步（依赖 ERP 侧）。
- 真表字段宽度未核：合成表规格里 ARTRN.SONUM C(12)、CNTYP C(1)、STCRD.RDOCNUM C(17)、RETSTK / FLAG C(1)、ARMAS.BALANCE 按推测写；
  老账套没有这些列时写路跳过那一列（`_filtered`），不报错。
- 待实录（评估第 11 节）：SR 的 SONUM 挂 HS **日汇总**（CC 自编单号 `HS691025-001`）时 Express 认不认；RDOCNUM 多位行号；混有免税商品时 Express 原生 SR 的行金额。
- 非现金退款「借现金 / 贷收款科目」的方向按评估，没有实录。
- ERP 侧接口（第 3 节）未接，端到端没跑通。
- 没推送、没合并、没发 CC 版本（进 CC 1.12.6 由发布窗口定）。
