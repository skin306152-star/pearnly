# Cowork OCR 来源与执行接入断点

日期：2026-09-21。第二批审计基线 `f409bce560df11f78ee5fc7908e71129c4e37a36`；第三批起点 `392d9d917cb62c1a00d0bb43f72dc7b806082a86`；工作树 `cowork-backend-migration`。
旧来源固定为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，快照 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。主代理已重新核对该版本仍承接旧生产；本子任务只读源码，没有访问生产数据、调用供应商、启动容器或执行部署。

## 本批实际交付

第二批迁移输入元数据、物理页关联及质量判定。第三批补齐 [发票schema和纯校验](invoice-contracts.md)、[完整分组](invoice-grouping.md)、结果准备、[纠错提示合同](invoice-feedback.md)及[实际运行策略](ocr-runtime-policy.md)，并验证完整结果在原生Site保存。没有注册 OCR handler，没有公开识别 RPC，没有接入模型，也没有宣布旧识别链完成迁移。

| 新落点 | 来源 | 保持的行为 |
|---|---|---|
| `ocr/inputs.py` | `recognize/core.py:101-136`、`pipeline.py:117-131`、`table_path.py:36-45`、`core/route_helpers.py:177-191` | 扩展名、非空、100 MiB、PDF 1–50 页；边界值允许 |
| `ocr/pages.py:flatten_pipeline_pages` | `services/ocr/legacy_adapter.py` | 一页多票展开但共享物理页编号，保留字段、来源、诊断、重复标记与 document |
| `ocr/pages.py:aggregate_confidence_band` | 同上 | 管线质量取最差：`needs_review > yellow_confirm > auto` |
| `ocr/pages.py:assess_confidence` | `recognize/core.py:43-62` 及识别后的重复/置信计算块 | 非副本优先；重复单号标记；全副本时选首次同号候选中字段最完整的一页；最高分导出 high/medium/low |
| `ocr/pages.py:all_pages_not_invoice` | `services/ocr/entrypoints.py:187-197` | 全部页明确非发票才返回 true；空列表不算；字符串仅 `true`（忽略大小写/两端空白）算真 |
| `ocr/pages.py:history_pages` | `recognize/persist.py:178-186` | 保留全部 source_pages，只把第一项 fields 替换为分组合并字段 |

这些函数使用独立副本，避免历史页修改反向污染管线结果。`flatten_pipeline_pages` 的输入是已校验的管线值对象（具有 `model_dump`），不是供应商原始 JSON；第三批已迁移 invoice/auto 所需完整 schema，尚未接模型运行。

### 可复用签名

```python
validate_input_metadata(filename, size_bytes, *, pdf_page_count=None,
                        max_bytes=MAX_FILE_BYTES, max_pages=MAX_PDF_PAGES) -> dict
file_extension(filename) -> str
flatten_pipeline_pages(pages) -> list[dict]
aggregate_confidence_band(pages) -> str
assess_confidence(pages) -> dict  # {pages, confidence}
all_pages_not_invoice(pages) -> bool
history_pages(group) -> list[dict]
```

`OcrInputError` 提供 `code` 和旧 detail 形式的 `details`。元数据函数返回 `extension/family/page_count/billing_kind`。新增 `ocr.invalid_input_metadata` 只用于拒绝内部错误的大小/限制类型；不能让调用方拿未解析的客户端页数绕过校验。

## 附件输入和 PDF 关联的确切合同

