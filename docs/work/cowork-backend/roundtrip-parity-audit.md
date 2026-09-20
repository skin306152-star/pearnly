# Excel 复核工作簿回导：整链一致性审计

审计日期：2026-09-21。第六批主任务已重新采集生产来源与 OCR 动态配置，两类 start gate 均 PASS、未漂移。固定来源 SHA 为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，只读快照位于 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。下列旧路径及行号均相对此快照；不得改用来源仓库最新 HEAD 解释当前生产契约。

本文先审计实际调用链，再评审第六批设计。文中“保留目标/本批设计”不等于已通过真实 Site 整链测试；安装、执行、计费回执、ERP 实际落账和用户验收由主任务分别登记。此次审计未读取真实票据/工作簿、未连接生产数据库、未触发外部模型或 ERP 写入，只新增本记录。

## 结论

回导不是普通“每工作表送一次模型”的 Excel OCR。它先确定性读取复核表，按单据生成虚拟页，然后经过另一层适配、分组、历史写入、计费和 ERP 防重。纯 reader 输出相同，不能证明后续业务相同。

三项最容易被 Source/Batch 接线改错的事实：

1. `roundtrip_intake.py` 的零成本指模型成本。真实网页入口在外层仍检查余额，并按原始工作簿的 Excel 字符数收费。
2. 回导创建新 history；隐藏 `PRN:<旧 history_id>:<行序>` 用于溯源及重推防重，不是对旧 history 执行 UPDATE 的指令。
3. 方向和旧记录键必须越过模型 schema、页适配、分组、持久化和推送读取全部边界。旧源码本身已有二次按票号合并不同回导记录的问题，不能把这个风险掩盖在“纯函数差分通过”里。

## 固定来源完整路径

