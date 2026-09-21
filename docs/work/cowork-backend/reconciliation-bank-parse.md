# Cowork 后端迁移：银行对账受控解析适配（第二十三批）

日期：2026-09-21。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，本批基线 `a76c742`（第二十二批交付）。

本包把旧项目**已经解码**的银行对账页面（`services/ocr/pipeline` 的输出字典）按部署版转换规则落成同样的流水行/总账行，并给原生存储加一个受控入口把解析结果存进去。文件读取、OCR 调用与模型抽取仍在旧项目；没有新增 DocType、数据库迁移、队列、前端或部署。

## 来源清点：为什么是这几块

上一批的原生存储只收"已解码的行"；解析出来的页面要进原生存储，缺的就是这一层纯转换。按依赖分三类：

| 来源模块 | 性质 | 本批处置 |
| --- | --- | --- |
| `services/recon/bank_recon_pipeline.py` | 适配器：解码后的 entries/pages → `StatementRow`/`GlRow`。同文件的 `_parse_bank_stmt_via_pipeline`/`_parse_gl_via_pipeline` 前半段是文件格式分派、OCR/引擎策略与模型降级 | **迁入解码后的转换部分**；文件与模型那半边留在旧项目 |
| `services/recon/bank_stmt_legacy.py:gl_rows_from_pipeline_legacy` | 纯转换：GL pages → `GlRow` | **迁入** |
| `services/recon/bank_recon_utils.py` 的 `_to_float`/`_parse_date`/`_BANK_SIGNATURES`/`_detect_bank` | 纯原语；同文件还带内存 + 磁盘模型缓存 | **只复现需要的纯函数与常量**，不整块搬 |
| `core/thai_date.py` 的月份名正典与 `printed_month_map` | 纯查表；`_parse_date` 的印刷月份分支依赖它 | **只迁本次需要的三张月份表与装配顺序** |
| `services/recon/bank_stmt_xlsx.py`、`bank_stmt_extract.py`、`bank_recon_v2.py`（PDF 文本/列/坐标/Gemini 兜底链）、`bank_stmt_reread.py`、`bank_recon_merge.py` | 文件读取、OCR/模型、多文件合并与换眼重读编排 | 未迁，仍在旧项目 |
| `services/recon/bank_recon_v2_store.py`、`routes/recon_jobs_routes.py` | 旧任务存储与轮询 | 未迁；新平台用自己的 DocType |

`statement_rows_from_entries` 是旧仓"管线适配路"与"断链换眼重读路"（`bank_stmt_reread`）共用的**单一转换事实源**，迁它是为了让将来的重读结果也能走同一条入账口径，而不是重写一套。

## 来源与边界

开工与交付均重新采集线上来源：开工 `baseline-twenty-third-start.json`（sha256 `709693c7519e41b681b2550b6129dc2835ed76efb57d3c72843115629ad6acbe`，`2026-09-21T11:50:42Z`），交付 `baseline-twenty-third-delivery.json`（sha256 `8b56503f3a9883e5051804af7c473dfeef5ae58a53365423594dd63baf75f1b6`，`2026-09-21T12:00:12Z`），`compare` PASS，源码仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。

- 所属 App：`pearnly_cowork`；域：`reconciliation`。新模块只依赖本域的 `bank_statement`（行类型与 `finalize_rows`）、`bank_matching`（`GlRow`）与标准库，没有新增跨域方向。
- 纯函数不读文件、不调用模型、不写 ERP/数据库/时钟。入账入口 `create_from_parsed` 走既有 `bank_recon_service.create`，权限、请求键、幂等与匹配持久化完全不改。

