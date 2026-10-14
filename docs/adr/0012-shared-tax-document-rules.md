# ADR-012 票种与业务处理：全平台一张决策表

- 状态：已采纳（2026-09-27，用户要求「基础的业务判断逻辑定死，不再发现一点修一点」，并要求 ERP 与 Cowork 协调落实）
- 协调：ERP 窗口（d45595）写 ADR 与共用模块并迁移两边调用点；Cowork 窗口（64f75b）确认期间不碰 `ocr/**`、`push/express/**`、`reconciliation/**`，合入后变基
- 实现：`apps/pearnly_core/pearnly_core/tax_documents/`（`codes` 码表、`rules` 决策表），回归样本 `tests/fixtures/tax-documents/cases.json`，测试 `tests/test_tax_documents.py`（进 Development checks）
- 关联：[ERP LINE 收单记录](../work/2026-09-25-erp-line-intake.md)「09-26 第六轮」、[架构](../development/architecture.md)

## 问题

识别面对的票据长相无穷，但每次出错的都是同一类基础判断：这张票能不能抵进项税、按含税全额记成本还是拆出 VAT、默认已付还是未付、要不要买方信息。盘点（2026-09-27）发现同一个概念有四套枚举、三种判法：

- Cowork `ocr/schema/invoice.py`：7 个票种；Express / MR.ERP 推送按旧线上 `judge_direction`（完整税票 + 印了 VAT + 写明买方才抵进项）。
- Cowork 第三轮重读提示词 `L3_SYSTEM` 只列 4 个票种（旧线上 `layer3_fallback.py:119` 原样），重读会把简式税票、转账凭证读回成 `tax_invoice` / `receipt`。
- ERP 采购识别：09-26 前完全不看票种，7-Eleven 简式税票被记成 122.43 + 可抵进项税 8.57；临时补丁只认 4 个票种、不查买方。
- 旧线上知识库「死规则引擎 22 条」用 `full_tax_invoice / abbreviated`，另一套拼法。

## 依据：法定票种有限

票种按泰国《税法典》定义，不按长相：

| 码 | 票种 | 法律依据 / ETDA ขมธอ. 3-2560 文件名 |
| --- | --- | --- |
| `tax_invoice` | 完整税票 | ม.86/4；ใบกำกับภาษี、ใบแจ้งหนี้/ใบกำกับภาษี、ใบเสร็จรับเงิน/ใบกำกับภาษี、ใบส่งของ/ใบกำกับภาษี |
| `simplified_tax_invoice` | 简式税票 | ม.86/6；进项税不得抵扣 ม.82/5；ใบเสร็จรับเงิน/ใบกำกับภาษีอย่างย่อ |
| `receipt` | 收据（非税票） | ม.105 ทวิ；ใบเสร็จรับเงิน |
| `credit_note` | 贷项通知单 | ม.86/10；ใบลดหนี้ |
| `payment_evidence` | 转账凭证 | 证明付款，不是税票 |
| `order_evidence` | 订单截图 | 不是税票 |
| `other` | 其他 / 认不出 | 含 ใบเพิ่มหนี้（ม.86/9）、50 ทวิ：本表不建模，交人工 |

ETDA 电子税票码表（T01–T05、388、80、81 等）与上面一一对应，接入电子税票时按码映射到这张表，不另立枚举。

## 决定

**三层：识别只抄 → 按事实定票种 → 查一张决策表。所有产品读同一份，认不出就交人工，不猜。**

1. **识别只抄票面事实**：标题原文（`document_title`）、买方税号 / 名称、票面 VAT。模型给的票种只作参考。
2. **定票种（`rules.classify`）**：标题里有法定票种名称就以标题为准（先窄后宽：อย่างย่อ / ABB → 简式；ใบลดหนี้ → 贷项；ใบเพิ่มหนี้ → 其他；ใบกำกับภาษี / TAX INVOICE → 完整税票；ใบเสร็จรับเงิน / RECEIPT → 收据），其次用模型给的，都没有就是 `other`。
3. **采购决策表（`rules.purchase_treatment`）**，口径照旧线上 `pearnly-app@ec2e5c95` `services/purchase/intake.py:38-82`：

| 票种 | 抵进项 | 记为 | 默认付款 | 要人看 |
| --- | --- | --- | --- | --- |
| 完整税票 + 印了 VAT + 写明买方 | 是 | 采购（进项票） | 未付 | — |
| 完整税票，缺 VAT 或缺买方 | 否，含税全额记成本 | 费用 | 未付 | 缺哪项 |
| 简式税票 / 收据 | 否，含税全额记成本 | 费用 | 已付 | — |
| 转账凭证 / 订单截图 | 否 | 费用 | 不猜 | — |
| 贷项通知单 | 否（不自动冲减） | 费用 | 未付 | 贷项通知单 |
| 其他 / 认不出 | 否 | 费用 | 不猜 | 票种不明 |

