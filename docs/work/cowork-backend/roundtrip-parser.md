# ERP 复核工作簿确定性回导：解析与纯转换

本包属 `pearnly_cowork.roundtrip`。第六批沿独立工作树基线 `669888001871f5e8cc4bbc783b0ff6c2499d76c5` 开发；开工生产源码/运行策略复核由主任务记录，均仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。本页仅证明本地解析、纯转换和固定源差分；原生受理/保存/收费/下载由主任务另外验证，未部署、未调用真实模型或 ERP。

## 入口与归属

- `parser.parse_roundtrip_workbook(content: bytes) -> dict`：受限读取 OOXML，返回 `documents`、`pending`、`sheets`，新增 `source_sha256` 与 JSON 可序列化的 `provenance`。不读取历史库、不授予来源权限。
- `contracts.to_pipeline(parsed, *, anchor_year: int) -> PipelineResult`：零 IO、零模型；文档在前、待判在后，一张解析票对应一个逻辑单元。依赖现有纯 schema，显式年份，不用后台系统时钟。
- `spec`：共用泰文列名、表名、七列必需指纹、可选分店列、PRN 键、状态文本；`REQUIRED_HEADERS` 是公开只读 tuple。单元格格式由导出 adapter 管理。
- `money`：共用 `to_money`、`quantize_money`、`line_amount`、`vat_of`、`sum_money`、`doc_totals`、`allocate_vat`；Decimal，两位 HALF_EVEN。无 Frappe/网络/文件依赖。

```python
from pearnly_cowork.roundtrip.parser import parse_roundtrip_workbook
from pearnly_cowork.roundtrip.contracts import to_pipeline

parsed = parse_roundtrip_workbook(content)
result = to_pipeline(parsed, anchor_year=2026)
# 调用方还须校验 source_history_id 的当前权限，并受理为新历史记录。
```

不承诺 `estimated_cost_thb=0` 等于用户免费。旧 `recognize/core.py:251-259` 对回导仍按 Excel 字符计价，`persist.py` 再走批次收费；这里的零只是没有模型调用成本。计价、扣款和重放归执行域。

## 逐字段保持的行为

| 来源规则 | 当前行为 |
| --- | --- |
| 只有 ขาย/ซื้อ/รอจำแนก，且首行含七列指纹才命中 | 相同；สาขาคู่ค้า 仍可省略；列可移动，重复表头取最左列 |
| 销项分组优先 PRN 原 ID、再票号、最后行号 | 相同，支持同 ID 非连续行；首次出现的票号、日期、买方、税号/分店、ERP 单号/对方码及票面 VAT 生效 |
| 销项每行商品 | 保留 description/qty/unit_price/amount/erp_item_code；显式金额优先，否则数量×单价；存在任意显式税前格时，仅这些格求和成为税基 |
| 进项每行一票 | 相同，即使 PRN/票号相同也不先合并；seller_tax/branch 优先法定列，回退到回导列；category、ERP 单号/对方码保留 |
| 进项总额 | 保留旧 reader：由税基+VAT 重算，忽略打印 total 列；税基为零且没有票面 VAT 时，三金额均 None；法定 VAT 为 0 时仍沿旧 `or` 语义回退到回导 VAT |
| 待判 | 保留 history_id/票号/日期、reason、空 items、source_rows；旧 reader 未导入其打印对方/总额，本包不凭空补入；无票号也保留为人工待判 |
| 日期/空值 | 单元格日期格式为 YYYY-MM-DD；`none/null/nan` 文本为空；模型日期使用既有显式 anchor_year 校验 |
| 方向 | 只由所在表显式给 sales/purchase；待判空方向；剪到另一个合法表后按新表方向 |
| 纯转换 | 保留 `layer_chain=[roundtrip_workbook]`、L1 confidence=1；旧默认 `final_confidence=0`、`confidence_band=auto` 未偷偷修改；pending needs_manual_review=True |
| PRN | 保留原格式及负行序可解析行为；行序是证据，不用来重新排序商品；格式有效不证明来源授权 |

原来源没有另外的结构化 warning/error 列表。待判 reason 是保留的业务提示，写入 invoice.notes；既有推送状态列只用于导出/指纹，reader 不把它当作新 ERP 已推送的事实。

`source_row_keys` 新增每条原行的原字符串键、decoded ID/line_index。`provenance.units` 每个逻辑单元保留表名、原行号、第一行表头、表头列位以及所有原行 cells（包括未参与计算的列）。这些是逻辑区段，绝非 PDF 物理页。每个 PipelinePage 的 `extraction_audit.roundtrip` 深拷贝上述引用及完整 reader_fields；ERP 码在这里保留，不伪装成旧 ThaiInvoice 原本就支持的字段。

整个工作簿可能含汇总表、无名/额外列、隐藏键等非本票独占内容，因此 parser 的 `coverage_complete=False`。主任务 `intake` 再保留通用解码的完整 workbook_evidence；这不阻止回导，只限制完整原件权限，不能因新票全部获授权而把整个原件授予他人。

## 金额边界与明确变化

reader 的 `_num` 原本用 float 解析数量、单价和显式销项税基。迁移在同一输入边界保留 float 解析，再立即 `Decimal(str(value))`，后续金额全 Decimal；不是改成任意精度字符串直入后悄悄改变旧结果。