固定来源只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`；差分脚本先校验 SHA-256，再只执行选定的纯函数、数据类与常量 AST。

| 文件 | SHA-256 |
| --- | --- |
| `services/recon/bank_recon_pipeline.py` | `c838888c6b9c7a107a1631ffdbf73a569ffc6288f20f71988480d35c484a5716` |
| `services/recon/bank_stmt_legacy.py` | `7af6c4130f2ddb4cf5bfa38658d8d9f2b1941d9256f684a874c4317e6b788c7e` |
| `core/thai_date.py` | `0e76c92f63f88a02cc82d223ea3ca56f4a7c999b616a00f1eae768e555fed987` |

| 固定来源 | 新落点 | 保持的规则 |
| --- | --- | --- |
| `bank_recon_utils.py:_to_float` | `bank_parse.to_float` | 去千分位/普通空格/NBSP、括号与前置负号为负、"–/—/-" 当零、泰式点号当千分位（多点只留最后一点的右侧）、`round(float, 2)` 与异常兜底 0.0 |
| `bank_recon_utils.py:_parse_date` | `bank_parse.parse_date` | datetime/date 直通（≥2500 减 543）、先按空格或 T 截断时分秒、印刷月份按插入序子串扫描、佛历短年（≥43 加 1957，否则加 2000）、`yyyy-mm-dd` 与 `dd-mm-yyyy/yy` 两条数字路 |
| `bank_recon_utils.py:_BANK_SIGNATURES`、`_detect_bank` | `bank_parse.BANK_SIGNATURES`、`detect_bank` | 七家银行的关键字表与顺序完全一致；全小写子串命中，未命中回 `generic` |
| `bank_recon_pipeline.py:statement_rows_from_entries` | `bank_parse.statement_rows_from_entries` | 两侧都为 0 的无动行跳过；日期优先印刷原文、回退拆分归一化日期；`description` 缺省空串；`source_file` 记传入文件名 |
| `bank_recon_pipeline.py:_parse_bank_stmt_via_pipeline` 解码之后的一半 | `bank_parse.statement_from_pipeline` | 无页即 `ok: False`；银行码取首页 `bank_name`；期初取文档字段并把 `finalize_rows` 按部署顺序跑完（过滤汇总行 → 方向纠正 → 逐行核对 → 金额反推）；期末优先文档字段，为 0 时回退最后一条非零余额；`balance_warn_count`/`amount_fixed_count`/`parser_version`/`needs_review` 口径一致 |
| `bank_stmt_legacy.py:gl_rows_from_pipeline_legacy` | `bank_parse.gl_rows_from_pages` | 逐页读取；借贷都为 0 的行跳过；`doc_no` 取 `voucher_no`；归一化日期能拆就用拆分结果，拆不动才回印刷原文；`source_file` 留空 |
| `bank_recon_pipeline.py:_parse_gl_via_pipeline` 解码之后的一半 | `bank_parse.gl_from_pipeline` | 可选账套前缀精确过滤、`accounts` 去重排序、`parser_version` 与 `needs_review` 透传 |
| `core/thai_date.py` 的月份表与 `printed_month_map` | `bank_parse` 的三张月份表 + `PRINTED_MONTHS` | 全称泰文、带尾点缩写、英文 3 字缩写三张表逐项相同；装配顺序（泰全称 → 泰缩写 → 英文缩写）与键序一致；差分用部署版 `printed_month_map(th_abbr="dotted_trailing", en_abbr=True)` 直接比对键序与内容 |
| `bank_recon_types.py:GlRow.__post_init__`（第二十批漏项） | `bank_matching.GlRow.__post_init__` | 用同一条 `date|doc_no|account_code|debit:.2f|credit:.2f` 的 MD5 前 12 位做行指纹 |

入账入口：`bank_parse.decoded_statement_rows`/`decoded_gl_rows` 把行写成原生"解码行"契约（`bank_recon_contracts`），`bank_recon_service.create_from_parsed` 用它们调用既有 `create`，`source_format` 记为 `pipeline_pages_v1`（解码行走 `decoded_rows_v1`）。调用方传的 `header` 覆盖从对账单读出的期初/期末/银行码，`gl_opening`/`gl_closing` 仍由调用方给（旧侧这两个值来自未迁的多文件合并编排）。新增 RPC `bank_recon_api.create_from_parsed` 与 `create` 同权限、同事务重试封装。

## 登记的有意差异

- **入账比旧匹配闸更严**：原生入口沿用第二十一批的"解码行"契约，所以解析行里没有描述、movement 金额为负（例如把 `(100)` 读成负的取款）、或整批超过 `MAX_ROWS` 时是**报错**而不是照旧参与匹配。真实对账单的行都有描述与非负金额，这里是把旧侧会静默带过的病态输入挡在门外。
- **未读余额的表达**：`to_float` 把缺失余额读成 `0.0`，所以解析路的行在原生存储里是"已知 0"；解码行契约能表达"未知（`None`）"。旧匹配本身就按假值处理 0.0 余额，结果一致，只有 `stmt_balance_known` 的注记不同。
- **`create_from_parsed` 的入参守卫是新增**：非 mapping 的 pages 或 header、没有页的字典，都会 `frappe.throw`（`request_invalid`/`header_invalid`/`statement_unparsed`）。旧侧没有这个边界，因为文件与解码在同一个进程里。
- **契约错误仍是契约错误**：解码行不符（未知字段、精度、负数等）会抛 `ReconciliationContractError`，不是 `frappe.ValidationError`；这是第二十一批既有行为，本批没有改动，也没有在 RPC 边界翻译。后续要统一成用户可见错误时单独做。
- **两处部署怪癖按原样保留**（差分证明与旧版逐输入一致，不按注释或直觉改）：
  - `_parse_date` 的"去时分秒"截断发生在印刷月份扫描之前，所以**带空格的印刷日期**（`15 มกราคม 2567`、`15 Jan 2026`）整条被截成日期前的数字而解析失败；只有不含空格的形式（`15ม.ค.67`）才走月份表。
  - 月份键全为小写且比较区分大小写，`15Jan2026` 不命中而 `15jan2026` 命中。
- **未迁的编排没有被模仿**：旧侧 `merge_statements`/`merge_gl_files` 的多文件去重、排序、期初反推与 `_anchor_ocr_snapshot` 覆盖逻辑仍在旧项目；新入口只做"一份解码结果 → 行 + 表头"，不假装已有合并能力。

## 顺带修复：`GlRow` 行指纹

第二十批把 `bank_recon_types.GlRow` 的字段搬全了，却没有搬它的 `__post_init__`，新对象 `row_hash` 一直是空串（旧侧用它做多文件去重键）。本批在受影响链（GL 行构造）上补齐同一条指纹，并加差分：差分现在逐字段比对包含 `row_hash` 在内的全部声明字段，所以这条链之后不会再静默漂移。`StatementRow` 的指纹在第十九批已迁入，未受影响。

## 验证

```sh
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest discover -s tests -p 'test_cowork_reconciliation*.py'
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest tests.test_cowork_bank_recon_contracts
PYTHONPATH=apps/pearnly_cowork python3.14 tests/test_cowork_reconciliation_source_parity.py \
    --source /tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b
