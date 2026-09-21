# 银行单 / GL 文档级 OCR 第四层前半：模型路径的判定逻辑（第四十批）

第四十批从新平台 `2e8a6ac` 开工，工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`。线上来源仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`（固定只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`）。不接前端、不部署、不改 DMS、不写旧库、**不调模型**。

第 ④ 层是"读取器都读不出来时的那条路"：把 PDF 交给模型、按提示词拿回 JSON、判它能不能用、把行归一化，以及在余额链断掉时换一只眼重读同一页。本批交付这一层的**提示词与判定逻辑**；模型客户端、缓存、PDF 渲染与并发属于器件，留给下一批的引擎接线。

## 一、本批交付

| 新落点 | 来源（sha256 固定） | 内容 |
| --- | --- | --- |
| `reconciliation/bank_prompts.py` | `services/recon/bank_stmt_gemini.py`（`9d0d277b…`）的对账单提示词 + `_STMT_PROMPT_VER`；`services/recon/bank_gl_gemini.py`（`6e2c6111…`）的 `_PROMPT` + `_PAGES_PER_CHUNK` | `STATEMENT_PROMPT`、`STATEMENT_PROMPT_VERSION`、`GL_PROMPT`、`GL_PAGES_PER_CHUNK`、`gl_prompt()` |
| `reconciliation/bank_model_path.py` | 两个 gemini 模块里"拿到模型结果之后"的那一段 | `statement_has_content`、`statement_from_model`、`gl_row_from_model`、`gl_from_model_chunks` |
| `reconciliation/bank_reread.py` | `services/recon/bank_stmt_reread.py`（`1e15ee4f…`，190 行） | `count_chain_breaks`、`maybe_reread_chain_breaks`、`EYE_VISION`/`EYE_DIRECT`/`MAX_REREAD_PAGES` |

**落点为什么在对账域**：部署版就把这条模型路径放在对账包里（`services/recon/bank_stmt_gemini.py`），行类型（`StatementRow`/`GlRow`）也是对账的，而本仓的域依赖表里 `ocr` 与 `reconciliation` **互不依赖**。所以模型调用在本批是**注入**的（下一批的引擎接线负责给），提示词与判定留在对账域，和部署版的归属一致。

保留的旧口径（都是钱路上的判定）：

- **整张读空不能当成功**：没有 `statement_has_content` 这条判据，`{"rows": []}` 会被当成功放行，对账里就出现一个**假的 0.00 期末**（旧侧注释写明）。返回 False 触发升级重试，再空才算失败。
- **期初行不是交易行**：无发生 + 期初关键词的行只用来补期初，模型自己给的期初优先。
- **缺日期沿用上一行**（同日多笔银行常省略重复日期），首行无可沿用就保持 None；只有"日期继承不了 + 两侧都没金额 + 没余额"的纯噪声行才丢。
- **置信度**：两侧金额都缺 → low；缺余额 → low；不认识的置信度词按 high 收。
- **长总账分块**：坏块**不能拖垮其它块**（整块失败就跳过）；期初**只认第一块**（第一块失败时保持 0，与旧侧一致）；没有日期的行丢掉；给了科目号就按前缀过滤。
- **换眼重读**：断链检测复用余额链的现成判定（`balance_ok is False` 计数），不另写一套；无断链 → 零渲染零重读；**严格更少断链才采纳**，否则原样保留（fail-safe，绝不越改越差）；任何一页失败 → **整轮弃、保原读**（半份重读会丢行，比原读更差）；每件最多一轮；页数异常多的巨份跳过防成本跑飞。

### 有意差异与未搬（都不改判定）

| 旧 | 新 | 理由 |
| --- | --- | --- |
| `json_from_pdf` / `try_with_fallback` / `flash_lite`（模型客户端与"主模型失败升级一次"的策略） | 不搬：模型调用由调用方注入 | 供应商与升级策略是器件层；本仓的引擎已经有 `ModelCall` 形状的注入点 |
| 内存 LRU + 磁盘缓存（键 = 文件哈希 + 提示词版本） | 不搬，但**保留版本串** `v63-totals` | 缓存是器件；版本串是提示词的一部分语义（改提示词要能让旧缓存失效），照搬备调用方使用 |
| `_page_chunks`（fitz 切页）、`_pdf_page_count`、`_render_page`（PyMuPDF 渲 PNG） | 不搬：`page_count` 与 `read_page(page_number)` 由调用方注入 | 打开/渲染 PDF 是新平台"器件"层的事 |
| `ThreadPoolExecutor` + `submit_ctx` 成本归因 + `OCR_BANK_REREAD_*` 环境开关 + 日志 | 不搬（本模块按页号顺序逐页读） | 并发与开关归调用方；仲裁本身与并发度无关。巨份上限保留为参数默认值 `MAX_REREAD_PAGES = 50` |
| 私有名 `_stmt_has_content` / `_row_from_dict` / `_count_breaks` / `_OPENING_KW` | 公开 `statement_has_content` / `gl_row_from_model` / `count_chain_breaks` / `OPENING_ROW_KEYWORDS` | 这一层要给下一批的接线用；判定与返回串一字未动 |
| 两个模块的 `logger` 与"异常一律吞成 `ok=False`"+ 返回 `str(e)` | 不搬 | 纯模块不持有 logger；异常处理归调用方（吞异常与把异常文案回给用户是两件事，新平台分开做） |

## 二、验证与证据

- 离线用例（隔离 bench）：模型路径 **24** 个（读空闸的四条路径、期初行与期初优先级、缺日期沿用、纯噪声行、有金额无日期保留、置信度降级与非法词按 high、页脚汇总字段的 `None`/数字/垃圾、分块合并与坏块跳过、期初只认第一块、无日期行丢弃、科目过滤、无可用行即失败、提示词版本与占位符）+ 换眼重读 **11** 个（非 PDF 不换眼、无断链零渲染、页数不合理跳过、更少断链才采纳、不更好就丢弃、空重读丢弃、单页失败整轮弃、上限由调用方给、按页号顺序读、两页各自看着对但接起来断链 → 不采纳）。银行族一起 **263** 个通过。
- 固定源差分（执行固定源码里的函数，把**器件换成替身**：模型客户端/升级策略/缓存/切页/渲染/读页/并发包装）：**差异 0**。计数：对账单 204 批（可用 187、读空 17、行合计 435、置信度 low 163）；总账 143 批（成功 47、失败 96、坏块 73、带科目过滤 28、行合计 73）；换眼 80 场景（采纳 7、丢弃 10、页读抛错 11、未触发 63、两方合计读页 86）。
- 差分覆盖面：对账单逐行九个字段（含 `confidence` 与去重指纹）、期初、期末、银行代码与页脚汇总一起比，**读空时的失败文案**也比；总账逐行九个字段加账户表/期初/期末/行数/错误文案；换眼比采纳后的行指纹与整条留痕（含 `breaks_before`/`breaks_after`/`kept`）。**提示词按字节比对**：对账单提示词从旧函数体里取出字面量与本模块常量比，总账 `_PROMPT` 直比，`gl_prompt()` 与旧侧 `.format(hint=…)` 的三个取值比。
- 静态检查：架构 **0** 违规（三个新模块都在既有 `reconciliation` 域内，无新增跨域方向）；`i18n-build --check` PASS；`check-i18n` REPORT-ONLY；`git diff --check` 干净。

### 线上来源

交付时独立采集：`evidence/baseline-fortieth-delivery.json`（`546d3f8a…`，采集 17:27:36Z）与第三十九批交付快照 `compare` **PASS**，`source` 仍 `ec2e5c95…`；运行投影 `push-runtime-fortieth-delivery.json`（`3d3dbf49…`）与上一批**逐项相同**（`settings_unchanged=true`、`observations_changed=false`）。**完整配置与旧状态迁入继续 HOLD**。

**本批开工未单独采集一次**：`before` 用第三十九批的交付快照，与第 34 批等此前批次的同类做法一致。这不是把规矩放宽 —— 它证明的是"从上一批交付到现在线上来源没变"，若要证明"本窗口开工那一刻的线上身份"，需要开工时的独立采集，本批没做，如实记在这里。

## 三、下一包（第 ④ 层剩下的：引擎接线）

把这条路径接起来才算第 ④ 层完成：引擎的**文档档位**（`run_invoice` 现在只准入 `invoice`/`auto`，要加银行单与总账的档位）、银行/GL 的执行接线（先走 ①②③ 的确定性读取器，读不出再走模型），以及**接进原生银行任务**（文件进来 → 读取器 → 行 → 既有余额链/校验/配对）。这需要真实文件、队列与一次真实的模型调用预算，属于下一批。

## 四、未做

- 未接任何调用方：`bank_prompts` / `bank_model_path` / `bank_reread` 三块都没有被生产的银行任务调用；不为了"看起来接上了"造一个没人用的壳。
- 未调模型、未渲染 PDF、未跑真实文件、未跑队列、未接前端、未部署、未推送、未合回主线；未动共享底座（`features.py`、`pearnly_core/**`、各产品术语表）。
