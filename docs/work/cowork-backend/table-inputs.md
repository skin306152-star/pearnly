# 表格与文本原件的结构化解码

第五批工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，开工基线 `ecdfd99ded2ea73c365d5b15922fbd4a3614f7a5`。父任务于 2026-09-21 重新核验旧生产 source/runtime gate 为 PASS，仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`；只读源码快照 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。交付前线上复核由父任务统一记录。

本包属于 `pearnly_cowork.ocr.table_inputs`，从原件 bytes 产生逻辑区段、旧 L2 输入和独立来源证据，不读 Frappe、数据库、磁盘文件或网络，不执行宏、Word 外链、LibreOffice/antiword、OCR 或计费。依赖方向为 `table_inputs → ocr.inputs + ocr.schema`；没有公开 RPC、队列入口或新 DocType。原件持久化、权限、manifest 冻结由 files 域负责；L2 编排由 engine 负责；字符数仅计费 units，不代表已取得余额或扣费许可。

## 接口

```python
from pearnly_cowork.ocr.table_inputs import extract_table, TableDocument, TableInputError

document = extract_table(original_bytes, "invoice.xlsx")
assert document.source_sha256 == original.sha256
# 受理层在首次模型调用前验证完整范围与旧输入覆盖，冻结 manifest。
# files.service.freeze_doc_manifest(original, document)
# engine.table_engine.validate_document(document)
# engine.table_engine.run_table(document, model_call, config=..., anchor_year=..., today=...)
```

`TableDocument` 为 frozen dataclass：`pages: tuple[Page,...]`、`page_count: int`、`engine: str`、`billing_characters: int`、`provenance: dict`、`source_sha256: str`；`coverage_complete` 属性读取 provenance 同名布尔值。`pages` 的 Page 字段保持旧实现，来源证据不混入提示词。页号 1..N 是**逻辑区段**，例如工作表，不能当作 PDF 物理页；原 Source 的页数占位与 manifest 的 reference count 由原件域分别保存。嵌套 Page/dict 仍为普通对象，持久层须以 canonical JSON 冻结并在重建后比对，不能把 frozen dataclass 误当深度不可变对象。

`source_sha256` 只计算本次解析输入 bytes。Source.sha256 仍是原件权威值，二者必须相等；解析器不接受调用方提供的 source ID/hash。`TableInputError.code` 是稳定错误码，`details` 只带必要本地元数据；空输入、无效格式、无法解码与资源超限均不返回部分完成结果。

## 固定来源与保持的算法

| 来源 | 新落点 | 保持行为 |
| --- | --- | --- |
| `services/ocr/table_path.py:48-88` | `reader.extract_table` | 八种扩展名路由；engine 保持 `table_path_excel/csv/tsv/docx/doc/txt` |
| `table_path.py:93-155` | `reader._excel` | openpyxl `data_only=True,read_only=True`；每 worksheet 一页，包括空/隐藏表；首非空行作表头，空行跳过，值转 str，重复表头仍覆盖；公式读取缓存，不执行公式或宏 |
| `table_path.py:161-199` | `reader._csv` | CSV 第一条记录作头，TSV tab 分隔；空 body 记录跳过，短行补空、长行按头截断；full_text 保留全部 body；编码顺序原样保留 |
| `table_path.py:205-251` | `reader._word` | DOCX 段落与全部表组成一逻辑区段；仅首表写 table_rows；首表无有效 body 时为 None，L2 会使用 full_text |
| `table_path.py:254-286` | `reader._text` | DOC 仅顺序解码后过滤不可打印字符，置信度 0.7；TXT 原解码文本，置信度 1；没有新增二进制 Word 解析器 |
| `table_path.py:292-318` | `_decode/_headers/_render` | UTF-8 BOM、UTF-8、CP874、TIS-620、CP1252、Latin-1 顺序；表头 strip；管道文本排版 |
| `services/billing/pricing.py:121-158` | 各 reader 的 characters | 独立旧字符单位，不用 L2 文本长度替换、不修改金额公式 |

`.xls` 在旧格式名单内，但同样调用 openpyxl，没有 xlrd fallback：真正 BIFF 二进制 XLS 仍失败，伪装为 `.xls` 的 OOXML bytes 与旧代码一样可读。`.doc` 仅尽力抽文字，不能声明为完整 Word 支持。

计费字符严格保留：Excel 为全部读取到的非 None 单元格 `len(str(value))` 之和，包含头与缓存值；CSV/TSV/TXT 为原 bytes 按 UTF-8 `errors='ignore'` 解码的字符数，包含 BOM（因此可能与识别文字不同）；DOCX 只计算 `document.paragraphs`，不含表格；DOC 尝试 python-docx 的段落口径，失败取 `len(bytes)//2`。零字符保留为 0，不抬到 1。旧 Excel 计费 helper 在解码失败时用 `bytes//4`，本入口的 Excel 解码失败会先终止，因此不生成可收费文档，也不靠粗估继续模型调用。解码器不决定业务是否收费，零字符、全非票、失败不收费由执行层另验。

## 来源证据与不完整覆盖

`provenance` 可 JSON 序列化，包含 `version=1`、`reference_kind='logical_section'`、`unit_kind`、`coverage_complete`、`coverage_issues`、`units`。每个 unit 含 1-based `page_number` 与 `source_kind`：