- `2.675 × 1 → 2.68`、负数 `-2.675 → -2.68`、`0.01 → 0.01`，均沿旧 reader 的 Decimal HALF_EVEN。
- `"1.00000000000000009" → Decimal("1.0")`；`"9007199254740993" → Decimal("9007199254740992.0")`，保留原 float 输入损失。绝不把这些案例改成新的精确值并放宽差分。
- `_num` 不识别 `THB`，而 `money.to_money` 识别；两条来源路径的宽松程度保持不同。
- 票面 VAT 与计算 VAT 在两位量化后相差不超过 0.02 时保留票面值；0.03 改用重算。金额 helper 与旧纯 erp_money 正常输入无有意差异。
- qty/price 输出旧模型字符串时恢复旧 float 格式（例如 1.0→"1"），只为文本兼容，不用于金额运算。

以下有意变化按本批主任务授权登记：

1. 连续 50 空行后仍有命名列数据：旧 reader 静默停止；现明确 `ocr.roundtrip_trailing_rows`，不截断、不转模型。49 空行继续解析，末尾 50 空行正常结束。
2. 指纹命中但表名周围有空格：旧 reader 字典 KeyError，旧 intake 静默退模型；现 `ocr.roundtrip_sheet_name_invalid`。
3. 命中空表或解析出错：旧 intake 返回 None 退模型；现分别 `ocr.roundtrip_empty` / `ocr.roundtrip_parse_failed`。只有真正不匹配才抛 `RoundtripNotMatched`，其 code 为 `ocr.roundtrip_not_matched`。
4. 非有限数字拒绝；`_num` 直接给 `ocr.roundtrip_invalid_number`，后续 Decimal 非法运算给解析失败。不能把 NaN/Infinity 存为有效金额。
5. 字节入口自身复用既有归档预检和 DecodeBudget，不能绕过压缩/维度/行列/字符限额；同第五批限制，没有额外升级依赖。超过默认 Decimal 上下文精度的巨大乘积仍失败，不伪造金额。
6. 新增来源审计；elapsed_ms 纯转换为确定的 0，不伪造运行耗时。主流程实际执行耗时另计。

旧入口后续 invoice_grouper 会按同票号二次合并不同原 ID/方向，确有错误合票。本 parser 一直保持旧 reader 的逐票边界；主任务 roundtrip 专用保存不再做这次二次合并，属另外明确登记的修复。回导只新增记录，PRN 并非更新或删除原记录的许可。

## 固定源与验证

固定源码在 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`，测试绑定文件 SHA256：

| 来源路径 | SHA256 |
| --- | --- |
| services/excel/erp_roundtrip.py | 03aaf37f6396613811d3c5723fd3c685ec121415b88770d3632c38f7d76f33ad |
| services/excel/erp_money.py | 6bfe9ba4ed0e69a7417e2c1520e682615dbdd43a133a245db3fb25a6d7d7bdc8 |
| services/excel/erp_roundtrip_reader.py | 26a367002ff778ae00e43ee309c3c90077accca1dfef2a4eb32420e09de171bc |
| services/ocr/roundtrip_intake.py | 68f3eae7d3e67f821458818f7f1440e467bcd579332aee519e2e5135c439308b |
| services/erp/express_push/common.py（仅 _q） | e00c6410c711208cf2bcbf20579d9d67e365b2957aa9d90a3cd9f68e4fe09f58 |

2026-09-21 候选 lab：镜像 `sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`，Python 3.14.7 / Pydantic 2.12.5 / openpyxl 3.1.5；Cowork/Core/tests 均挂载本任务树。未启动、重启、安装、迁移环境。

- `tests/test_cowork_roundtrip_parser.py`：16 项通过，含成功、失败、原行审计、深拷贝、待判无票号、资源预检及金额边界。
- `tests/test_cowork_roundtrip_parser_source_parity.py`：5 个固定文件 AST，仅执行纯逻辑与本地解码，没有 legacy app 导入。162 份正常工作簿、1048 个 PipelinePage 对齐；1521 次金额、21 个 `_num` 输入、20 次规格比较一致；另有 2 份旧新均 Decimal 失败；4 项有意变化逐项断言。
- 差分比较除新增 provenance/source_row_keys/extraction_audit 及 elapsed_ms 外的完整旧结果。旧浮点 qty/price 仅按已声明 `Decimal(str)` 类型映射比较；模型输出字符串仍完整相等。schema 两边复用第三批已验证模型、固定年份 2026，这不是重新声称旧系统时钟行为已核验。
- `python3 scripts/check-architecture.py` 0 violations；`git diff --check` 通过。

可复现命令（本工作树，不访问 Site、不外呼）：

```bash
docker --context colima-pearnly compose -f infrastructure/compose/cowork-backend-lab.yml exec -T bench \
  /home/frappe/frappe-bench/env/bin/python -m unittest discover \
  -s /workspace/tests -p 'test_cowork_roundtrip_parser.py' -v

python3 - <<'PY' | docker --context colima-pearnly compose -f infrastructure/compose/cowork-backend-lab.yml exec -T bench /home/frappe/frappe-bench/env/bin/python /workspace/tests/test_cowork_roundtrip_parser_source_parity.py --source-stdin
import json
from pathlib import Path
root = Path('/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b')
paths = ['services/excel/erp_roundtrip.py', 'services/excel/erp_money.py', 'services/excel/erp_roundtrip_reader.py', 'services/ocr/roundtrip_intake.py', 'services/erp/express_push/common.py']
print(json.dumps({p: (root / p).read_text() for p in paths}))
PY
```

本包没有新增依赖。实际二进制 BIFF `.xls` 并非旧 openpyxl 能解的输入，旧 intake 仅名义列了扩展名；OOXML 字节误用 `.xls` 扩展的兼容性由既有 table_inputs 处理。真实 ERP 删除/重推、历史 UI、外部业务及用户验收均不在本包完成声明内。
