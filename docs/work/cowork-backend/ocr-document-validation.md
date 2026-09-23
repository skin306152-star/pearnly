# 银行单 / GL 文档级 OCR 第二层：列来源校验（第三十七批）

第三十七批从新平台 `625c3d0` 开工，工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`。旧来源仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`（固定只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`）。不接前端、不部署、不改 DMS、不写旧库、不调模型。

这是[四层计划](ocr-document-chains.md)的第 ② 层：拦掉"印在说明/凭证号/科目列里的数字被当成金额"的那一类误读。旧侧就是 `services/ocr/validators.py`（443 行），新侧此前只迁了**发票**那一半（`ocr/validation/sources.py` 里的三个关键字表与 `validate_invoice_sources`），本批把**总账与银行对账单**那一半补齐。

## 一、这一层在挡什么

旧文件开头自己写着：这个模块存在的理由，是拦住"GL 里 6091 这种说明文字被解析成金额"。做法是先看这笔金额**是从哪一列读出来的**（模型要给出溯源 `FieldRef.source_column`），再决定：

1. 来源列是说明/凭证号/科目代码 → 拒绝，并**把那个值清掉**（下游就不可能再用错数）；
2. 来源列不是该金额该来的那一列（借/贷/余额、存/取/余额）→ 报出来但不动值；
3. 金额同时以**独立数字**出现在说明里（且不在凭证号/科目代码里）→ 报"疑似取错列"；
4. 借贷（存取）互斥、方向与借贷一致、推导出的金额与借贷一致 → 各自单独报。

关键字表是**多语言**的（英/泰/中），借贷列也是（`debit`/`เดบิต`/`借方`…）。本批把三个文档类型**共用同一套表**照搬过来，和旧文件的做法一致——因为"哪一列是说明列"这件事三个类型只有一个答案。

## 二、本批交付

| 内容 | 来源 | 落点 |
| --- | --- | --- |
| 借贷/余额、存/取/余额的允许列名表（多语言） | `validators.py` 的 `_GL_*` / `_BANK_*` | `ocr/validation/sources.py` |
| `_is_allowed_gl_amount_source`、`_is_allowed_bank_amount_source` | 同上 | 同上 |
| `_value_appears_in_description`（独立数字匹配 + 去尾零重试） | 同上 | 同上 |
| `validate_gl_document` + `_clear_amount_field`（R1–R5b） | 同上 | 同上 |
| `validate_bank_document` + `_clear_bank_amount_field` | 同上 | 同上 |

### 有意差异（都不改判定）

| 旧 | 新 | 理由 |
| --- | --- | --- |
| `_AMOUNT_TOKEN_RE`（正则常量） | **不迁** | 在旧项目里定义了但**从未被任何代码使用**（全仓检索过），没有规则要保留 |
| `validate_gl_document(doc, page)` / `validate_bank_document(doc, page)` 的 `page` | 保留形参，文档说明"今天的规则只读行内溯源" | 部署版三个校验器都用不到 `page`（静态检查确认）；保留形状是为了第 ④ 层接线时不用改签名，页级包含性检查在 `confidence` 里另有一条规则 |
| `validate_invoice(inv, page)`（旧） | 新侧是 `validate_invoice_sources(inv, *, today)`（第二/三批已迁） | 新侧把"今天"从运行时时钟改成显式入参（本平台的既有规矩），行为其余相同 |
| `_is_allowed_*` 的文档串 | 写成中文注释 | 只改注释，判定与返回串一字未动 |

## 三、验证与证据

- 离线用例（隔离 bench）**31** 个：`_value_appears_in_description` 的独立数字/长数字不误伤/去尾零/去逗号（含"只对值去逗号、说明原文不动"这条旧怪癖）、三种关键字表（含泰文/中文列名与尾随标点）、借贷列归属、**6091 真实事故复现**、拒绝后**值被清掉且方向/金额一起清**、余额被拒不动方向、错列只报不清值、借贷互斥、非数值、方向一致性、推导金额不符、行号回落、干净文档零告警，银行侧对应各条（含"银行侧吞掉非数值"这条与 GL 不同之处）。
- 固定源差分（只执行被搬的纯定义；旧函数的注解类型与本平台模型同名）：**GL 413 批 + 银行 407 批，差异 0**。除了返回值，还逐字段比对**行状态**（拒绝是否清值、三个 ref 是否置空）。关键字表/列名表/`_norm_col`/两个 `_is_allowed_*`/`_value_appears_in_description` 另做了**逐一等值比对**，不是"看起来一样"。
- 同族回归（隔离 bench）：文档模型、余额链、量级基线、单行错误、发票校验与 schema 共 **122** 个用例通过。
- 静态检查：架构 **0** 违规；`i18n-build --check` PASS；`check-i18n` REPORT-ONLY；`git diff --check` 干净。

### 线上来源

交付时独立采集一次：`evidence/baseline-thirty-seventh-delivery.json`（`0c854389…`，采集 17:02:57Z）与第三十六批快照 `compare` **PASS**，`source` 仍 `ec2e5c957e6b…`；运行投影 `push-runtime-thirty-seventh-delivery.json`（`a202b10d…`）与上一批**逐项相同**（端点摘要仍 `c5882f4b…`）。本批是纯规则，但按迁移规矩照样复核，不因为"没接存储"就跳过。**完整配置与旧状态迁入继续 HOLD**。

## 四、有意不迁（仍留在旧侧，属第 ③/④ 层的输入）

- 旧 `validate_invoice(inv, page)` 的调用点（`page_runner.py` 的编排）——发票侧本平台走 `validation/` 里那套纯函数 + `engine/runner.py`；
- `pipeline.py` / `page_runner.py` 里"校验结果驱动置信分桶与 needs_review"的那层编排——归第 ④ 层（模型路径 + 执行接线）。

## 五、未做

- 未把这两个校验器接到任何调用方：第 ③ 层（确定性读取器产出已解码页）与第 ④ 层（模型路径）还没落，本批只交纯规则；不为了"看起来接上了"造一个没人用的壳。
- 未跑真实文件、未调模型、未接前端、未部署、未推送、未合并主线。