- Excel：sheet 名/索引、首有效头原行号、全部读取的原行和按列排列的 cells（含空行）；`table_row_index` 表示映射到旧 Page.table_rows 的 1-based 索引，未进入为 None。`roundtrip_headers` 单独用 `data_only=False` 读取真实 row 1，只为旧回导指纹核对，不替换模型输入。数组/数据表公式的对象 repr 含内存地址，证据改用稳定类型标记；这些标记不能命中任何旧必需表头。
- CSV/TSV：解码编码、delimiter、record 序号和实际文本行号 `line_start/line_end`；带换行的引号单元格可以跨多行。原 cells 保留被旧 dict 覆盖或截断的值。
- DOCX：所有正文段落及全部表的原行/cells，首表的 table_row_index。这里“原 cells”指旧库解码后的字符串，完整 Office XML/二进制仍由 hash 绑定的私有原件保留。
- TXT/DOC：解码后行与原行终止符。DOC 明确标 `line_basis='decoded_printable_text'`，不冒充 Word 物理布局或原始字节偏移。

以下不更改旧 Page/提示词，而是标 coverage false：重复表头、无名列有值、超过表头宽度的非空值；DOCX 实际选择首表时被遗漏的非空段落/后表；非空页眉/页脚/脚注/尾注及被库跳过的嵌入媒体、文本框、结构化内容和修订；Excel drawing/批注/嵌入对象/扩展结构与非空 header/footer；XML `<f>` 无非空缓存值的公式；所有 legacy DOC 尽力抽取。原件域与执行域据此阻止不完整范围进入模型，避免“正常返回但漏内容”。这不等于新增识别这些内容，原件仍可在既有上传接口保存。

## 明确有意变化与资源上限

父任务已批准以下新保护，超限拒绝，不截断或部分处理：

- OOXML ZIP 最多 10,000 个成员，声明及实际展开总量最多 128 MiB；读取 CRC 后才交给解码库，不解压到磁盘。XML DTD/实体声明（含 UTF-16 编码）拒绝；损坏 XML/ZIP 拒绝。
- Excel 每表最大 100,000 行、256 列，全部表声明矩形单元格及实际 XML cells 各最多 500,000。按 XML root 与 worksheet relationship 识别工作表，覆盖自定义 part 路径和无扩展名 part，不能依赖 `xl/worksheets/` 前缀。解码前查维度，实际坐标超出声明、行列重复或逆序均拒绝，避免旧库静默漏读或分配巨大空矩形。
- CSV/TSV、Word 表格最多 256 列、合计 500,000 cells；每表/文本记录最多 100,000 行；提取文本合计最多 8,000,000 字符。CSV 原文本也先检查字符数。保留标准 csv reader 的原字段长度限制。
- L2 的旧 30,000 字符静默截断，由 engine 包的受理预检改为明确拒绝；本包不切块、不选取前半。manifest 额外 8 MiB UTF-8 JSON 上限由原件域负责，不能将证据截短后称覆盖完整。

## 依赖与验证

源 `requirements.lock.txt`：openpyxl 3.1.5（118）、python-docx 1.2.0（181）、et-xmlfile 2.0.0（47）、lxml 6.1.1（110）、typing-extensions 4.15.0（213）。xlrd 2.0.2（245）存在于源 lock 但本路径未使用，不新增依赖。父任务维护镜像；本包没有安装包、升级底座或启停容器。

2026-09-21 候选 lab image：`sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`。已核 `colima-pearnly` / `pearnly-cowork-backend-lab` 的 Cowork/Core/scripts/tests 挂载均来自本工作树。运行 Python 3.14.7、Pydantic 2.12.5、openpyxl 3.1.5、python-docx 1.2.0、et-xmlfile 2.0.0；既有 lxml 为 6.1.2，保留底座现状，未降为旧 lock 6.1.1。

- `tests/test_cowork_table_input_runtime.py`：20 项通过，覆盖来源映射、零字符/BOM/CP874、DOC/DOCX/Excel不完整来源、缓存缺失与公式证据稳定、失败、CRC、XML 实体、压缩展开/维度/cell/text 上限、自定义及无扩展名工作表路径。
- `tests/test_cowork_table_input_source_parity.py`：345 个正常范围样本的 Page 全字段、page_count、engine、字符数与旧函数严格相等，另 5 个解析失败拒绝等价。分布 CSV 103、TSV 100、TXT 63、XLSX 15、XLSM 15、OOXML 改名 XLS 15、DOCX 30、DOC 4。使用候选运行库同时执行旧固定函数与迁移函数；这验证已列输入算法和该候选 lxml 补丁组合，不声称已重建旧生产 Python/lxml 全部环境。
- 差分脚本只在 hash 校验后执行选定旧函数 AST，不 import 旧应用。输入为内存生成样本，无 Site、网络或真实 OCR。
- 最后一次架构检查 PASS（0 违规），本包 6 个 Python 文件 AST/空白检查与 `git diff --check` 通过；未提交、未部署。

| 来源 | SHA-256 |
| --- | --- |
| `services/ocr/table_path.py` | `4c12cc5acea0e5633261ca866ba45bfd74c02bde0fcb5e2e2b617f21e62a671d` |
| `services/billing/pricing.py` | `fa7d4d604e2a39223a63667bc5f4c11669d9c2af7bce087e34ae42398456f908` |

定向测试在候选 bench 内执行 `env/bin/python -m unittest discover -s /workspace/tests -p 'test_cowork_table_input_runtime.py' -v`。差分脚本接受 `--source <固定目录>`，或仅两个源文件 `{相对路径: 原文}` 的 JSON stdin 配合 `--source-stdin`。纯解码通过不代表原生 Site 保存/恢复、共享权限、计费服务、真实模型、部署或用户验收通过；这些层由父任务分别记录。
