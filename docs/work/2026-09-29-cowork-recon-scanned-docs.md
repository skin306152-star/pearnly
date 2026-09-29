# Cowork 三个对账功能读扫描件与照片（2026-09-29）

来源：「语料盘点与压测准备」窗口转达用户要求（「旧项目能用，这里就要能用」）：银行对账（账单 / 总账）、收入对账（总账 / 销项税报告）、
销项税报告核查（报告）遇到扫描 PDF 与手机照片一律读不出。本窗口修，压测窗口用测试机上的真实语料（`C:\PearnlyStress\语料\`）压测，
问题回传再修，循环到通过；合入、构建、上生产按用户每次同意。

- 分支 `codex/cowork-recon-scanned-docs`，工作树 `.claude/worktrees/cowork-recon-scanned-docs`，基线 main `26e852e2`。
- 只改 `pearnly_cowork`（共用层未动）。Google Cloud 未改：识别服务账号 `pearnly-ocr-sa` 已有 Document AI API User，
  新加坡处理器 `pearnly-enterprise-ocr-sg`（`6c7dfffac937fcd9`）启用、固定版本 `pretrained-ocr-v2.1.1-2025-01-31` 已部署（只读核实）。

## 旧线上实际在跑的是什么（ec2e5c95，旧项目 09-24 退役前最后承接流量的版本）

- 旧仓 `docs/deployment/MIGRATION_STATUS.md`：2026-09-05 22:19（曼谷）原子更新识别策略 —— 发票 economy（3.1 Lite → 3.8 LOW），
  **其余任务全部 enterprise**；银行 / 总账 / 税报扫描件走冻结的 Enterprise 财务适配器；Web / Worker 加了 Document AI 配置与权限。
- 企业档（提交 `a48766de`）：Document AI 读字（每页 0.0015 美元 ≈ 0.05 泰铢，PDF 每 15 页一块、照片每张一次），
  银行与总账先按版面坐标本地拼行，余额链算得平就直接用，**不调大模型**；算不平（以及税报，没有本地拼行器）才逐页让
  `gemini-3.8-flash` LOW 看原图 + 识别原文，按冻结契约（JSON Schema）抽取；对账适配器直接返回，不再做第二次余额修复。
- 用户 2026-09-29 问「对账到底走的啥」后选定：**迁企业档**（先前一版按普通管线做的「每页 Vision 取字 + 大模型」撤回，
  它每页都付模型费，也不是旧线上实际在跑的路）。ADR-013 的「3.1 先读、3.8 默认关」只管发票（用户当时：「对账不动」）。

## 读代码另发现的问题（一并修）

对账后台任务直接调模型，不在识别任务（Pearnly OCR Work）里；服务商层要求在 Work 里（`ocr.durable_work_required`，RuntimeError，没人接）。
所以**凡是要模型读的文件都会整单失败**：带文字层的收入总账 PDF（旧线上先走模型）、银行对账里免费解析不够要模型兜底的 PDF / 图片。

## 改动

| 位置 | 改动 |
|---|---|
| `ocr/enterprise/local/*`、`contract.py` | 旧线上 `enterprise_local`（版面几何、银行 / 总账本地拼行、余额链）与冻结契约**逐字节照搬** |
| `ocr/enterprise/request.py`、`pages.py`、`adapter.py` | 抽取请求构造、Document AI 请求体与页文字、页适配：函数逐条照搬，只改导入 / 错误类型；页结果结构在企业档内单独定义（平台的 `PipelinePageResult` 只迁了发票形态） |
| `ocr/enterprise/pipeline.py` | 旧线上 `enterprise_pipeline.run`：分块读字 → 本地拼行（平就用）→ 否则逐页 3.8 契约抽取 → 整份校验；服务商调用改为注入；页面图只在要抽取时逐页渲染 |
| `ocr/providers/client.py`、`ocr/engine/contracts.py` | 客户端加持久化的 `document_ai_call`（检查点、共用限流、429 顺延）；模型请求支持 `response_schema`（`responseJsonSchema`） |
| `recon_center/document_ocr.py`（新） | 每份要模型读的文件一个持久识别任务（kind `cowork.recon_document`，`hooks.py` 登记），与销项税核查读发票同一套：冻结识别配置、余额检查、7 分钟让出续跑、检查点防重复付费；读出行按页扣费；对账任务结束后没跑的任务自行取消 |
| `cowork_bank_reconciliation.py`、`bank_intake.py` | 银行接缝走识别任务；企业档的行按旧线上对账适配器转换、作为定案行（读取入口不再二次修复）；删掉在任务外直接调模型的 `configured_model_call` |
| `recon_center/sources.py` | 总账：PDF 先走识别（旧线上顺序）读不出退文字行；扫描 PDF / 照片走识别。税表：带文字层 PDF 免费读出不足 3 行才走识别（旧线上 `MIN_DETERMINISTIC_ROWS`）；扫描 PDF / 照片走识别；企业档引擎名传给税表解码（旧线上口径保留无字母数字单号的行） |
| `reconciliation/bank_recon_merge.py`（新）、`bank_recon_service.create_from_documents` | 银行对账两边都收多份（旧线上 `bank_v2_submit`）：逐份读，一边一份都读不出才失败，按旧线上 `merge_statements` / `merge_gl_files` 合并（按日期、行哈希去重）；照片读出的账单与 PDF 一样可进复核 |
| `recon_center/jobs.py`、`vat_check.py`、`public/desk/recon.js` | 三种对账把各文件的识别读取器注入读取层；银行两边上传口可多选；三个页面左右上传口都收 PDF / 照片（png/jpg/jpeg/webp/tif/tiff/bmp）/ 表格 |
| 词条 | `reconciliation.balance_insufficient` / `recognition_timeout` / `document_unreadable` / `recognition_busy` 四语 |

## 成本（用户 2026-09-29：成本换质量的地方先告知）

1. 扫描页 / 照片每页 **Document AI 约 0.05 泰铢**。
2. 银行 / 总账本地拼行**算不平时**，整份每页一次 3.8 LOW 看图抽取；**税报每页都要 3.8**（旧线上同样，没有本地拼行器）。
3. 带文字层的**收入总账 PDF 先走识别**（旧线上顺序）；原来这条路整单报错，没有花费。银行电子 PDF 仍先免费文字解析，读不好才走识别（与旧线上不同：旧线上 PDF 全走企业档）。
4. 读出行的文件**按页向客户扣费**（与录入发票同价）：新平台对账原来不扣；旧线上对账按页扣（`max(页数, ⌈行数/40⌉)`，前 200 页 ฿1.50/页）。

## 已知限制 / 与旧线上的差别

- 银行电子 PDF 先走免费文字解析（旧线上企业档对所有 PDF 都 OCR）。
- 模型超时 120 秒（客户端上限；旧线上 180 秒）；Document AI 的限流走共用层预算（旧线上数据库间隔 9 次 / 分钟）。
- 一份最多 50 页、单文件 25MB、单页渲染 4000 万像素；HEIC 照片不收（旧线上对账同样不收）。
- 收入对账两边仍各一份（旧线上 `gl_vat_submit` 也是各一份）。

## 验证

- 离线（一次性容器，`pearnly/frappe-sample:16.34.1`）：Cowork 1674 项，2 个既有错误（样板缺 python-docx）；新增 `tests/test_cowork_recon_scanned_docs.py` 13 项。
- 旧版对照（新增 `tests/test_cowork_enterprise_source_parity.py`）：8 个文件逐字节相同、7 个函数语法树相同、2 个函数只差错误类型 / 占位构造、
  24 种抽取请求逐项相同；文档识别层、文档模型路、税表解析、收入解析、识别引擎、供应商、发票结构对照均零差异 / PASS。
- 架构 0 违规；`i18n-build --check`、`check-frontend-i18n --strict`、`node --check`、`git diff --check` 通过。
- 测试机（线上镜像 `4ff689c1` + 本分支实时源码，testcowork）：
  - B04 MrsFlowers SCB3061 02 月扫描账单（2 页）+ Excel 总账：企业档 50 秒，Document AI ฿0.105；本地拼行余额链不平 → 两页 3.8 抽取，35 行，
    按旧线上口径进「待复核」（35 行低信心，余额链与跨页衔接报警）。先前普通管线版读出 41 行并直接完成 —— 谁更准待压测窗口按答案核对。
  - B05 冰厂 KBank8229 01 月存折 5 张照片 + Excel 总账（多文件）：5 个识别任务全成功，100 秒；本地拼行都不平 → 逐张 3.8 抽取；
    各张 0 / 12 / 4 / 23 / 1 行（第 1 张像封面），合并 40 行，进「待复核」。**疑点**：合并后最早日期 1982-04-05，像两位佛历年读错，交压测核对。
- 交给压测窗口（2026-09-29）；测试机 Cowork 栈保持挂着本分支实时源码、testcowork 切在测试机、线上那份测试租户定时任务暂停。

## 选型调研（2026-09-29，只读网上调研，未调用任何新服务）

用户问：市面上有没有专做多行密集表格的识别，还是只有谷歌在成本和质量上占优。结论：

- **没有任何产品公开过泰国银行账单 / 总账 / 税报逐行准确率**；泰文基准只测文字或表格相似度。只能拿压测语料（有答案）自己比。
- 谷歌的优势在「便宜的读字 + 坐标」：Enterprise OCR 每页约 ฿0.05、支持泰文印刷体、新加坡区域；真正保证质量的是我们自己的本地拼行 + 余额链校验，任何服务都不替我们做这一步。
- 不适用：AWS Textract 不支持泰文；谷歌 Form Parser / Custom Extractor / Bank Statement Parser 官方不列泰文或只支持英文；Azure 银行账单预置模型只支持美国英文。
- 值得比的：Azure Layout（泰文印刷 + 手写、单元格表格、东南亚区域，每页约 ฿0.35）；Mistral OCR 4.1（每页约 ฿0.14，批量 ฿0.07，带逐词置信度）；
  Typhoon OCR 1.5（2B 开源泰文专用，但泰文基准字错率不如 Gemini；旧项目当年也试过 Typhoon 输了）；iApp 泰国银行账单接口（直接出交易行，每页约 ฿0.9–1.25）。
- 大模型逐页读（1600×2200 一页、输出约 3000 token）：3.1 Flash-Lite 约 ฿0.17–0.22；3.8 Flash 约 ฿0.44–0.57（调研称是截至 2026-12-31 的优惠价，2027 起约翻倍）；Qwen3-VL-Flash 约 ฿0.05 但数据出境需核对。
- 现在真正的成本在「本地拼行算不平 → 整份逐页 3.8」：测试机两份样本都走到了这一步。优先方向是提高本地拼行通过率、税报补本地拼行、只对不平的页升级。
- 下一步（需用户同意）：在测试机语料上做对照赛，指标为逐行完全一致率、漏行 / 重复行、余额链通过率、耗时、每页成本。只用谷歌各档不需要新账号；接 Azure / Mistral / iApp 要用户开账号，并同意把真实客户资料发给新服务商（PDPA）。
