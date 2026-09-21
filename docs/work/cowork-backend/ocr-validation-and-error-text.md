# OCR 族的两个确定性缺口与架构归位（第三十五批）

第三十五批从新平台 `412d9fd963659f698affd096138acc8843fc47f9` 开工，工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`。父任务在开工与交付前两次独立采集线上来源，仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`（固定只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`）。本批不接前端、不部署、不改 DMS、不写旧库。

本批做三件事：补 OCR 族清点认定的两个真缺口（供应商量级基线、单行错误文案），并把 Cowork 的域依赖方向登记进架构检查。清点与逐条对应见 [OCR 族缺口清点](ocr-family-audit.md)。

## 一、供应商历史量级基线

| 项 | 内容 |
| --- | --- |
| 来源 | `services/ocr/magnitude_baseline.py`（sha256 `94c17604…`，47 行） |
| 落点 | `apps/pearnly_cowork/pearnly_cowork/ocr/validation/magnitude.py` |

保留的判定：只揪不改值；`RATIO=20.0`、`MIN_HISTORY=5` 可被调用方覆盖；有效历史取中位数而非均值；非正数/非数字/缺失总额一律不判；`True` 被当正数的既有类型怪癖照原样保留。中文原因串与阈值格式（`>{ratio:g}×`、`{med:.2f}`）逐字保留。

有意差异只有两处，都不改判定：模块 docstring 改写为迁移说明；模块名去掉 `_baseline` 后缀以匹配 `ocr/validation/` 的命名。**部署版从未接线**（其注释自述"按 seller_tax 取近 N 张正 total_amount"的查询未提交），原生侧同样只提供决策核心，历史来源由调用方给出——本批不发明历史查询，也不把未接线的规则写成已生效。

## 二、单行错误文案

| 项 | 内容 |
| --- | --- |
| 来源 | `services/ocr/error_format.py`（sha256 `8774b6f4…`，78 行） |
| 落点 | `apps/pearnly_cowork/pearnly_cowork/contracts/errors.py` |
| 接回的落点 | `reconciliation/income_parse.py` 的两个 `error` 字段（`excel_rows`、`text_rows`） |

保留的行为：`ValidationError` 走字段摘要（丢首个模型位置、字段去重、列前三个 + `…(+N)`、无字段时"字段校验失败 · 请确认文件格式与表头是否完整"）；其它异常只留首行、去掉 pydantic 文档 URL、超过 160 字符截断加 `…`、空文案回落类名。`pydantic` 仍是函数内导入，模块本身不依赖它。

**落点选择**：部署版把它放在 `services/ocr/` 下，但它的使用者是银行/GL/VAT 的**解析入口**（`_short_err` 四处）。原生对账不会为了一个文案依赖抽取域，所以放进根 `contracts/` 纯契约域，两侧都能用而不互相依赖；这也顺带把 `reconciliation → contracts` 登记为新方向（见第三节）。

**接回的方式与范围**：部署版 `error: _short_err(e)` 出现在**文件级**入口（打开文件/调管线的那一层），原生没有这一层，对应位置是"调用方交来的内容解码失败"的两个 `error` 字段，此前写的是 `str(error)`（原始异常全文）。本批把它们改回部署版文案。诊断字段 `diag.exception` 继续留原文——它不进用户可见的 `error`，是给排障用的原生新增字段。

## 三、架构归位（用户本批要求）

1. `contracts/errors.py` 是新的共享纯契约条目；新增方向 `reconciliation → contracts` 写入 `architecture-policy.json` 并在文档里说明理由（对账要部署版同形文案，且不能依赖 `ocr`）。
2. 新增 `cowork_domain_dependencies`（13 个域）+ `scripts/check-architecture.py` 的 Cowork 分支：未登记的新方向直接失败，与 DMS 同一机制、同一共享钩子。已登记方向按当前代码实测生成，不是手写猜测。
3. 既有环如实登记为债务：`ocr ↔ roundtrip`、`files ↔ ocr`、`history ↔ ocr`。文档同时写明"登记 ≠ 方向正确"，改到这些链时要拆开。
4. `docs/development/architecture.md` 新增"Cowork 的实际职责"一节（域职责表 + 债 + 框架模块目录不进域依赖表的原因）。

## 四、验证与证据

- 离线：`magnitude` **8** 个用例、`errors` **15** 个用例（含 pydantic 真实 `ValidationError`：嵌套 `loc` 丢索引、>3 字段溢出、`model_validate_json` 的 JSON 错误）。主机（无 pydantic）跑纯逻辑 13 个通过、2 个按设计跳过；隔离 bench 里 15 个全过。
- 固定源差分（两个都跑在 bench 内，用 `--source-stdin` 管道喂固定源）：
  - `magnitude`：**521** 批（低报 67、高报 28、撤回 426）差异 **0**；
  - `error_format`：普通异常 24、真实 pydantic 错误 7、包装后 14、`_fmt_validation` 替身 14、两个常量直接比对，差异 **0**；覆盖含截断 5、类名回落 6、字段摘要 7、空错误兜底 2、`__str__` 抛错传播 1。
- 既有用例未回归：`income_parse`/`vat_parse`/契约 **96** 个继续通过；`income_parse` 的固定源差分 **305** 批（161/172/156/70）差异 0（它内部的 `_short_err` 替身已改为真实迁移函数，说明见下）。
- 隔离 Site（`cowork-backend-ack.localhost`、`cowork-backend-preparation.localhost`）：收入对账存储 **19** 用例/Site 通过，匿名 RPC 全部 403。
- 架构：`scripts/check-architecture.py` **0** 违规；新增的守卫用例证明"未登记的 Cowork 跨域依赖会失败、登记后通过"。`i18n-build --check` PASS，`check-i18n` REPORT-ONLY（新文案与既有 `sanity.py`/`vat_parse.py` 同类，按现状不登记 `keep-cjk.tsv`），`git diff --check` 干净。
- 线上：`baseline-thirty-fifth-delivery.json`（`40ae6777…`）与第三十四批快照 `compare` **PASS**；运行投影 `push-runtime-thirty-fifth-delivery.json`（`201bb3bf…`）与上一批逐项相同（端点摘要仍 `c5882f4b…`，`settings_unchanged`、`observations_changed` 均无变化）。

## 五、有意差异与限制

- `_short_err` 在 `tests/test_cowork_income_parse_parity.py` 里原本是 `str(error)` 替身（当时该函数未迁）。现在替身指向迁移后的真实函数，因为部署版调的就是它；文案本身的逐字等价由 `test_cowork_ocr_error_format_source_parity.py` 对固定源单独保证，不靠这个替身。
- 未接线的东西仍标未接线：量级基线没有历史来源（部署版也没有）；`bank_chain_gate`/`gl_balance_chain` 明确不属本切片，缺口清单已登记，等用户对"是否原生承接银行单/GL 文档 OCR"给方向。
- 只跑 SYNTHETIC 输入与隔离 Site；没有真实文件、真实模型、真实 ERP 写入、前端、部署或用户验收。

## 六、未做

- 未改 DMS、未接前端、未部署、未推送、未合并主线。
- 未跑异步队列、未调供应商、未动 `pearnly_control/features.py`、`pearnly_core/**`、`scripts/i18n/messages.tsv` 这三个共享底座文件。
- 试过把 `main`（`2b4cf18`）合入本分支以确认组合无冲突：`apps/pearnly_core/pearnly_core/desk/navigation.py` 与 `scripts/i18n/messages.tsv` 冲突（另有 4 个生成的四语 CSV 双侧改动），**已中止合并、未改任何一侧文件**。两处都属共享底座与另一窗口在改的范围，留给拥有它们的窗口；合并时须注意 `main` 侧边栏重写仍保留 Cowork 的 `boards` 菜单项，而本分支已按要求删除。
