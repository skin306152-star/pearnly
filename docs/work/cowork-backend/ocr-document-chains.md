# 银行单 / GL 文档级 OCR 第一层：文档模型与余额链修复（第三十六批）

第三十六批从新平台 `3325881` 开工，工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`。开工与交付前各独立采集一次线上来源，仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`（固定只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`）。不接前端、不部署、不改 DMS、不写旧库、不调模型。

用户本批明确决策：**银行单 / GL 的文档级 OCR 要原生承接**（"OCR 是产品的核心产物"），所以清点文档里登记为"待决策"的那处缺口转为开工。整条链分四层，本批只做第一层，顺序按依赖排：

1. **本批**：文档模型 + 余额链确定性修复（纯逻辑，后面三层都要用）；
2. 列来源校验（`validators.py` 的 GL/银行部分：金额只能来自金额列，说明/参考号里的数字不得当金额）；
3. 确定性读取器（Excel/CSV/文本直读、PDF 文字层重建）→ 已解码页；
4. 模型路径（银行/GL 提示词 + 引擎的文档档位 + 执行接线 + 整页升档重读）。

## 一、本批交付

| 新落点 | 来源（sha256 固定） | 内容 |
| --- | --- | --- |
| `ocr/schema/documents.py` | `services/ocr/schemas_documents.py` | `GLEntry` / `GeneralLedgerDocument` / `BankStatementEntry` / `BankStatementDocument` 四个模型、全部 `mode="before"` 兜底（None→""、数字→字符串、bool→字符串、`direction` 白名单）与 `FieldRef` 溯源字段 |
| `ocr/validation/chains.py` | `services/ocr/bank_chain_gate.py`（`23244676…`）、`services/ocr/gl_balance_chain.py`（`e59f82bd…`） | `repair_bank_chain`、`bank_chain_warnings`、`repair_gl_document` 与各自私有助手 |

模型的字段与描述逐条保留，包括 `BankStatementEntry` 上的三个审计标记 `chain_repaired` / `chain_amount_imputed` / `review_required`——钱路上自动改数必须留痕，这是旧设计的核心约束。

两个余额链修复器放在同一个模块，因为它们共用同一条解析口径（旧侧自己写着"与 `gl_balance_chain` 同口径,两链不劈叉"），分开写等于把同一条规则复制两遍。判定顺序原样保留（**先保真再纠偏**）：承转行放行 → 正常向核验 → 翻转向（唯一解）→ 链推定覆写（安全带内）→ 报首处断点。容差 `0.01` / `0.05`、覆写安全带 `20%` 都按旧值。

### 有意差异（都不改判定）

| 旧 | 新 | 理由 |
| --- | --- | --- |
| `_audit_warnings` | `bank_chain_warnings` | 调用方要用它产出页级复核警告，不再是私有助手；行为不变 |
| `_TOL`（两份文件各一个） | `BANK_TOL` / `GL_TOL` | 两个容差现在同处一个模块，同名会混 |
| GL 侧的 `_set_side` | `_set_gl_side` | 与银行的 `_flip_*` 助手同处一模块，避免歧义 |
| `logger.info("GL 行%d 借贷方向…")` | 不迁（返回的警告与行上的 `direction_autocorrected` 标记仍在） | 纯模块不持有 logger；自动改数的痕迹靠标记与返回值可见，不靠日志 |
| `bank_chain_gate_enabled()`（读 `OCR_BANK_CHAIN_GATE`） | 不迁 | 运行时开关归调用方决定，纯模块不读环境 |
| `reread_bank_page()`（整页升 max 重读一次） | 不迁（留到第 4 层） | 它需要引擎的"文档整页升档"臂与 `DirectReadFallback`，本平台还没有文档档位；带一半会得到一个没人调的壳 |
| `VatReportDocument` / `GenericTableDocument` | 本批不声明 | 它们的读取路径各自迁；先声明等于假装能收 |

## 二、验证与证据

- 离线用例（隔离 bench）：余额链 **29** 个（翻正、覆写两向、安全带内/外、双侧非零、两侧相等、承转跳变、缺期初、缺余额、不可解析、只报首处断点、警告文案、GL 方向纠正、页脚合计消歧、合计对不上、期初反推、期末回填、空文档）；文档模型 **11** 个（None/数字/bool 兜底、方向白名单、审计标记默认清、FieldRef 与 raw_row_data）；上一批的 magnitude **8** 个与 error_format **15** 个继续通过。
- 固定源差分（只执行被搬的纯定义，旧日志器用 no-op 替身）：
  - 银行链 **272** 批：断点 98、翻正 24、链推定覆写 32（全部同时置 `review_required`）、警告 130 条，差异 **0**；
  - GL 链 **266** 批：方向纠正 94、警告 133、期初反推 107、期末回填 146，差异 **0**；
  - 两侧逐字段比对（`deposit`/`withdrawal`/`amount`/`direction`/`balance`/三个标记，GL 另有 `debit`/`credit`/`raw_row_data` 与文档的期初/期末），不只比返回值。
- 架构 `scripts/check-architecture.py` **0** 违规（两个新模块都落在既有 `ocr` 域内，没有新增跨域方向）；`i18n-build --check` PASS，`check-i18n` REPORT-ONLY（链警告文案与既有 `sanity.py`/`parse` 类同档，按现状不登记）；`git diff --check` 干净。
- 顺手清理：上一批 `ocr/validation/magnitude.py` 与其单测的缩进由 tab 改为 4 空格，与本包其余文件一致（纯空白，行为不变；差分重跑仍是 521 批差异 0）。

## 三、下一批（仍在本决策内）

按第 2 层开工：`validators.py` 的 `validate_gl_document` / `validate_bank_document` 与它们的列名关键字表（`_DESCRIPTION_COLUMN_KEYWORDS`、`_is_allowed_*_amount_source`、`_value_appears_in_description`）——这是"6091 被当成金额"那类误读的确定性拦截，纯逻辑、可离线差分。之后是读取器与模型路径。

## 四、未做

- 未接前端、未部署、未推送、未合并主线；未动共享底座（`features.py`、`pearnly_core/**`、`messages.tsv`）。
- 未跑真实文件、未调模型、未跑队列；本批全部是纯逻辑与固定源差分。
- 未迁第 2/3/4 层（列来源校验、确定性读取器、模型路径），也未迁 `reread_bank_page` 的整页升档。