| 环节 | 真实来源及行号 | 实际契约、效果与边界 |
| --- | --- | --- |
| 网页收料入口 | `routes/ocr_recognize_routes.py:26-58` | `/api/ocr/recognize` 接受文件、公司/客户、整批 direction/posting_kind，传 `staged=True`。回导与普通文件走同入口；不是保存旧记录 API。 |
| 缓存与余额 | `services/ocr/recognize/core.py:141-196` | staged 不复用文件缓存，须生成本次草稿；非 staged 可以按文件指纹和账套命中缓存，直接返回且不新增 history。余额检查在回导识别之前，不因模型零成本跳过。 |
| 回导优先短路 | `services/ocr/handlers/invoice.py:19-38`；`services/ocr/roundtrip_intake.py:91-103` | 表格先 `try_parse_roundtrip`；命中直接返回 PipelineResult，不调通用 L2。旧解析异常、非匹配、空结果会返回 None，转普通模型表格路径。此宽松降级是旧行为，不能误称有确定性成功保障。 |
| 指纹 | `services/excel/erp_roundtrip.py:23-33,55-73,197-210`；`erp_roundtrip_reader.py:231-256` | 只认 `ขาย/ซื้อ/รอจำแนก` 数据表；按真实第一行必需列名判断，`สาขาคู่ค้า` 可选。按列名取值，重复列取最左；`data_only=False` 保留公式与手填值的区别。摘要 `สรุป` 不回导。 |
| 销项读取 | `erp_roundtrip_reader.py:124-161` | 每源行是商品行；优先按 PRN 的旧 history_id 聚合，缺键再按票号，再按源行号。行序仅解码，未用来替用户排序或去重。一个解析单据可来自多行。 |
| 进项与待判读取 | `erp_roundtrip_reader.py:164-221` | 进项每行一票；待判每行一待判记录。待判实际只保留旧键、票号、日期、空 items 与原因；表上的对方名、税号、合计未在此读取。不能假定 reader 已完整保留待判业务字段。 |
| 空行与舍入 | `erp_roundtrip_reader.py:76-91,94-121,168-174`；`erp_money.py:24-27,50-99` | 连续 50 空行后停止。公式金额按数量×单价重算，手填金额优先；税额与重算值差在 0.02 内保留票面值，使用 Decimal 和 HALF_EVEN。进项总额由税基/税额导出，不直接相信合计格；qty/price 读取仍经过旧 `_num` 浮点边界，精确数值迁移须登记差异。 |
| 页与模型转换 | `roundtrip_intake.py:43-88,105-130` | 每个解析单据（含待判）生成一个递增虚拟页，页数不是 sheet 数。direction、旧 history_id 放在页级 `declared_direction/source_history_id`；待判 `needs_manual_review=True`，原因进 notes。`erp_docnum/erp_party_code/items.erp_item_code` 虽由 reader 读出，实际没有传入 ThaiInvoice，不能把这些上传值变成可信 ERP 写入指令。 |
| 适配与二次分组 | `services/ocr/legacy_adapter.py:110-116`；`invoice_grouper.py:171-180,208-264,289-303` | 页级方向/旧键转 `fields.direction/history_id`，分组合并专门保留首个非空溯源值。但分组键仍是票号，不是旧 history_id；不同原记录或不同方向同票号会再次合并。 |
| 方向、账套、历史 | `recognize/persist.py:63-78,128-186,261-293` | 行方向优先；缺方向时补整批声明。再做账套归属及分类/归档处理。每组执行 `insert_ocr_history` 新增 history，保存 staged、posting_kind、页/来源关联；不存在“按 PRN 更新旧 history”。旧原件多票的 `source_pdf_id` 是批次标识，不能当真实 PDF 类型证明。 |
| 入库后费用与其它副作用 | `recognize/core.py:251-259,382-405`；`recognize/persist.py:327-345`；`recognize/history_postprocess.py:16-46,106-141` | 非 exempt：表格类型用 `excel_char_count_estimate`，整批历史成功后一次收费，以第一条新 history 作参考；仅 exempt 或单位≤0跳过。另有买方归属/异常检查；staged 禁止自动推送，非 staged 依套餐/端点派发。费用归属与模型 token 成本是两套事实。 |
| 原始字符计费口径 | `services/billing/pricing.py:121-141` | XLSX/XLSM/XLS 按全部工作表 `data_only=True` 的非 None 单元格字符串长度求和，失败旧逻辑 bytes//4。包含表头、PRN、摘要等，既不是解析单据数，也不是仅被回导的数据行字符数。 |
| 历史批量导出 | `routes/ocr_export_routes.py:110-139,197-219,250-338` | 100 条上限；服务端读 history 后回填真实当前 history_id。最近成功推送动作与最近一次推送状态分别查，只有唯一 adapter 为 Express 时输出四表；其余走旧单销项模板。向导 `/api/ocr/export` 的 sales_detail_th 是另一条按票号 enrich 的单表路径（145-175），不能混称同一完整回导导出。 |
| 工作簿内容 | `services/excel/erp_workbook.py:90-145,168-213`；`excel_template_th.py:234-340`；`erp_roundtrip.py:76-88,169-193` | 四表恒在，包括空表；销项每商品行，进项每票，待判含原因，摘要列真正新建主档。销项前 12 列公式合同不变，回导列追加；PRN 隐藏但不删除。导出再次使用当前 history_id，新一轮回导关联当前一版。 |
| 推送事实与旧单防重 | `services/erp/export_actions.py:122-166,211-257,270-302`；`express_push/prior_doc.py:34-115` | 最近成功记录提供 ERP 凭证/主档码，最新状态提供成功/失败/排队/人工。推送优先用 `fields.history_id` 查旧 history 的最近成功 DOCNUM，tenant 来自服务器 history 而非上传 fields。必须保留旧记录键才能在改票号后仍识别旧 ERP 单。 |
| 实际 ERP 边界 | `express_push/mapper.py:350`；`sales_mapper.py:313`；`enqueue.py:86-139` | 两种 mapper 附 `prior_docnum`，服务端体检后只产生 pending/manual 等入队状态。真正检查旧凭证仍存在、拒绝重复或落账由外部 companion 完成；此固定 Web 源码和纯测试不能证明当前客户机已执行成功。 |
| 删除语义 | `erp_roundtrip.py:2-5`；`erp_workbook.py:13-15`；`routes/history_routes.py:339-367` | 原流程是会计在 ERP 删除错单后重推，不是导入器自动删除。ERP 删单不会删除为该单新建的主档，摘要表是清理依据。删除 Pearnly history 只删本地记录/无引用文件，不是 ERP 撤单；先删旧 history 会使 prior_doc 的 JOIN 查不到旧成功日志，不能靠“解除关联”绕过防重。 |

## 已存在的新平台保护与本批保留目标

前五批已存在：Site/购买功能/角色/公司/客户服务端校验；Source 精确字节及 SHA、私有 File 原生旁路保护；不可变原件清单；批次稳定身份及回执；全批原子历史保存；页适配及分组显式传递 `direction/history_id`。这些能力并不代表 roundtrip 已接入；第五批对命中模板明确返回 `ocr.table_roundtrip_not_supported`。

第六批保留目标：固定泰文协议和必需/可选表头、按列名且最左列优先、销项商品聚合/进项逐行/待判保留、原工作簿字符计费、行方向优先、首次原始结果不可变、回导新增新 history、保存旧键的溯源语义、草稿不自动外推，以及真实推送事实缺失时留空/未知。任何字段只在 pure reader 里存在而未进服务端历史，不计为已保留。

## 本批设计评审与明确变化

