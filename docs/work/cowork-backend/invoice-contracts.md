# 发票 schema 与纯业务校验迁移

日期：2026-09-21。新平台本批起点 `392d9d917cb62c1a00d0bb43f72dc7b806082a86`，工作树 `cowork-backend-migration`。

生产基准由主任务重新核对为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，凭据 `/tmp/pearnly-cowork-migration-20260920/baseline-third-start.json`；来源只读快照仍为 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。本批只实现纯模型/规则，不执行供应商、不读取用户数据、不注册 RPC 或 handler、不配置或迁移 Site。

## 交付范围和稳定入口

| 新入口 | 用途与调用合同 |
|---|---|
| `ocr.schema.validate_invoice(payload, *, anchor_year)` | 返回独立的 `ThaiInvoice`，使用显式年份校正模型日期；年份传入所有 additional_invoices |
| `ocr.schema.invoice.ThaiInvoice / LineItem` | Pydantic v2 模型；保留全部 30 个发票字段、`model_fields`、`model_dump`，供页面与分组使用 |
| `ocr.schema.evidence` | `FieldRef/BoundingBox/Word/Paragraph/Block/Page/Layer1Result`，保留位置、词置信度、物理页号和表格文本证据 |
| `ocr.schema.results` | 发票所需 `Layer2PageResult/Layer2Result/Layer3PageResult/PipelinePageResult/PipelineResult` |
| `ocr.validation.check_amount_math(invoice, *, tolerance=Decimal("0.5"))` | 严格解析 subtotal/vat/total，宽松解析 discount；折扣前/后小计两种合法勾稽任一成立即通过 |
| `ocr.validation.evaluate_sanity(invoice)` | 金额结构、VAT 比例、税号、单行/行和及同页附加票的硬复核原因；只读输入 |
| `ocr.validation.infer_missing_discount(invoice)` | **明确修改传入对象**；仅差额与 7% 税基双证据成立才回填，并返回 `discount_inferred:` 留痕。调用方须传副本、保存首次证据、强制复核 |
| `ocr.validation.credit_note_review_reason(invoice)` | 贷项单方向必须人工确认；不依赖金额正负猜测方向 |
| `ocr.validation.validate_invoice_date(invoice, *, today)` | 显式日期锚点；未来超过 1 天、早于 5×365 天标记复核，不改字段 |
| `ocr.validation.validate_invoice_sources(invoice, *, today)` | 日期与金额 source_refs 来源列警告；不清空来源证据或金额 |
| `ocr.validation.evaluate_triggers(page, invoice, *, pattern_anomaly=None, amount_tolerance=Decimal("0.5"))` | 重读原因；pattern 只接调用方已计算的异常文本，不读取旧文件记忆 |
| `ocr.validation.evaluate_soft_flags(page, invoice, *, confidence_threshold=0.85)` | 已在 L1 文本出现但词置信度低、卖方税号格式异常等复核提示，不要求付费重读 |
| `ocr.validation.aggregate_page_confidence(page, triggers, needs_manual_review, soft_flags=None)` | 原 L1 均值/中性起点减触发及复核惩罚，返回 0–1 非金额 float |
| `ocr.validation.bucket_confidence(conf, needs_review, *, auto_threshold=0.98, review_threshold=0.90)` | `auto/yellow_confirm/needs_review`，明确的 needs_review 优先 |
| `ocr.validation.field_confidences(page, invoice)` | 关键字段的词置信度，缺 L1 文字来源为 0；短值没有词匹配时不伪造低置信 |

还导出 `check_field_in_l1_text/find_field_min_word_conf/count_invoice_no_candidates` 及 `line_sum_mismatch/vat_inclusive_lines/vat_ratio_mismatch`，便于编排层复用同一规则。低层金额辅助函数接收 Decimal；不要传 float 参与金额运算。

例：