- PDF：`.pdf`。图像：`.png/.jpg/.jpeg/.webp/.tiff/.tif/.bmp/.gif`。表格与文本：`.xlsx/.xls/.xlsm/.csv/.tsv/.docx/.doc/.txt`。扩展名转小写，不额外 trim 文件名。允许扩展名不代表本轮已实现对应识别器。
- 文件必须非空，最大 **104857600 字节**。PDF 由适配器从原始 bytes 实际解析页数，范围 **1–50**。原流程先验扩展名，再验大小，再解析 PDF。损坏 PDF 是 `ocr.invalid_pdf`。
- 图像按一个物理页处理，旧入口没有尺寸/解压后像素上限及完整图像解码校验；多帧 TIFF/GIF 也没有在该门禁展开成多页。不能把本批元数据门禁称为图像安全解码器。新附件层若收紧解码/像素限制，应单列有意变化。
- PDF 旧管线按 **200 DPI** 渲染 PNG（`pipeline.py:217`）；旧代码一次保留整份图像列表，不能直接据此承诺 100 MiB/50 页在新运行组的内存可行性。图像降采样最长边 2400 仅属于 Vision L1；直读与 L3 不是这个输入合同。
- `page_number/page/page_index` 全部是 **1 起始物理页号**。同一页识别出多张票时，所有兄弟记录仍使用同一个物理页号，不能按展开数组重编号。
- `invoice_grouper` 在缺少 `page_index` 时才按输入序号补值，`page_indices` 可能含重复物理页号；这是源字段，附件抽页时应验证范围并明确去重语义，不能将票数当页数。
- 新 `history.recognition.persist_batch` 的适配约定：允许来源页号列表有重复，对封存/权限映射使用 `sorted(set(indices))` 的物理页集合；`pages` 完整保留，不因物理页号相同而删掉同页兄弟票面。该文件关联层由主任务实现，本纯层不承担落库。
- 多票批次历史有同一 `source_pdf_id`，每票有 `source_page_indices`、`source_index`（1 起始）、`source_total`。旧 `persist.py:79,274-275` 仅多票时填写 PDF 批次/页关联；单票为 None，不能因此丢失原件关联。
- 分组历史首个 page.fields 是合并后的票据字段，其余 source_pages 保留原页证据。首次 `ai_raw` 与后续人工修改分开，后续保存不得覆盖第一次结果。
- 页面展开会复制物理页的耗时/token 诊断到每张兄弟票。**供应商用量不能汇总展开后的历史页**；从未展开管线页或供应商 `usageMetadata` 取一次。用户 PDF 计费按物理页数；表格 `page_count=1` 只是占位，计费单位是字符估算值。

## 旧识别主链及最小可用范围

来源入口 `services/ocr/recognize/core.py:65`：作用域/方向归一 → 文件门禁 → 非 staged 的授权缓存查询 → 余额查询（失败关闭）→ `entrypoints.run_pipeline_for_file` → 规范化页面 → 全部非发票拒绝 → 重复/质量 → `persist_invoices` → 计费及后处理。staged 不命中缓存，也不自动推送。

普通发票/auto 的图像路径默认启用直读（`direct_read.py:67,119`），最多20物理页；但第三批实查线上 `OCR_FAST_PATH_ENABLED=true`，文本PDF达到阈值会先走文本L2，优先于直读。逐页模型输出经 `ThaiInvoice`、金额勾稽与结构闸；不可接受时 `DirectReadFallback` 回落整件 Vision 管线。超过直读页数或其他路由走 L1 Vision → L2 → 必要L3；当前旧配置还缺Vision必需环境，不能称此回落已验证可用，详见运行策略证据。不能只用“JSON 能解析”代替这些闸。

`direct_read.py:429` 的硬闸包括总额存在、`_check_amount_math` 和 `evaluate_sanity`（包含税号校验等）；允许有记录的折扣推导。缺单号仅软警告；credit note、异常日期、折扣推导等影响人工复核。`OCR_DOUBLE_READ` 默认关闭，开启时首次结果与重读差异须留底。

企业档 Document AI 只在 `enterprise_pipeline.category_for` 对 **bank_statement/general_ledger/vat_report** 生效，不是 invoice/auto 默认链。表格路径结构解析后走文本 L2，并有 `handlers/invoice.py:19-47` 的 roundtrip 导入优先分支。不能把 bank/gl/vat、表格 roundtrip、Qwen、自托管模型一并写成“当前已支持”。

建议第一个可接执行的业务切片：人工上传的 invoice/auto，正确持久化原件、物理页与首次结果，产出 staged histories 供复核；保留所选范围内的 schema/金额闸/失败与回落语义。若先只提供少量格式或直读而暂不提供 Vision 回落，需要显式声明范围缩小，并在未支持路由失败关闭；它不等同旧识别完整对齐。本批只完成该切片的纯合同前置部分。

### Schema 和金额分组：第三批已迁纯层