4. **销项不走这张表**：我方开出的简式税票同样要交销项税，按票面金额记；买方要求沿用各产品现状（Cowork 复核页简式税票 / 收据不强制买方税号）。
5. **改规则的顺序**：先在 `cases.json` 加一条真实原件或条款用例（只存识别出的事实，不存图片），再改 `codes` / `rules`，测试通过才算。产品代码里不再出现票种字符串判断。

## 本批落地（2026-09-27）

- 共用：`pearnly_core.tax_documents`（纯契约，只依赖标准库；`tests/test_tax_documents.py` 核对纯度、样本、与旧线上判据逐组合一致、各产品提示词不漂移）。
- Cowork：`push/express/field_rules.py` 的 `judge_direction` / `doc_type_payment_hint` 改为调用共用决策表（行为不变：`scripts/cowork-express-source-parity.py` 1013 个用例与旧线上一致）；`ocr/schema/invoice.py` 的票种枚举与说明取自码表（逐字不变，schema 对照 980 例通过）；`L3_SYSTEM` 票种补齐为 7 个（登记在 `tests/test_cowork_engine_source_parity.py` 的有意改动）。
- ERP：采购识别要 `document_title`、`buyer_name`，经 `classify` 定票种、经 `purchase_treatment` 定抵不抵进项；要人看的原因在卡片 / 网页红字显示，只标注、不触发换模型重读；卡片「单据类型」按码表显示。

## 补充：简式税票含税价拆税（2026-10-15）

问题：testcowork 教程录制（2026-10-13）里一张手写 ABB（票面「ABB / VAT INCLUDED」「รวมเงิน (รวมภาษีมูลค่าเพิ่มแล้ว) 727.00」，没单写税额）
识别成税前 727.00、税额 "0"，推送按 727.00 全额记收入，ภ.พ.30 少 47.56 销项税；同一本另三张模型自己拆对了。模型输出不稳，需要确定性规则。

决定：简式税票只能由增值税登记人开具，票面价依法含 VAT（ม.86/6），税率 7%。`rules.included_vat`：票种是简式税票、税额是 0 或没写、
税前额等于总额（或没写）→ 税额 = 总额 × 7/107（四舍五入到分），税前额 = 总额 − 税额。其余一律不动：已有税额、别的票种（完整税票缺
VAT 仍由采购决策表交人看）、外币、带预扣税、税前额与总额不同、票面文字写明免税 / 零税率（ยกเว้น、NON-VAT、VAT 0%、อัตราศูนย์ 等）。

- 销项、进项一样拆；进项拆出后抵不抵仍走各产品原有流程（Cowork：Express「小票 / 简式税票问一次抵不抵进项」，选「不抵」照旧含税全额记费用）。
- 只把数字规整对：不加识别后提示、不加确认步骤、不改复核状态。
- Cowork 在识别存档（`ocr.results.prepare_history_batch` 分组之后，`ocr.pages.split_included_vat`）逐页调用，原读数记进该页
  `_extraction_audit["vat_included"]`；不改识别引擎与 Cloud Run 镜像，人工复核改过的值不再动。
- 回归样本在 `cases.json` 的 `vat_included`；离线回放 `scripts/cowork-vat-included-replay.py`（只读）在已存识别结果上列出会改的票。

## 未做（下一批，按顺序）

1. Cowork 识别也抄 `document_title` 并在 schema 校验里套 `classify`：要改 L2 提示词（登记对照）并用真实原件付费实测，未在本批做。
2. 旧线上「死规则 22 条」的其余检查（税号校验、查重、必填项、客户规则）按同样方式收进共用层；ERP `recognition/checks.py` 与 Cowork `ocr/validation` 目前各有一部分。
3. 对账：简式税票 / 个人买方没有税号时第二轮配对永远配不上（`reconciliation/matching.py:47-55`），按票种给配对键。
4. 贷项 / 增项通知单在 ERP 的建模（负数、关联原票）。
5. 含税判定现有三份实现（Cowork `sanity.vat_inclusive_lines`、ERP `checks.vat_mode`、MR.ERP `fmt.mrerp_sales_tax_label`），容差不一，收成一份。
6. 回归样本按真机原件持续补充；ERP 采购单尚未持久化票种（`has_vat` 已表达是否抵进项）。