```python
from copy import deepcopy
from datetime import date
from pearnly_cowork.ocr.schema import validate_invoice
from pearnly_cowork.ocr.validation import evaluate_sanity, validate_invoice_sources

# 这两个时钟值应在请求受理时由服务端按已核实的来源时区固定到工作单，
# 重放时读取同一值；不能从用户输入或每次 worker 的“今天”重新推导。
first_raw = deepcopy(provider_json)
invoice = validate_invoice(provider_json, anchor_year=2026)
reasons = evaluate_sanity(invoice)
warnings = validate_invoice_sources(invoice, today=date(2026, 9, 21))
```

解析器本身不保存 ai_raw；编排层在规范化/回填/重读前保留 `first_raw`，首次历史写入仍走已有首存契约。模型生成新值不等于已完成历史、文件关联或计费持久化。

## 来源对应与保留行为

固定源文件 `services/ocr/` 下 11 份文件的 SHA-256 写在 `tests/test_cowork_invoice_schema_source_parity.py:HASHES`。差分先逐文件验哈希，再 AST 提取选择的模型/函数；不 import 旧 App，不注入 sys.modules，不连接 Frappe/数据库。

| 来源 | 新落点 / 重要保留项 |
|---|---|
| `schemas_invoice.py` | `schema/invoice.py`；金额和数量保持字符串，null/数字/bool 的防御性转换保持；invoice_number/date/total 的空串归 None；未知 document_type→other，null→tax_invoice |
| `schemas_layer1.py` | `schema/evidence.py`；FieldRef 的 null 默认、0=未知来源页、4 点框和置信范围，完整 L1 层次 |
| `schemas_results.py` | `schema/results.py`；层链、触发/诊断、字段置信、token/耗时、方向与原 history 来源等发票页面元数据 |
| `validators.py` 发票相关定义 | `validation/sources.py`；四个钱字段不能从描述/凭证号/科目代码列取数；只保留发票使用的来源词集，不迁 GL/Bank 规则 |
| `sanity.py` + `sanity_multi.py` | `validation/sanity.py`；正负/税号/MOD-11/VAT/行和/同页多票规则；旧相互延迟导入合并为单一无环纯模块 |
| `date_sanity.py` | `validation/dates.py`；同一软日期窗口，非法或缺失 ISO 日期由其他规则负责，当前规则不发明额外报错 |
| `triggers.py` 发票函数 | `validation/gates.py` + `validation/math.py`；金额数学、关键字段缺失、L1 来源、漏票候选、词置信与降级分桶 |
| `confidence.py` | `validation/confidence.py`；去空白/逗号/分隔符/泰铢符，最小 4 字符重叠，词级最小置信度 |
| `page_runner.py:_field_confidences` | `validation/gates.py:field_confidences`，不迁 page_runner 的 IO/线程/模型编排 |
| `money.py` | 由同批金额/分组工作包提供 `ocr.money`；本包只复用 `normalize_money/normalize_id/valid_thai_tax_id` |

金额含义保持：`total_amount` 是折扣后净应付；`payment_received/cash_amount/change_amount` 不互相替代。`wht_rate/wht_amount` 独立，currency 非空外币标记不被清空。seller/buyer_branch、source_refs、additional_invoices 都保留。原 schema 实际允许更深的 additional_invoices，尽管旧说明要求子项为空；本批不擅自新增结构拒绝规则，但页面展开/同页 sanity 仍只消费来源规定的直接附加票层。

sanity 与重读不可混为一项：已自洽的主票即使有更多票号候选，原重读函数也不无条件付费重打；完整漏票告警仍要由后续编排承接。卖方税号格式异常主要是 soft flag；13 位但 MOD-11 不过由 sanity 硬复核。贷项单必须另外调用方向复核规则。组合函数返回“无原因”不能直接授权外部入账。

同页附加票逐张做核心 sanity，并对主票及附加票加双向行和校验。该双向分支保持旧规则：允许有金额证据的 VAT 内含行，不传 discount 打开更宽折扣豁免。不能因为看到旧注释“漏行合法”就移除多票页的反向检查。

## 明确的有意差异