1. **独立确定性执行。** Batch 冻结 engine 为 roundtrip，不创建模型 client、不要求 provider 凭据、不产生模型调用；仍冻结明确的费率档/计费基准并走余额与回执。不能调用通用表格 worker 后靠 token=0 假装短路。受理和 worker 使用同一冻结日期上下文，不因两位年份或跨年恢复改变日期。
2. **引用粒度为解析单据。** Source 仍 `logical_section`，一个 unit 是 reader 单据（含 pending），不是工作表。provenance 保留 sheet、source_rows、原始 PRN claim 和完整工作簿证据；模型/解析页摘要保持确定。Source 的 unit_kind 校验与投影必须同步支持此类型，原 `page_count=1` 不参与授权。
3. **保留单据边界，修复旧合并。** 专用整批计划逐页保存 reader 的票边界，不再经普通按票号二次 grouper。这是有意修复旧行为。审计用固定源码 grouper 的实际函数、合成两页作零 IO 验证：同票号、分别 `sales/old-A/107` 与 `purchase/old-B/214`，旧输出一组 `[1,2]`，只剩 `sales/old-A/107`。不能将这个问题写成已保持旧结果等价。
4. **待判不丢。** 无票号但有待判来源/原因的有效行仍须形成可人工处理结果；普通 `_has_retained_result` 只认号/金额/卖家，会拒绝这类行。专用合同应明确待判状态。待判不能被整批 direction 静默补为已裁决；旧 `persist.py:67` 的补值行为与“待判不自动推”意图不一致，修复须列为有意变化。
5. **旧键仅是 claim。** 所有 PRN 引用在受理和持久化前查当前 Site 历史权限，须与 Source 公司/客户匹配。无法证明旧平台 UUID 映射时明确拒绝，不伪造 Link、不当成首次推送。来源 claim 应独立不可变保存；普通编辑 pages.fields.history_id 不能替换已授权关系。未来真正推送时仍须重验即时权限和服务器成功回执，不能把受理授权当永久授权。
6. **坏模板失败关闭。** 命中却为空/损坏、连续 50 空行后仍有有效数据、trim 后名称相同却非规范 sheet 名等明确拒绝；不退模型、不静默遗漏。这是对旧宽松 fallback 的变化。缺键/坏键/手填新行的处理必须区分，不得将伪造外部旧键静默降格为无键新票。
7. **原件全覆盖不能只看新票数。** 未解析非空工作表、50 空行后的记录、摘要内残留主档/隐藏列等，不能因所有解析票已绑定就自动授权整份文件。完整证据和 coverage 必须显式处理；不能证明覆盖的原件继续仅允许具有初始及全部已知范围权限的上传者读取。document 始终拒绝 PDF 页预览。

## 仍未接或不能由本批纯测试证明

- 旧平台历史、成功 ERP 回执及 PRN UUID→新原生记录映射没有因实现 parser 自动迁入；本批不会读取真实旧业务数据来补齐。
- 四表字节生成不等于导出入口已获得权限约束，也不等于已有可信 ERP 状态 enrichment。必须使用服务器授权记录和真实状态；新平台缺少的外部动作列不得捏造成功。
- 回导不代表已实现 ERP 重推、旧 ERP 凭证删除、主档清理或本地 companion 版本验收；这些依然是独立业务链。
- 类别/归档模板、按税号自动账套归属、买方归属/异常检查、非 staged 文件缓存、自动推送等旧外层效果，不能因本批 Source/Batch 能保存一张历史就宣称全部迁移。需要逐项登记实际落点和验收，未接的继续列缺口。
- 旧导出还有无明细总额作税基、仅 subtotal 明细漏金额，以及“已合并主页+保留其它页”在导出拼接 items 时可能重复的问题；应由导出包用真实历史形状覆盖，不能只测手工构造的干净 merged_fields。没有确定无损映射时明确阻止，不猜金额。

## 本批整链验收应覆盖的证据

至少通过真实 Site 的：授权历史导出四表 → 修改票号/金额/跨表移动 → 上传 → 确定性受理/worker → 新 history 与旧 claim 同时存在 → 草稿完成 → 再导出，逐步核对单据数、商品数、金额、方向、PRN、原件索引及计费一次。外部 ERP 未写入时，这条链的终点只能写成“新平台历史/导出完成”，不能写“已重推到账”。

还须单列：两个旧键同票号、进销项同票号、同键多商品、重复商品行、无号待判、缺失/越权/跨客户/未映射旧键、编辑后伪造引用、无模型凭据、余额不足/查询失败、回执丢失后恢复、请求重试、部分历史保存失败、无有效回导行、50 空行后剩余内容，以及原始 provenance 不写入通用 Version。纯 reader/金额/工作簿差分、真实 Site 业务流与外部 ERP 结果分层报告。
