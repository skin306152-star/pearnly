# 表格与文本原件映射

本批将逻辑区段与 PDF 物理页分开，上传、原件字节、文件指纹和既有 PDF/image 契约保持原义。固定来源为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`；开工生产基准由主任务重新取证为 PASS。此记录描述本地实现与验证，不代表生产部署、真实旧文件导入或用户验收。

## 来源契约和有意变化

旧 `services/ocr/table_path.py` 的 Excel 每工作表一页，CSV/TSV、Word、纯文本各一页；并不是每行一页。`layer2_structure.py` 有结构化表格时只使用表格投影。因此 DOCX 首表之外的段落和其它表可能没有进入模型提示词，重复列头也可能覆盖原值。本批保留旧提示词和识别输出约定，同时明确记录覆盖不足；不为开放整份原件而伪造覆盖或把遗漏内容判成非发票。

`files.service.inspect_content/create` 仍只验证、保存精确原件，document 的 `page_count=1` 仍是旧上传占位值。新增字段不改变上传请求指纹。受理时本地解析后才调用 `freeze_doc_manifest(source, document)`，worker 在任何模型调用前重新解析并校验完全一致。

| 原件 | 引用类型与数量 | 历史附件与预览 |
| --- | --- | --- |
| 旧/新 PDF、image | `physical_page`，旧空字段回落 `page_count` | 保留 `source_pdf_id`、物理页预览与原有绑定指纹 |
| document 已冻结 | `logical_section`，冻结的 `reference_count` | `source_page_indices` 是逻辑区段；`source_pdf_id=None`；拒绝物理页预览 |
| document 未冻结 | `logical_section`，数量 0 | 不允许从占位 1 推导原件覆盖或创建识别历史绑定 |

前两种类型均使用原有 `source_file` 保存原件权威关联。历史投影由 execution 集成包增加 `source_reference_kind`；调用方必须据此解释索引，不把工作表号显示成 PDF 页号。文件投影增加 `reference_kind`、`reference_count`、`coverage_complete` 和 `reference_unit`（`page` / `worksheet` / `delimited_text` / `word_document` / `decoded_text`）。物理页的 `coverage_complete` 返回 null，此标记只描述文档解析覆盖。未冻结文档的 `reference_unit` 为 null。

## 冻结清单

Source 的 `reference_kind`、`reference_count`、`provenance_json`、`coverage_complete` 仅由可信文件服务首次写入，独立于普通原件写入 capability。控制器在 `before_validate` 和 `validate` 都检查；`ignore_validate` 或客户端伪造 flags 不能改写。已冻结内容即使持有普通写 capability 也不能更改。

`provenance_json` 使用 canonical UTF-8 JSON，结构为：

```json
{
  "schema": "cowork-document-manifest-v1",
  "source_sha256": "原件 SHA-256",
  "reference_count": 2,
  "engine": "table_path_excel",
  "billing_characters": 123,
  "page_inputs_sha256": ["解析 Page 投影摘要", "解析 Page 投影摘要"],
  "provenance": {
    "version": 1,
    "reference_kind": "logical_section",
    "unit_kind": "worksheet",
    "coverage_complete": true,
    "coverage_issues": [],
    "units": []
  }
}
```

示例省略 `units` 的内容；实际必须逐个覆盖连续 `1..reference_count`，与解析 Page 顺序一致。解析器保留原始行号、空行、列顺序和单元格，另记进入模型表格的行索引。Word 保留段落和全部表格来源；覆盖问题由 `coverage_issues` 明确列出。清单中的源 SHA 必须匹配 Source，并重新通过受保护 File 读字节验 SHA。

`page_inputs_sha256` 对真实 Page 的 JSON 投影逐个取摘要，防止原始行记录不变、但解析器升级改变模型输入时静默续跑；不再次保存模型解析全文。包括计费字符数在内，任何冻结内容变化都拒绝；同输入重试不再保存 Source，也不隐式提交或回滚调用方事务。

完整清单上限为 **8 MiB UTF-8 字节**，超过即在受理时明确拒绝，不截断。解析器的 ZIP、行列、单元格及字符资源限制独立生效。冻结保存通过 `save(ignore_permissions=True, ignore_version=True)` 显式禁用 native Version 和实时通知，避免将原件行文本复制到通用日志或广播。通用元数据投影不返回 provenance；原件与 File 原有专用权限及 no-store 下载机制继续生效。

## 原件授权与历史绑定

所有读取都重新校验产品购买/角色、Source 初始公司与客户范围，以及每条已关联历史的当前公司/客户权限。上传者保有其已提交原件的读取权，但也必须通过上述当前范围检查。

其它用户读取 document 整份原件还须同时满足：冻结清单存在、`coverage_complete=1`、整批历史已封存、所有 `1..reference_count` 都有历史绑定，且不存在范围外引用。覆盖不足时，即使识别成功且一张历史绑定了索引 1，仍只允许有当前授权的上传者读原件。

旧 Word 提示词未覆盖的段落/其它表、重复列头或无名列造成的遗漏、旧 `.doc` 的有限提取等，由解析器标为覆盖不足；这不会悄悄改动旧提示词。解析失败、缺失区段或超过限制不生成“已覆盖”占位数据。

`history.recognition.persist_batch` 按引用数量校验每个索引、与历史正文页关联一致；允许额外发票共享同一区段。document 的绑定指纹额外绑定冻结清单摘要，PDF/image 的既有指纹公式不变。历史及完成标志继续在同一事务保存，重放复用首次历史 ID；失败按原保存点回滚，不留下部分绑定。部分引用仍可以封存，但不会因此解锁整份原件。

## 验证与边界

- `tests/test_cowork_document_manifest.py`：28 项通过；执行实际源码函数，显式替换 Frappe/存储为零 IO fake。覆盖旧物理页兼容、占位文档拒绝、清单 SHA/连续索引/字节上限、输入投影漂移、不可变 capability、跨范围/全部区段授权、原生验证入口、逻辑历史附件、额外发票共享区段、重放及失败保存点路径。
- 原生安装/迁移、真实 MariaDB/File/Version、worker/计费/历史整链由主任务统一运行 `scripts/cowork-table-lab-verify.py` 及相关既有现场脚本；其结果归主任务汇总，离线 fake 不能代替这些证据。
- 本包未运行 Site、修改云服务、调用真实付费模型、读取真实票据或迁移旧文件。计费种类与数量由 execution 包独立冻结；原件区段数不等于应收费次数。


真实Site补充发现：仅设置 `flags.ignore_version=True` 会被 Frappe 16.33 `Document._save(ignore_version=None)` 覆盖；离线替身原先未模拟该行为。本批改为传显式参数，替身同步模拟真实覆盖规则，原生回归同时确认该 Source 的全部 Version 均无 provenance_json。失败验证生成的内容仅为专用lab合成文件，未处理真实业务原件；不将离线通过替代这项原生证据。
