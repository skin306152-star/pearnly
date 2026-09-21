# Cowork 后端迁移：对账族缺口清点（只读，2026-09-21）

只读清点 `services/recon/**`（56 个文件）、`services/recon_jobs/**`（6 个）与对账用到的 `services/vat/**`，回答一个问题：**这一族还剩什么真缺口，哪些是"有意不迁"，哪些根本不属于本族。** 结论先写：真缺口只剩一处（银行 run 的业务警告与锚点快照），而**异步任务与轮询不需要原生副本**——它存在的理由在新平台已经由别的族承担。本文不改代码、不写库，来源为固定只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。

## 一、已迁（有落点与批次）

| 来源 | 落点 | 批次 |
| --- | --- | --- |
| `bank_recon_types.py`、`bank_recon_match.py` | `reconciliation/bank_matching.py` | 第二十/二十一批 |
| `bank_recon_scoring.py` | `reconciliation/bank_scoring.py` | 第二十一批 |
| `bank_recon_utils.py`、`bank_recon_pipeline.py`、`bank_stmt_legacy.gl_rows_from_pipeline_legacy` | `reconciliation/bank_statement.py`、`bank_parse.py` | 第二十一/二十三批 |
| `bank_recon_excel*.py`（5 个） | `reconciliation/export/`（`workbook`/`summary`/`labels`/`usage`/`styles`） | 第二十四批 |
| `field_comparator.py` | `reconciliation/normalization.py` | 更早 |
| `gl_vat_types.py`、`gl_vat_reconcile.py` | `reconciliation/income_vat.py` | 第二十五批 |
| `gl_vat_parse_common.py`、`gl_vat_parse_excel.py`、`gl_vat_parse_pdf.py` | `reconciliation/income_parse.py` | 第二十七批 |
| `vat_parser_common.py`、`vat_parser_excel.py`、`vat_parser_pdf.py`、`vat_parser_csv.py`、`vat_report_parser.py`（转换半段） | `reconciliation/vat_parse.py` | 第二十八批 |
| `gl_vat_excel.py` | `reconciliation/export/income_workbook.py` | 第三十批 |
| `gl_vat_store.py`、`bank_recon_v2_store.py`、`vat_recon_store.py` 的**存取语义** | 原生任务/行 DocType + `recon_tasks` | 第二十一/二十六/二十九批 |

## 二、有意不迁（有理由，非遗漏）

| 来源 | 理由 |
| --- | --- |
| `bank_stmt_xlsx.py`、`bank_stmt_text.py`、`bank_stmt_extract.py`、`bank_stmt_balance.py`、`bank_gl_excel.py`、`bank_gl_pdf.py`、`bank_gl_pdf_mrerp.py`、`bank_gl_stacked.py`、`bank_table_io.py` | 打开文件与抽表/抽文字本身：新平台的边界是"调用方交出已抽取内容"，这些留旧侧（`bank_table_io` 还被 `fileconv`/`summary_import`/`steward` 复用） |
| `bank_stmt_gemini.py`、`bank_gl_gemini.py`、`enterprise_adapter.py`、`vat_parser_gemini.py`、`vat_ocr_batch.py`、`vat_ocr_extract.py`、`vat_file_classifier.py` | 模型调用与 OCR 编排：属 OCR/抽取族的引擎档（`services/ocr/**`），随该族核对 |
| `bank_stmt_reread.py`（换眼重读）、`bank_recon_merge.py`、`bank_recon_serialize.py`、`bank_recon_facades.py`、`bank_arbitration_guard.py`、`bank_recon_v1_store.py`、`gl_vat_reconciler.py`、`gl_vat_serialize.py` | 旧内部件/旧代（v1 存储）；被原生服务与原生序列化取代，外部调用点只在本族内部 |
| `bank_recon_v2.py`、`gl_vat_parse_excel.py:parse_gl`、`_parse_gl_impl`、`_parse_gl_via_pipeline`、`vat_report_parser.py` 的 facade/分发 | 按扩展名分发 + 编排；原生改为按调用方声明的 `kind` 分发，回退责任移到调用方 |
| `vat_recon_schema.py`、`vat_recon_tasks_store.py`、`vat_recon_core.py`、`reconciliation_matcher.py`、`recon_resolve.py` | 第一代（M2 发票存档 ↔ 报告行）与通用 V1 对账：**不是**总账对销项税这一族（第二十六批已登记）；`reconciliation_matcher` 仍被 `routes/recon_routes.py` 与 `vat_excel_export` 使用，属 M2 |
| `services/vat/vat_excel_build.py`、`vat_excel_export(er).py`、`vat_excel_*`、`vat_report_checks.py`、`vat_report_merge.py`、`vat_ai_analyzer.py` | M2 销项税 Excel 交付族（不是对账族） |
| `workorder_recon_adapter.py` | 工单对账族（`services/workorder/**`）的适配器 |

## 三、异步任务与轮询：**不需要原生副本**（附证据）

旧链：`POST /api/recon/gl-vat/submit`（`routes/recon_jobs_routes.py`）→ 存暂存文件 + 计费预检 → `store.enqueue("glvat", …)`；`services/recon_jobs/worker.py` 跑 handler，状态 `queued → running → done / needs_review / needs_mapping / failed`（带 `attempts < max_attempts` 重试）；`GET /api/recon/jobs/{job_id}` 轮询返回 `status/progress/review/mapping/result_table/result_id`。

