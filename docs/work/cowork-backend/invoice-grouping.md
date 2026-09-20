# 发票分组与 OCR 金额纯合同

第三批本地后端迁移；本轮开始基线 `392d9d917cb62c1a00d0bb43f72dc7b806082a86`，工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`。父任务已重新核对旧生产仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，凭据索引 `baseline-third-start.json` 为 PASS；固定来源目录 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。交付前线上复核由父任务统一记录，本工作包不访问生产。

本次所属 App/域为 `pearnly_cowork.ocr`，迁纯分组、OCR 金额/税号合同及已验证识别结果到原生历史草稿的纯准备；无 Frappe、数据库、文件读写、时钟、模型供应商或外部业务。未接前端、未启停/迁移环境、未部署。

## 入口与依赖

`pearnly_cowork.ocr.grouping.group_invoices(pages)` 接收已经验证的页字典列表，返回：

```python
[
    {
        "invoice_fields": {...},
        "source_pages": [{"page_index": 1, "fields": {...}}, ...],
        "page_indices": [1, 3],
    },
]
```

每组可直接传给既有 `ocr.pages.history_pages(group)`，仅第一张保存页接合并字段，所有来源页及页级诊断继续保留。`page_index` 沿用输入的 1-based 物理页号；缺失时按原规则补顺序号。同一物理页展开多张票时允许重复索引，不能用展开后的列表长度计算 PDF 页数或计费。

依赖方向为 `grouping → schema.invoice.ThaiInvoice + money`。模型字段由协作工作包提供；分组依据 `ThaiInvoice.model_fields` 自动携带全量标量，排除 `items/notes/additional_invoices/source_refs/is_not_invoice/is_copy_or_duplicate` 的专用或页级结构。`direction/history_id` 单独保留首个非空值。没有反向引用或新增跨 App 依赖。

共享金额模块接口：

- `normalize_money(value) -> Decimal | None`：保留旧 OCR 的宽松字符清洗；空、不可解为 None，真实零为 Decimal 零。只供 OCR 语义，不能替代严格金额输入校验或对账的空值规则。
- `normalize_id(value) -> str`：仅保留数字字符。
- `valid_thai_tax_id(value) -> bool`：保持 13 位 MOD-11 校验，不查询主档或认证身份。

金额勾稽需要严格解析时，由 `ocr.validation` 使用自己的严格 Decimal 合同；不把两个解析语义混为一谈。旧清洗行为如 `1e3 → 13`、`(100) → 100`、Unicode 减号被剥离均保留且有测试，不把这支函数当作通用财务解析器。

## 来源与保持行为

| 固定来源 | 新落点 | 保持内容 |
| --- | --- | --- |
| `services/ocr/invoice_grouper.py:group_pages_to_invoices` | `ocr/grouping.py:group_invoices` | 三路门槛与分组结果格式 |
| `_group_by_invoice_boundary` | 同名内部函数 | 至少半数有号时按号；同号跨非连续页回并；前导无号续页挂起；无号页完整金额块与当前不同则独立 |
| `_build_invoice_from_pages` | 同名内部函数 | 标量首个非空、金额原始字段不改写、items 合并、notes 用 ` · ` 拼接、direction/history_id 溯源 |
| `_merge_items` | 同名内部函数 | 名称去空白、数量数字部分、价格键；严格/子串去重；重复达到一半且至少两行时，才按相似度 ≥0.9 做复写联归并 |
| `services/ocr/money.py` | `ocr/money.py` | OCR 清洗和税号算法；返回金额改为 Decimal |
| `services/ocr/schemas_invoice.py` 字段集合 | `ocr.schema.invoice.ThaiInvoice` | 源差分从旧模型 AST 独立取字段名，核对新分组的完整标量集 |

有号页不足半数时，带 `total_amount` 的页作为一张发票的结束页，归入前面的无总额缓冲页；没有任何总额则所有页归为一张。`0` 是有效总额；号码只 trim 作分组键，不忽略大小写、不重写票面原号码。

两项明确边界：

1. 新入口先 deepcopy，避免旧函数补页号或更换较长 item.name 时污染调用方原始证据。返回结果与旧函数在独立输入副本上的结果对齐；这不是承诺 source_pages 内的合并过程对象从不变化，调用方仍应独立保存原始 OCR。
2. 旧金额结束页策略在最后附加无 total 尾页时，**不重建 invoice_fields**。尾页 items/notes 仍在 source_pages 中，但不补入第一张页的合并字段。本批保留并专门断言这条已上线行为；其后若修复，必须单独作为业务变化验证。

## Decimal 的精确差异

分组只用金额进行比较和生成两位价格判重键，不重算或覆盖返回字段里的原金额文本。两位价格键固定采用 `ROUND_HALF_EVEN`，不受调用方 Decimal context 的舍入设置影响。普通两位金额差分严格相等，以下变更独立给出旧/新精确断言，没有用容差放行。

| 输入价格 | 旧 float 键 | 新 Decimal 键 |
| --- | --- | --- |
| `2.675` | `2.67` | `2.68` |
| `2.665` | `2.67` | `2.66` |
| `1.015` | `1.01` | `1.02` |
| `-2.675` | `-2.67` | `-2.68` |
| `-0.005` | `-0.01` | `-0.00` |

这会影响商品判重：同名同量 `2.675 + 2.67` 旧保留一行、新保留两行；`2.675 + 2.68` 旧保留两行、新保留一行。这里是 Decimal 规则的真实行为差异，不能称全量逐位兼容旧 float。

三个总额边界也有明确发票数断言：第一张有号，第二张无号但含 subtotal 与自己的总额。

| 两个总额 | 旧结果 | 新结果 |
| --- | --- | --- |
| `9007199254740992` 与 `9007199254740993` | float 碰撞，1 张 | 精确不同，2 张 |
| 400 个 `9` 与 400 个 `8` | 均溢出为 inf，1 张 | 两个有限 Decimal，2 张 |
| `0` 与 `0.` + 399 个 `0` + `1` | 后者下溢成 0，1 张 | 保留微小非零值，2 张 |

400 个 `9` 的旧价格键为 `inf`，新键为原 400 位整数加 `.00`。本批不为兼容浮点污染把精确金额重新变成 float；调用方仍须在后续审核/推送层执行自身金额范围与完整性校验。

## 已验证识别结果的整批准备

`ocr.results.prepare_history_batch(result: PipelineResult, *, expected_page_count, workspace, source, client=None, posting_kind=None, direction=None)` 串起真实 Pydantic 结果、`flatten_pipeline_pages`、`assess_confidence`、`group_invoices`、`history_pages`，返回 `groups` 可由内部 `history.recognition.persist_batch(source_file, groups, request_key)` 消费；本模块不调用该服务，也不产生 IO。依赖方向是 `ocr.results → ocr.schema/pages/grouping + contracts.history/push`，没有向 Frappe 持久化层的导入。

来源对照：固定源 `services/ocr/recognize/core.py:287-345` 的全非票检查与批次置信度、`recognize/persist.py:178-186` 的首张页合并与其 `insert_ocr_history` 首次 `ai_raw=pages` 默认语义、`page_runner.py:292-375` 的 L2 回退人工复核。保留每张来源页、物理页号、同页多票、全量字段、页级诊断和批次置信度。每组 `pages` 和 `ai_raw` 由相同的首次保存内容分别深拷贝，后续审核编辑不会反向改原始快照；传入 `PipelineResult` 不变。

必须显式传入独立核定的原件物理页数及现有历史渠道，`source` 只接受既有合同的 `manual / cowork_line / summary_table_batch`，不推测 `web` 或 ERP 渠道。所有组使用显式 workspace/client，最终持久化重新做服务端 scope 权限检查；这里不做公司自动分配、归档命名、类别学习、数据库查重或授权。

| 情况 | 准备结果 |
| --- | --- |
| 物理页完整，常规识别 | `state=ready`，提供草稿 groups；不代表业务自动完成 |
| 原来已标记 needs_manual_review 或 confidence_band=needs_review | `state=review_required`、`requires_review=True`，保留可保存草稿 |
| 有效 L2 保留，但 `L3_failed / L3_quota / L3_transient / L3_skipped_budget / L3_totals_rescue / IF_failed` 或 recoverable error | 必须含 L2、无早期硬失败，并有票号/总额/卖方或明确非票结果；强制拷贝后的每张展开页进入 needs_review，保留 error/layer_chain，不污染输入 |
| 缺页、重复物理页、越界页、报告页数不一致、L1/L2/未知硬失败、回退但无可用 L2 | 抛 `OcrResultError(ocr.result_incomplete)`，带缺失/重复/失败页明细；没有可封存的批次 |
| 全部展开结果均明确非票 | `state=not_invoice`、groups 为空、`has_billable_result=False`；不生票，不发出任何收费操作 |
| 发票与非票混合，包括同页分票后的非票父项/剩余非票项 | 保持旧分组：不偷偷丢弃非票页证据，不因一项非票就拒绝整批 |

`has_billable_result=True` **仅表示有票据准备结果，不是余额通过、价格确认、豁免判定、幂等成功或计费许可**。收费仍须后续授权流程在持久化成功后处理。`requires_review` 与该标志独立；`ready` 也只表示该纯准备完成，本批历史仍使用已有 staged 语义，不自动推送。

原件物理页合同适用于已独立解码的 PDF/图片；表格行的虚拟 page_number 不能冒充文件物理页，待单独来源映射后才可接入此准备入口。`raw_pages` 保留分组前展开证据（必要时强制 review 标志），合并后的首次保存证据在每组 `pages/ai_raw`；不能用展开页数/重复 token 字段重新算物理页或供应商用量。

本入口遇分组异常会传播失败，不照搬旧 persist 中吞异常并把所有页当一张票的 fallback。这是为避免错误封存原件的明确失败处理变化；没有假成功返回，也没有在纯模块内补造票据或记录。完整但关键字段全空且没有任何失败标志的结果仍按旧识别草稿/低置信度语义，不把本批准备层扩成新的识别质量算法。

## 检查与证据

2026-09-21，本工作树未提交代码：

- `tests/test_cowork_invoice_grouping.py`：17 项单测通过，覆盖三路分组、半数门槛、非连续同号、完整金额块、复写联/普通跨页区分、全标量/来源传递、physical page 重复、history_pages 适配、无输入污染和金额边界。
- `tests/test_cowork_invoice_results.py`：13 项真实 Pydantic 纯集成用例，覆盖首次 pages/ai_raw 独立快照、同页多票、非连续页、方向溯源、批次置信度、全部/混合非票、缺页/失败阻断及 L2 回退人工复核。
- 既有 `tests/test_cowork_ocr_contracts.py`：12 项通过，物理页证据与旧置信度适配未回归。真实 Site 保存/权限回读由父任务的 `scripts/cowork-invoice-lab-verify.py` 另记，不以这些纯测试替代。
- `tests/test_cowork_invoice_group_source_parity.py`：10 固定 + 3,000 随机分组、11,441 普通金额解析/价格键、3,000 税号完全一致；另对 5 个半分价键、2 个商品行数变化、3 个发票数变化、1 个 inf 价格键差异作精确断言，全部通过。
- `python3 scripts/check-architecture.py`：PASS，0 违规；`git diff --check`：通过。
- 使用已运行、挂载本工作树的 `colima-pearnly` / `pearnly-cowork-backend-lab` bench Python 执行纯测试，因为宿主 Python 3.14 未装 Pydantic；没有 init/connect Site、无 DB 读写、无新增环境依赖、无容器启停或配置修改。

哈希绑定（来源不匹配立即失败；只执行纯函数和常量 AST，不 import 旧项目）：

| 来源文件 | SHA-256 |
| --- | --- |
| `services/ocr/invoice_grouper.py` | `5905242c620c13306d16aa2e49cc47e763b83aadf7408a23800e4d0f60835b6a` |
| `services/ocr/money.py` | `48aacdd033dbf9bc5d77f8bcc64835c96394ba4114e3f9684ea9d615249d105c` |
| `services/ocr/schemas_invoice.py` | `c3c9f848cb587c725b519f7af7defdfc7fab2599b8f9beced482d04531f8c793` |

单测可运行：

```bash
docker --context colima-pearnly compose -f infrastructure/compose/cowork-backend-lab.yml exec -T bench /home/frappe/frappe-bench/env/bin/python -m unittest discover -s /workspace/tests -p 'test_cowork_invoice_grouping.py' -v
```

差分脚本支持 `--source <固定源根目录>`，或将上述三文件 `{相对路径: 原文}` JSON 送入 stdin 后使用 `--source-stdin`。本次采用后者，不把来源快照写入容器。源金额规则全量函数读取为参考，其 float 仅存在测试 oracle，新业务计算未照搬。

此结果证明纯合同与列明边界，未证明真实 OCR 供应商输出、完整生产识别链、并发业务、计费、UI/真机或上线验收；这些由后续工作包分别承接。