`schemas_invoice.py` 的金额/数量字段是字符串，不是 float：`total_amount` 是折扣后的净应付，不能用实付现金或找零代替；`subtotal/vat/discount/wht_rate/wht_amount/payment_received/cash_amount/change_amount` 分开。items 使用 `name/qty/price/subtotal`。空 THB currency 与非空外币不同；保留 `date_raw`、`source_refs`、document_type、additional_invoices、is_copy_or_duplicate。

来源 schema 会将数字转字符串，null 标量转空串；document_type 未识别值变 other。其年份归一基于 `date.today().year` 的候选窗口，只改年份而保留模型月日；这与已迁 history 人工修改的曼谷时间/完整日期处理不完全相同，不能直接替换。迁移时应注入明示时钟并补边界差分。

`invoice_grouper.py` 根据单号比例分三路：单号主导分组（允许跨不连续页同号）；多数无单号时以金额页作边界；都无金额时整体一组。items 合并含规范名称、qty/price、子串及相似度去重。第三批已按此完整逻辑实现 `ocr.grouping`，使用Decimal并对普通输入严格差分；半分舍入、极大/极小金额导致的判重变化单列精确断言，不能声称逐字等价。`ocr.results.prepare_history_batch` 拒绝缺失物理页，保留有效L2但重读失败的待人工复核草稿；内部组装结果可传已有persist_batch，不是RPC/收费/模型工作流。

## 供应商协议、配置和新 Core 差距

| 范围 | 旧实现 | 新 Core 当前能力 / 缺口 |
|---|---|---|
| 模式决策 | `engine_policy`：env、平台设置、task/plan/exempt；缺省 economy；还有 direct35/selfhost/qwen | `core/ocr.py:190`：Site `pearnly_ocr_mode`，缺省 enterprise，仅 enterprise/economy。源码默认不能证明线上有效配置 |
| Gemini 传输 | `providers/vertex.py` / 对应 backend；结果含模型、token、错误类别；结构化多模态设置 thinking 预算/级别 | `core/ocr.py:228`：图片 inline_data + prompt，temperature 0、JSON MIME；未设置旧 thinkingConfig/响应 schema；仅返回首个 text 的 dict/None |
| 凭据/地址 | Vertex 项目/位置 env + Google credentials；部分模式有用户自有 Gemini key；3.8 模型强制 global | Core Site `pearnly_ocr_backend/api_key/credentials/project/location/endpoint`；Vertex 默认，AI Studio 可选。服务账号 token 刷新；不要将用户 key 自动带到 Vertex |
| 模型档位 | 按旧 task/plan 解析；直读用 flash_lite，特定重读/升级用各档位 | Core enterprise 的 flash_lite/fallback 均为 3.8 Flash；economy 为 3.1 Flash Lite → 3.8 Flash。档位不是用户计费单价的别名 |
| Vision | `layer1_vision` Google Cloud Vision SDK，保留文字/坐标/置信度 | Core Gemini transport 没有 Vision adapter；旧 SDK 外呼不会自动获得新 checkpoint/budget 保护 |
| Document AI | 仅指定长表类别；分块和独立 quota；旧 DB 锁/等待 | 本批不接；不能沿用旧轮询锁跳过新全机限流 |
| MIME | 旧 direct sniff 支持 GIF，未知默认 PNG | Core detect_image_mime 未识别 GIF、未知默认 JPEG；不能对旧允许格式照搬默认 MIME |

新接口 `json_from_images` 是传输工具，不含 ThaiInvoice、旧 prompt、账务闸、字段 provenance、多票分组或批次持久化。`try_with_fallback` 只按返回值/谓词切换模型，也不等价于旧整件 Vision 回落。

依赖清单需按新平台 Python **3.14.7** 验证，不直接复制旧 Python 环境：旧 requirements 有 Pydantic 2、PyMuPDF 1.24.10、pypdf 6.15.0、Pillow 12.3.0、google-genai、google-cloud-vision 3.14.0，以及 Excel/Word 结构解析库。当前 Core/Cowork pyproject 没有列入这些产品依赖；本批未安装或升级任何依赖。实际供应商有效模式/项目/位置/开关仍需主任务读取非敏感运行配置后确认，不能从注释中推断。

## 执行与计费合同

新 Core `execution/work.py:35` 已有 handler 注册、幂等 submit、OCR 专队列、租约、延期/不确定/失败状态和修复调度。任务参数最多 32 KiB，应保存私有附件引用、不可变内容标识和必要业务上下文，不能放图片或凭据。相同 kind/reference 的 arguments/user 必须一致。submit 会 commit，不能假定与附件创建天然在同一事务中。