它存在的原因是**解析慢**：要在 worker 里跑 OCR/Gemini，并且要在"OCR 行需要人工核对"（`needs_review`）与"表格列认不出、要人工确认列对应"（`needs_mapping`）时暂停等用户。新平台把这两件事都换了承担者：

- 抽取（读文件、OCR、模型）已经是迁过来的 OCR 执行族：批次、队列、轮询、失败重试、计费都在 `apps/pearnly_cowork/pearnly_cowork/ocr/execution/**`，`hooks.py` 里 `pearnly_ocr_handlers = {"cowork.invoice": …}` 就是它的队列入口。
- 对账（两侧解码 + 配对 + 落库）**本地同步**完成：`income_recon_service.create_from_parsed` 一次事务跑完，没有模型调用，也没有可暂停的人工态。

所以"给原生对账再建一套 job 表 + 队列 + 轮询"会复制已经存在的机制，还会引入新平台不需要的状态机（`needs_review`/`needs_mapping` 是抽取侧的状态）。**本族不做异步副本**；如果前端将来要"提交后轮询一次对账结果"，那是把 OCR 执行族的状态与同步对账调用组合起来的产品交互，不是迁移件——需要时按产品需求单独设计。

## 四、我上一批提到的"计费"，指的是旧产品的按次扣费，不是新产品的订阅/套餐

旧链里的计费只有两处（`routes/recon_routes_shared.py`）：

- `precheck_upload_billing`：读**旧产品**的账单状态（`db.get_billing_status_combined`），查不到就 503 fail-closed，余额盖不住就 402，豁免账号直接放行；
- `schedule_parse_charges` → `db.charge_ocr_async`：按 **PDF 页数 / Excel 字符数**事后扣费（fire-and-forget，失败只记日志）。

这是"按解析量扣额度"，属旧 monolith 的 billing 模块；**新产品这边已经有了对应实现**，不需要从旧侧搬：

- `pearnly_core.billing_client`（`status(kind, units, tier)` / `charge(kind, units, …)`）；
- Cowork 的 OCR 执行把这条链走通了：`ocr/execution/service.py` 落库时冻结计费口径（`billing_kind`/`billing_units`/`billing_tier`/`billing_reference`、`billing_state="not_due"`）→ 完成后 `billing_client.charge(...)` → 记 `billing_receipt`、状态 `charged`/`pending`，并留 `retry_billing` 重试入口；控制面在 `pearnly_control`（`billing*.py`）+ `pearnly_core.payments`。

结论：订阅/套餐/登录/OCR 订阅这些确实在新产品侧开发且已具备；旧侧的计费钩子映射到新平台的计量，**迁移时不用搬旧 billing 模块**。对账族本身也不额外计费（它不烧 OCR 额度，读的是已经抽取好的内容）。

## 五、真缺口：一处

**银行 run 的业务警告与锚点快照/覆盖**（`bank_recon_warnings.py`、`routes/recon_routes_bankv2_run.py`、`services/recon_jobs/bank_handler.py`）：

- `detect_recon_mismatch` + `brv2_warn`（期间不重叠 / GL 行数过少 / 无任何匹配 / 多账户 / 完整性）→ 存进 `summary_json._brv2_warnings`；
- `apply_anchor_overrides` → `summary_json._anchor_ocr`（OCR 期初/期末快照，供前端预填）与 `_anchor_overrides`（实际采用的人工覆盖值）。

这三个键由**run 编排**（上传 → 解析 → 锚点处理）产出，而新平台把 run 编排留在调用方（原生 `create` 收已解码行 + 表头）。第二十四批已把工作簿的三段渲染能力迁好（`workbook(...)` 支持 `anchor_overrides`/`anchor_ocr`/`warnings`，差分覆盖了传入时的逐格一致），缺的只是**存储与写入**：原生银行任务还没有这三项字段，所以导出的工作簿不显示这些段落。

`field_overrides`（`services/recon/field_override.py` + `db_migrations/field_overrides.py`）**不算本族缺口**：唯一写入方是 `routes/recon_routes.py` 的 M2 发票侧校正（写 `reconciliation_row`），迁移给 M3 `gl_vat_task`/M4 `bank_recon_v2_task` 加的同名列**在部署版没有任何写入点**（alembic 注释自己写着 M4 仍以 `summary_json._anchor_overrides` 为单源），收入工作簿也不渲染覆盖痕迹。银行工作簿"手动录入痕迹"段显示的正是上面第五节的 `_anchor_overrides`。

## 六、建议的后续顺序

1. **本族最后一件代码活**：给原生银行任务补 `warnings_json` 与锚点快照/覆盖的存储与写入形状（与 `_brv2_warnings`/`_anchor_ocr`/`_anchor_overrides` 同键），并让 `bank_recon_service.export` 把它们传给工作簿——工作簿侧渲染已在第二十四批就绪，差分里也覆盖了。
2. 其余按族推进（OCR/抽取族、M2 销项税 Excel 族、工单对账族）各自清点，不在本族内硬塞。

## 来源核对

只读批次不改代码，但仍独立采集了一次交付来源：`evidence/baseline-thirty-second-audit.json`，与第三十一批交付快照 `compare` **PASS**，`source` 仍 `ec2e5c957e6b…`。

## 未做

- 未改任何代码、未写库、未跑 Site（本文是只读清点）。
- 未核：`services/ocr/**` 引擎族与 M2/工单族的逐件对应——那两族的清点各自单独做。
