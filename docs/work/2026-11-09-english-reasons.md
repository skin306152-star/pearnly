# 界面上直接显示英文的原因（2026-11-09）

主会话派：测试机中文界面右侧「待处理」卡片里 CC11214-0002 的原因显示英文
「The computer could not read this account…」（截图 `~/pearnly-devtools/release-1108/shots/05-before-send-english-pending.png`）。
按类找出 CC 桌面版、服务端送到界面上的原因与状态文字里没走四语词条、原样显示英文的，补成四语词条，加检查拦以后新冒出来的。
已有文案一条不改。MR.ERP 的原因码由 MR.ERP 线 `pearnly/mrerp-p0-reason-codes`（11-10 已合入 main 64c128a88）补，这里不重复（已与该窗口对过：
它只改 `export_facts._REASON_CODES` 第三条、`intake-upload.js` 同一条、ERP `pending.reason_label` / `transactions.js` 认 `mrerp.*`，
术语表追加在 intake.error / mrerp.error 块末尾；本分支不碰这些，新增行不放块末尾）。

## 原因怎么到界面上

- Cowork：原因记在 `Cowork OCR History.last_push_reason`（`push_state.record_failure` / `submission_failed` 先 `source_text` 还原成词条原文），
  待处理卡片、CC 右栏、结果表格按看的人的语言 `export_facts.reason_text` 再翻：`[码] 原文` → `intake.error.<码>`；其余按原文 `_()`；翻不了的**原样显示**。
- ERP：`express/pending.reason_label` 与单据页 `p.express`：`[码] 原文` → `pending.CODES` 或 `express.error.<码>`；翻不了的**原样显示**。

所以英文原样冒出来只有两种来源：

1. 服务端抛的没走词条的英文句子（`frappe.throw` / `frappe.*Error` 写死英文）被记成原因；
2. CC 桌面版回报的码在该产品术语表里没有词条，于是显示 `[码]` 后面的英文原文。

## 查到的（全仓）

| 来源 | 位置 | 处理 |
|---|---|---|
| 写死英文（本次截图那条） | `pearnly_cowork/assistant/tools.py` `_choices_view`：`frappe.throw("The computer could not read this account set ({0}). …".format(…))` | 改用已有审定词条 `cowork.masters.failed`（网页「要选的项」同一句，四语齐）；一分钟内没读完的用 `cowork.masters.reading`。原英文句子本身违反文案规则（超长、三句、「may」推测），不登记成新词条。已存成英文句子的旧原因由补丁 `v0_0_28_unread_account_set_reason` 改记成 `cowork.masters.failed` |
| 同类写死英文 | 全仓扫 `frappe.throw` / `msgprint` / `frappe.*Error` 的写死文案（含 `.format` 拼参数的写法） | 只有上面这一处 |
| 码没有词条（ERP） | `ACCOUNT_SET_NOT_ALLOWED`、`AMOUNTS_NOT_CONSISTENT`、`ENTRY_NOT_BALANCED`、`ERR_LOCATION`、`ERR_PAYLOAD_VERSION`、`MAPPING_MISSING_CUSTOMER`、`MAPPING_MISSING_ITEM`、`OPERATOR_UNAVAILABLE`、`POS_INVALID` | ERP 加 `express.error.<码>`，四语照搬 Cowork 已审定的 `intake.error.<码>`（不新写文字） |
| 码没有词条（两边都缺） | `OPERATOR_EXEMPT_LINES_NEED_PER_ITEM_VAT`（代录：没开按商品记增值税的账套里免税、应税混着） | 新写四语，Cowork `intake.error.` 与 ERP `express.error.` 同一句 |
| ERP 固定原因映射 | `express/pending.py` 的 `REASONS` / `CODES` / `STATES` | 全部已有词条，不动 |

新写的四语（新增，按官方风格直接写）：

| en | zh | th | ja |
|---|---|---|---|
| Exempt and taxable lines cannot be mixed in this account set. Enter this document in Express | 此账套的单据不能同时有免税行和应税行，请在 Express 中录入 | ชุดบัญชีนี้ใส่รายการยกเว้นภาษีและรายการที่มีภาษีในเอกสารเดียวกันไม่ได้ กรุณาบันทึกใน Express | この会計データでは免税明細と課税明細を同じ伝票に入れられません。Express で入力してください |

## 防再漏

- `tests/test_reason_codes_have_words.py`（进 Development checks）：CC 桌面版 `ERR_*` 常量与 `worker._outcome_for` 分类表里的每个码，
  Cowork 要有四语 `intake.error.<码>`，ERP 要有四语 `express.error.<码>`（或单据页认得的 `pending.CODES`）；MR.ERP 代录目录不扫（MR.ERP 线管）。
  会红：换回 main 的两份术语表时 Cowork 缺 1、ERP 缺 10，测试失败。
- 文案闸门 R-HARD 认出 `frappe.throw("…".format(…))`（原来只认字面量和 f-string，本次那句就是这样漏过的）；`tests/test_check_copy.py` 加用例，
  换回旧的 `copygate/sources.py` 时该用例失败。
- 改的那句的单测改成断言记的是词条 `cowork.masters.failed`。

## 验证

- gate 全过；文案闸门新增 11 · 拦截 0；候选离线 105 组 LANE EXIT 0（d25e99d88，镜像 sha256:9fef288f…）。
- 连带入口（共用件 Cowork 推送 / CC 工具）：改动只在「电脑读不到这份账套」那一个失败分支换了说法、术语表加行、闸门规则与补丁。
  `cowork.express.push`（auto，测试机推送冒烟）、`cowork.cc-chat.push`、`cowork.cc.mrerp-setup`、`cowork.ocr`、`cowork.execution`、`cowork.assistant`、
  `cowork.tax-documents` —— 没测到（今晚测试机不动；推送主路径未改，失败分支由单测覆盖）。部署后在测试机让 CC 推一张读不到账套的单，看待处理卡片按中文显示。