`work.run` 当前不会替产品 handler 恢复 `doc.user` 的会话身份；handler 要显式恢复并重新核对当前用户、Cowork entitlement、workspace/client、文件与全部历史关联的权限，不能用 worker Administrator 默认上下文放行。

`execution/provider.py:28` 在调用前持久化 `started`，成功保存完整 JSON（含 `usageMetadata/modelVersion`）；相同 payload+model 的 `done` 直接重放。`started` 结果未知时抛 Uncertain，禁止自动付费重打。429/503 明确拒绝可延期；传输结果未知不能当成可安全 fallback。checkpoint key 不含 URL/凭据，任务恢复期间模型、backend、prompt/schema 版本、输入必须稳定；配置迁移不可静默改变未完成任务的供应商语义。

新 Redis budget 提供跨产品/租户容量控制（`execution/budget.py`，专用 DB 6），单请求最大 120 秒、响应最大 8 MiB，job 600 秒。50 页全部顺序调用/全量渲染未必能在该边界内完成；需按真实资源设计分页恢复和缺页可见性，不能把部分页成功标为整件完成。`execution/inputs.py` 提供私有缓存及磁盘门禁，但无 work 上下文时不替调用方限制 loader 大小，产品文件门禁仍必须执行。

旧 `recognize/persist.py:262-328`：先存全部 histories，失败清理本批后报错；成功后以首个 history ID 为引用，整件扣一次。PDF/图像用管线物理页数，表格用源字符估算；全部非发票不保存/不扣费。旧后处理异步 charge 失败可能只记日志，新实现不能把这一点保留为“可靠计费”。

新 `billing_client.status/charge` 与控制端 `billing.charge:178` 可用：身份来自 Site scoped token；先查余额失败关闭；charge 的稳定 reference（≤140）按 tenant/product/usage 唯一回读，重复相同 product/kind/units/tier 返回既有收据，复用引用但参数不同会拒绝。

推荐接线顺序（尚未实施）：

1. 固定上传内容及 owner/scope，校验实际大小/页数；固定模型路由、prompt/schema 版本、单位与幂等批次引用，再提交 work。
2. worker 恢复身份和权限，首次付费前查余额；已有确定完成 checkpoint 时恢复已发生工作，避免新余额闸把已付费任务永久卡死。
3. 受资源预算控制地逐页渲染/调用；每次付费调用经 Core checkpoint；schema/业务闸/回落同样保留 provenance 和首次结果。
4. 全部页齐全、分组/公司归属已确定后，幂等写全部 histories 与文件关联；不能新建一张 history 后立即收费并宣告整批成功。不得启用 staged 自动推送。
5. 以稳定批次 reference 对整件 charge 一次；计费通信失败延期，重试只重放已完成 provider checkpoint 并回读同一账单收据。只有历史/附件齐全且计费结果可确认（或明确免计费）才成功。

需另外验收：同一文件并发提交、部分页完成后重启、429/503、请求结果未知、最后一票保存失败、扣费响应丢失、账号/账套权限撤销、同页多票与跨页一票、最大尺寸解码/PDF 渲染、零金额/外币/贷项/佛历边界。不能以纯层差分测试替代真实 Site、供应商样本和用户业务验收。

## 已执行验证与断点

- `test_cowork_ocr_contracts.py`：12 项离线单元测试通过，覆盖格式/边界、页号、多票、原始金额字符串、独立副本、质量与非发票语义。
- `test_cowork_ocr_source_parity.py --source <固定快照>`：校验六个来源文件 SHA-256 后，仅 AST 提取纯函数/块，不 import 旧业务或运行 IO；通过 336 个输入边界、2000 组质量页面、200 组多票页面和 9 组非发票差分。
- `python3 scripts/check-architecture.py`：本批纯层完成后通过，0 violations。
- 差分没有覆盖 provider、模型质量、完整 ThaiInvoice、金额分组、PDF 渲染、计费 IO、附件持久化或 worker 生命周期；这些是明确保留的后续断点。
- 未运行付费 OCR、真实用户数据、容器、生产部署或真机验收。