python3 scripts/check-architecture.py
python3 scripts/i18n-build.py --check
git diff --check
```

- 对账用例 **123** 个通过（本批新增 **21** 个：一个不依赖框架的新测试模块 **20** 个用例，覆盖金额/日期/银行识别/两个转换函数/两段适配器/解码行契约/拒绝路径/入参不被改写，另加 `bank_matching` 的 GL 指纹用例 **1** 个）；`test_cowork_bank_recon_contracts` 15 个继续通过。
- 固定源差分新增 **439** 个金额输入、**1642** 个日期输入、**419** 段银行文本、**800** 批 entries→行（2728 行）、**800** 批 GL 页→行（3836 行）、**600** 批整段银行适配器、**400** 批整段 GL 适配器；月份表、月份装配键序、银行关键字表与键序、`to_float`/`parse_date`/`detect_bank`/两个转换函数、`GlRow` 全字段（含 `row_hash`）逐输入与部署版比较，全部一致。
- `create_from_parsed` 的整段适配器差分不重写实现：用部署版 `statement_rows_from_entries` + `finalize_rows` 与 `gl_rows_from_pipeline_legacy` 现算期望行，再逐项比对 `row_count`/`opening`/`closing`/`bank_code`/告警计数/`needs_review`/`accounts`。
- 隔离 Site（`cowork-backend-ack.localhost`、`cowork-backend-preparation.localhost`）各 **10** 个用例通过（本批两个 Site 各新增 **2** 个：解析入账全链 + 拒绝路径），两个 Site 一次 `migrate` 后再 `migrate` 一次均通过；四个入口匿名 WSGI 调用全部 **403**（含新增 `create_from_parsed`）。
- 顺带核对第二十一/二十二批遗留的运行态：`bench migrate` 后两个 Site 的旧 `pearnly-work` 页已不存在（`Page` 表按 `pearnly%` 共 19 条，无 work 项），`Pearnly Cowork` 工作区侧边栏存在。这是本批执行的 Site 迁移，不是部署。
- 架构检查 0 违规；`i18n-build --check` PASS，本批改动文件在 `check-i18n.py --list` 中 0 条（新消息键是 ASCII 语义键，沿用既有 `reconciliation.*` 口径）。
- 运行投影：交付前采 `push-runtime-twenty-third-delivery.json`（`2964173fd8ca8390bb118f37915bf994e8bd5f2d73bd2c3daec198de7b668395`），`verify` PASS；与第二十一批交付投影比较为 `push_runtime_identity_changed`，差异只在 `erp_endpoints` 的 `configuration_digest`（`5fa7110d…` → `c012939c…`，端点数仍 60、两个平台开关不变）。本批**没有**开工投影文件，所以这只能证明跨批配置仍在变，**不是本批窗口内的前后一致证明**；完整配置与旧状态迁入继续 HOLD。

## 未做与后续

- 未迁：文件读取与格式分派、OCR/引擎策略与模型降级、模型结果缓存、多文件合并与排序、`bank_stmt_reread` 换眼重读、异步任务状态与轮询、导出复核工作簿、人工复核动作、收入对账（`reconciliation_type=income` 复用同一入口）、GL/VAT 汇总。
- 未接前端、未写 ERP、未部署、未合并、未推送；没有真实 XLSX/PDF 或模型调用，所有输入都是任务内合成数据。
- 宽范围 `test_cowork_*.py` 仍有既有模块因本机缺 `frappe`/`openpyxl` 无法导入（本机只跑纯逻辑，Site 用例在隔离 bench 里跑）。
- 数据与清理：只改本任务路径；lab 容器用于本次 Site 迁移与验证，收尾停止；未改 Windows 侧与旧仓，未新增凭据。
