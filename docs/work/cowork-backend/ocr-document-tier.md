# 银行单 / GL 文档级 OCR 第四层收口：OCR 文档档位接上模型缝（第四十二批）

第四十二批从新平台 `b1bffd9` 开工，工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`。线上来源仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`（固定只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`）。不接前端、不部署、不改 DMS、不写旧库、**没有付过任何模型调用**。

上一批把文件入口接进了原生银行任务，但模型缝是空的：原生读不出的文件会被明确拒绝。这一批把缝填上 —— **提示词 → 文档模型 → 列来源校验 → 换眼重读 → legacy 形状 → 行**，也就是四层计划第 ④ 层剩下的那一段。

## 一、本批交付

| 新落点 | 来源（sha256 固定） | 内容 |
| --- | --- | --- |
| `ocr/engine/document_prompts.py` | `services/ocr/layer2_prompts.py`（`7087750f…`）的 `_BANK_STATEMENT_SYSTEM_PROMPT`（2,939 字符）与 `_GL_SYSTEM_PROMPT`（3,895 字符） | 两份**文档档位**提示词，逐字照搬 |
| `ocr/engine/document_tier.py` | `services/ocr/page_runner.py` 的校验顺序、`services/ocr/direct_read.py` 的银行闸接线、`services/ocr/bank_chain_gate.py`（`23244676…`）的 `reread_bank_page` | `text_pages`、`document_prompt`、`decode_document`、`model_document`、`validate_document`、`needs_manual_review`、`reread_bank_page`、`escalated_reader`、`document_to_legacy`、`read_document` |
| 控制器 `document_model_rows` + `configured_model_call` | 新的编排接线 | 把文档档位接成入侵入口要的 `model_rows`：文本层 → 模型 → 校验 → legacy → `statement_from_pipeline`/`gl_from_pipeline` 出行 |

### 提示词有两族，别互相替换

这一层新增的是**文档档位**那一族（要求模型填 `*_ref` 溯源列，服务文档 schema 与第 ② 层的列来源校验）。第四十批落进 `reconciliation/bank_prompts.py` 的是**旧 gemini 路**那一族（要求印刷页脚汇总与余额自检，服务行级转换）。部署版两条路都在用，本批**两族并存**，差分里各按各的字节比，并在测试里显式断言两族不相同（防止将来有人"顺手统一"）。

### 保留的旧口径（部署版顺序，一字不改）

- **校验顺序**（`page_runner` 2026-05-21 多 schema 重构）：总账先 `validate_gl_document` 再 `repair_gl_document`（"两链不劈叉" —— 单页直读路也是这个修复）；银行单先 `validate_bank_document`；
- **非发票类型**只要有告警就转人工复核（发票有自己的 triggers，部署版注释写明这些类型的视觉兜底还没实现）；
- **银行余额链闸**（第 ④ 层最早登记未迁的那一件）：先按算术修 —— 存取互换行翻正（唯一解，打 `chain_repaired`）、单侧量级误读在**模型自身读数的 20% 安全带内**用印刷余额反推（打 `chain_amount_imputed` + `review_required`）；仍不可解才**升档重读一次**；重读结果同样过链校验但**不再升档**（顶档再生是死循环），残断点与推定覆写行统一转 audit 警告交给人工。重读任何失败 → `DirectFallback`（部署版同一个 fail-safe 信号，原生沿用 `ocr.engine.errors.DirectFallback`）；
- `_needs_review` 由校验告警与银行审计告警共同决定。

### 有意差异

| 旧 | 新 | 理由 |
| --- | --- | --- |
| 单页直读的升档读的是**页面图片**（`read_max(image_bytes, api_key)`） | 升档读的是**同一页文本**，同一个文档提示词、更强的档位模型 | 原生还没有"图片 → 文档"的渲染直读器件；文本路径下同提示词同文本是等价的那一次重读 |
| 模型客户端、`ProviderOutcome`、档位选择、`OCR_BANK_CHAIN_GATE` 开关、日志 | 注入 / 不迁 | 客户端来自站点自己的 OCR 冻结配置（与发票引擎同一套 `settings.snapshot()` + `build_client`）；开关与日志归调用方 |
| `pipeline_result_to_legacy_dict` 的完整形状 | `document_to_legacy` 只给关键字段（pages/document/document_type/validation_warnings/_needs_review） | 对账侧真正消费的就是这几个（`statement_from_pipeline`/`gl_from_pipeline`），其余是旧编排的转发字段 |
| `vat_report` / `generic_table` | 不接（`admitted()` 拒绝） | 原生还没有这两个文档模型；接了只会产出没人读的形状 |
| 模型回答坏了走"回落另一条路" | 控制器捕获 `ContentError`/`ProviderFailure` → 返回"没有模型读数" → 入口报读取器的错误 | 原生这边没有第二条模型路可回落；**绝不落一张空任务**。站点没配 OCR 时是显式的配置错误，不是静默 |

## 二、验证与证据

- **端到端（隔离 Site，零付费）**：`scripts/cowork-bank-recon-lab-verify.py` 新增两条 ——（1）真扫描 PDF 经 files 域上传 → 控制器 `document_model_rows(model_call=假模型)` → `create_from_documents`：文档档位真跑（两次 `stage="document"` 请求、文档 schema 解码、两个校验层、legacy → 行 → 入库），存下来的任务逐行核对（matched + 两侧各一条未配、期初 1000、`statement_source`/`gl_source` 都是 `model`、`needs_review` 否）；（2）**站点没配 OCR 时被拒**（`frappe.ValidationError`，一个模型都没问、一张任务都没落）。整份 **20** 个用例通过；匿名 HTTP 8 个端点全 403。
- 离线用例（隔离 bench）：文档档位 **30** 个（只准入两个文档类型且拒其它、提示词必须带 `*_ref`、两族提示词不相同、文本层逐页、请求形状（stage/model/prompt 拼接/temperature/response_mime）、JSON → 文档模型、schema 失败与截断是内容错误、重试重发、围栏 JSON、非 mapping 被拒、总账 6091 复现 + 清值 + 修复、银行侧 6091 复现、非发票才转人工、互换行翻正不升档、不可解升档一次、升档结果只审计不再升、升档拿不到答案/schema 坏/抛异常三种回落、干净链不问重读、legacy 形状可 JSON 序列化、不支持类型在调模型前就被拒）。文档族 + 银行族一起 **439** 个通过。
- 固定源差分（执行固定源码，**器件换替身**：供应商调用、升级档、回落异常类；`repair_bank_chain` 与它的助手来自第三十六批已差分过的本侧实现，用来隔离本批的升档逻辑）：**120 批、差异 0**。计数：干净 62、翻正 10、链推定覆写 5、升档 55、回落 58、审计警告 16。比分三样：是否回落（连原因文本）、升档后逐行八个字段（含三个审计标记）、审计警告列表。两份提示词按**字节**比对。
- 静态检查：架构 **0** 违规（新模块都在 `ocr` 域内；控制器在模块编排层）；`i18n-build --check` PASS；`check-i18n` REPORT-ONLY；`git diff --check` 干净。
- 线上来源：交付 `evidence/baseline-forty-second-delivery.json`（`7ea12219…`，采集 17:53:39Z）与第四十一批交付快照 `compare` **PASS**，来源仍 `ec2e5c95…`；运行投影 `push-runtime-forty-second-delivery.json`（`c88b6c73…`）与上一批**逐项相同**。

### 本批仍没做开工采集

同前两批：`before` 用上一批交付快照，它证明"上一批交付到现在线上没变"，不等于"本窗口开工那一刻的身份"。

## 三、四层计划到此闭合，剩下的是可选项

**①文档模型 + 余额链修复 → ②列来源校验 → ③确定性读取器 → ④模型路径与文档档位**，四层都已交付并各自差分过；文件入口把四层串成一条链并接进原生银行任务，端到端在隔离 Site 上跑通（模型调用用假模型，真调用仍需一次预算）。

已登记、尚未做的可选项（都不挡迁移）：

1. **首次真实模型调用**：需一次付费预算（按既有约定不进默认回归）。接法已经就位：站点配好 `pearnly_cowork_ocr_environment`/`pearnly_cowork_ocr_policy` 后，控制器自动建客户端；
2. **图片/Word 直读与 pdfplumber 表格/坐标解析**（器件层，部署版有、原生未接）；
3. **多文件合并**（`merge_statements`/`merge_gl_files` 未迁）；
4. **`needs_mapping` 模板学习层**（要映射存储表与确认界面）；
5. **入口的文档态**：现在银行任务只接"两个原件"，部署版还有"确认列对应""重跑""复核动作"等流程件；
6. 清点表里其余两族（M2 销项税 Excel、工单对账）与主数据（客户按 ERP 为准，商品/供应商/科目各产品自维护）。

## 四、未做

- 未付一次真实模型调用；未跑真实银行单；未接前端、未部署、未推送、未合回主线；
- 未动共享底座（`features.py`、`pearnly_core/**`、各产品术语表）；未做用户验收。