1. **固定时钟**：旧 schema 每次调用 `date.today().year`，日期软闸默认 `date.today()`；新入口必须传 anchor_year/today。来源年份规则仍只改模型日期中的年，保留月日；不使用 history 人工编辑的完整日期算法。直接构造未带 context 的原始 ThaiInvoice 会拒绝，推荐始终使用 `schema.validate_invoice`。已验证模型携带非序列化的私有年份，可被结果包装复用；普通 JSON 无法伪造该私有值，包装时也不能原地更换年份。需要另一次显式解析时，公共入口生成独立副本，包括传入的已构造 FieldRef。
2. **不迁 ERP 分支**：旧 schema 的 ERP enabled 分支会用另一 printed_date 算法。Cowork 固定使用非 ERP 路径，源差分仅将该分支固定为关闭，不为其建立新的产品入口。
3. **Decimal 金额**：金额解析/运算不经 float；纯装饰器仅局部扩大 Decimal 精度，避免大数加减丢分，不改变进程全局 Decimal context。金额 warning 的未格式化数字可能保留原小数位，字符串不保证与旧 float 展示逐字一致；差分按完整有序原因文本屏蔽数值 token 比较，具体钱数另用精确断言。
4. **非法数值关闭**：旧 `float('NaN')` 及 Infinity 的比较可让数学闸返回无问题；新严格金额解析拒绝非有限值。超过 2^53 后旧 float 丢失的 1 泰铢差额，新闸能识别。这些改变有独立旧/新对照断言，未扩大误差容忍掩盖差异。
5. **显式配置**：旧置信阈值和金额容差在 import 时读 env；新函数由参数提供，缺省值与固定源默认一致。有效线上配置仍要由主任务读取并固定到 work 上下文，源码默认不代表生产实际值。pattern 异常也是显式值，不迁文件记忆依赖。
6. **限定发票切片**：结果的 business document_type 只允许 invoice/auto，document 只允许 None；银行、GL、VAT 报表等模型和校验未迁入。`PipelineResult.estimated_cost_thb` 使用有限非负 Decimal，JSON 输出字符串（旧为 float 数字）；金额 ledger 接线要使用 Decimal，不把该估算值当用户收费依据。

## 验证与剩余接线

在主任务已启动的专用隔离 lab 执行纯测试；本子任务没有启动/停止容器、安装依赖、init/connect/migrate Site 或调用外部系统。

- 目标 Python **3.14.7 / Pydantic 2.12.5**：本包 **28 个单测**通过；与同批金额/分组的 17 个单测合计 45 个通过。
- `test_cowork_invoice_schema_source_parity.py --source /tmp/cowork-invoice-source`：**980** 组模型/日期/结果包装对照通过，4 个显式年份锚点；raw payload 不变。
- `test_cowork_invoice_validation_source_parity.py`：**2500** 组 sanity、**2500** 组金额闸、**12** 组折扣推导、**56** 组日期/来源、**800** 组置信/重读决策对照通过；另有 **3** 组明确的 Decimal/非有限差异断言。
- 单测包括第一份证据隔离、同页额外票、VAT 内含票、低于/高于阈值、实际 0/缺失、非数字、13 位税号、贷项负数、巨大整数差额、时钟拒绝、目标结果枚举限制与 Decimal JSON 类型。
- 架构检查通过（0 violations）；未修改全局 Decimal context、模型环境配置或旧来源文件。
- 这些只证明纯合同和限定差分；模型质量、PDF/图像解码、文本 PDF 路由、few-shot、Vision/直读/L3、部分页恢复、供应商协议、用户计费及页面验收仍属于执行编排接线，未由本批验收。

调用方还必须把 `discount_inferred:`、日期/贷项/source_refs/sanity 原因合并入人工复核决策，并保留首次与重读差异；不能只运行 `check_amount_math` 就宣称可以自动入账。现有 `ocr.pages` 负责物理页展开与历史质量，业务质量 high/medium/low 与模型置信桶不是同一字段。
