# Cowork 表格与文本识别纯编排（第五批）

第五批从新平台 `ecdfd99ded2ea73c365d5b15922fbd4a3614f7a5` 开工；主任务已重新核对旧生产源码仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，并确认实际 invoice/economy/Vertex 运行配置后授权实施。固定来源为 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。本模块只处理已解码的 `Page` 与注入模型回调，不读文件、环境变量、数据库、时钟或网络；不包含前端、外部推送或部署。

## 真实来源与范围

| 固定源入口 | 真实合同 | 本批落点 |
| --- | --- | --- |
| `services/ocr/handlers/invoice.py:30` | 表格先尝试复核工作簿确定性回导，未命中才进通用表格链 | 特征命中先明确拒绝；不能错误改走 LLM |
| `services/ocr/table_path.py:48` | xlsx/xlsm/xls、csv/tsv、docx/doc、txt 逐格式读取 | `ocr.table_inputs`；解析器与资源限制由输入模块维护 |
| `services/ocr/pipeline.py:424` | 按虚拟页顺序调用同一 invoice/auto L2；关闭 L1/Vision、direct、L3 | `table_engine.run_table` → `engine.runner.run_text_pages` |
| `services/ocr/layer2_structure.py:276` | 优先 headers+rows 网格，缺结构才取 full_text；strip 后不足 20 字符视为非发票；大于 30,000 字符旧源静默截断 | 保持提示输入选择与短页语义；超过 30,000 字符改为受理前整件拒绝 |
| `services/ocr/page_runner.py:136` | 来源/日期警告、折扣修复、金额/税号/包含性/同页漏票触发、sanity、贷项、字段置信、分桶 | 复用第四批同一纯函数，不另写一套校验或提示 |
| `services/billing/pricing.py:121` | 表格按原件字符估算，独立于虚拟页数和 provider token | 输入层保留单位口径；root 服务报价与幂等收费 |

通用表格源**没有行分块，也没有 2,000 行或 50 工作表上限**。2,000 行来自另一个 `summary_import/parse.py` 人工汇总表导入产品路径；50 页是图像/PDF 的物理页限制。不能把这些限制混为源功能，或者把通用表格 L2 改成银行/GL/汇总导入专用提示。本批不迁这些无关分支。

格式事实必须与扩展名列表分开说明：旧 `.xls` 实际仍只调用 openpyxl，没有注释所称的 xlrd fallback；真正 BIFF 文件可能失败。旧 `.doc` 只是顺序尝试编码后过滤不可打印字符，不是真正二进制 Word 文档阅读器。旧 DOCX 把所有段落和表格留在 full_text，但只要第一表有结构化正文，L2 就只看到第一表；重复表头的 dict 覆盖、超出表头列截断也可能漏掉数据。输入模块保留源 Page 形状，同时用独立 coverage 证据揭示这些问题；受理服务必须通过完整性检查才可调用模型，不把解析成功等同于完整识别。

## 接口与虚拟单元

```python
from pearnly_cowork.ocr.table_inputs import extract_table
from pearnly_cowork.ocr.table_engine import validate_document, run_table

document = extract_table(content, filename)
validate_document(document)  # 受理前；执行重放时再次验证
result = run_table(document, model_call, config=config,
                   anchor_year=anchor_year, today=today,
                   fewshot_selector=authorized_snapshot_selector)
```

`document.pages` 为现有 `Page` 的有序 tuple，`page_count` 等于逻辑单元数，页号必须为 1..N。Excel 一个工作表是一个单元，CSV/TSV/TXT/DOCX/DOC 各为一个逻辑单元。这些数值不能进入 PDF 截页/图片裁切授权；原件 manifest 使用 `reference_kind=logical_section`，保存 source hash 和 sheet/行/单元映射。`document.provenance` 是独立源证据，不拼进模型提示；`billing_characters` 是费用单位，不能按虚拟单元数或返回兄弟票数替代。

`validate_document` 在任何模型调用前验证**全部**单元，抛稳定 `EngineError`：`ocr.table_units_invalid`、`ocr.page_association_invalid`、`ocr.table_text_too_long`、`ocr.table_roundtrip_not_supported`。超长判断使用与实际 L2 完全相同的网格序列化和 strip；full_text 较长但模型实际读取合法短网格时，此条限制不会误报，遗漏覆盖应由输入层 coverage 单独判断。

`run_text_pages` 是新增公共薄入口，不改 `run_invoice` 的物理页行为。它复制完整输入后顺序调用已有 `_Run.pipeline_page`，强制禁用 direct/L3，根本不接受 Vision 回调。返回 `EngineResult(pipeline, calls)`，pipeline engine 为旧 `pipeline_v1_table`。同单元 `additional_invoices` 原样保留，不展开成新的付费调用。Root 服务仍负责完整结果闸、History 原子封存与收费。

`EngineConfig` 无新增环境键；实际消费 L2 模型、超时、JSON 重试、金额容差、置信/分桶阈值。支持 invoice/auto 与 economy/enterprise+Vertex，未支持模式明确拒绝。root 现有受理使用 document_type=`invoice`；旧通用函数默认 `auto`，两者走相同发票模型、提示和校验，仅结果入口标记不同；差分覆盖两者。日期、年、模型配置和授权 fewshot 快照必须受理时固定；本模块不查询历史修正记录，逐单元按实际模型文本选择快照中的例子。

## 复核工作簿与剩余迁移

旧 `erp_roundtrip.py` 指纹要求 Excel 真实第一行包含七个必需回导列，且工作表 strip 后是 `ขาย`、`ซื้อ`、`รอจำแนก`；`สาขาคู่ค้า` 是可选列。列位置、额外列和重复列不改变该指纹。新输入层额外保留 `roundtrip_headers`（data_only=False 的真实第一行），避免把公式缓存值误当真实表头；通用识别仍沿旧 data_only=True。

第五批曾在模型与受理前拒绝命中指纹的文件；第六批已在 `ocr.execution` 先分流到 `roundtrip` 专用解析。通用 `run_table/validate_document` 仍拒绝回导模板，防止误喂模型。销项按历史键/票号聚合商品行、进项一行一票、表名方向、待判、Decimal金额及源行证据已接本地原生流程；50空行后有未读业务行明确拒绝。旧History权限、当前Site和公司/客户逐次复核；上传PRN不授予权限。尚无旧UUID映射、外部ERP重推或旧单删除，详见 [第六批整链合同](roundtrip-native.md)。

旧回导 `estimated_cost_thb=0` 表示没有 provider 调用；旧 recognize 保存成功后的字符收费并未据此豁免用户。第六批已按原Excel字符单位，在原子保存后以稳定引用结算，零模型调用不免业务计费。

## 失败、复核与收费证据

- 源表格 `pipeline.py:449-462` 顺序直接调用 `_process_one_page`；L2 位于 `page_runner.py:179-184` 且没有“变成失败页后继续”的 catch。任何 L2 最终失败整件抛出，新链仍不返回部分成功。Core 负责既有付费结果的检查点、Deferred/Uncertain 和可续跑状态；不能吞未知结果而重复付费。
- L2 JSON 内容重试最多沿冻结配置一次；保留全部已完成 raw/attempt/token/key。已收到的明确 ProviderFailure、权限/配置错误都向上抛；表格没有“升 L3 抢救”。先前已完成单元由 Core 检查点留证，不由纯函数伪造半件结果。
- 旧“低置信人工”不是“有任一 trigger 就 manual”：基于 avg_confidence，每 trigger/soft flag 减 0.05，结合强制复核与原阈值分桶。日期/来源 warning 本身仍未独立强制人工；sanity、贷项、系统补折扣和疑似漏票等规则保持。不以旧文档简写替换真实代码。
- 字符口径沿源：Excel 所有非空单元格 `len(str(value))`；CSV/TSV/TXT 按 UTF-8 忽略无效字节后的长度（与 CP874 解码文本字符数可能不同）；DOCX 只算顶层段落，未计表格单元格；旧读取失败还有文件大小粗估。50 字符 1 satang、向上取整是源普通字符价格，Core 租户计费由 root 适配。模型 token、返回发票数量和虚拟工作表数都不是这项用户收费单位。
- 新明确差异：完整性问题及超过 30K 文本不再静默成功；资源边界按输入模块的独立上限明确拒绝；不新增隐形行分块。首存 provider 证据、空识别不生 History/不收费、整件原子保存继续使用第四批保护。

## 验证

运行于专用隔离 lab，Python 3.14.7 / Pydantic 2.12.5，附加 python-docx 的本任务镜像 `sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`。仅纯离线测试，未初始化/连接 Site、未启动或停止容器、未调用外部 OCR。

`test_cowork_table_engine.py` **20 项通过**，覆盖顺序 L2、完整输入预检、51 逻辑单元、短页、网格提示、低信分桶、首存/折扣、同单元多票、内容重试、各类失败透传、后页失败不返回半件、配置/日期、重放输入冻结与回导指纹。`test_cowork_table_engine_source_parity.py` 校验固定源码 SHA-256 后只编译指定纯 AST 定义；不导入旧应用。差分已通过 **1,500 个回导指纹、288 个完整单元提示与结果决定、1 个 30K 有意差异**。288 场景覆盖 invoice/auto、网格/纯文本/短文本、四置信度及金额/日期/来源/贷项/同页多票等十二种模型响应；耗时与全部重试用量沿第四批登记差异排除，金额告警数字显示沿第三批规则比较，其余结果逐字段比较。架构检查 0 violations，原 `engine/runner.py` 全部既有 AST 未改，只有新增公共函数；table_engine 无 IO/运行层反向依赖。

以上证明纯编排与来源合同，不代表真实供应商质量、账单落账、MariaDB/RQ 恢复、文件下载权限、前端或用户验收。输入解析、manifest、工作流/Site 总验证与交付前生产读回由各归属模块和主任务报告。

## 本批后端四语词条

唯一来源 `scripts/i18n/messages.tsv` 新增 30 条 Cowork 标签/枚举/服务错误/纯错误码友好文本，使用既有 `i18n-build.py` 的 read/existing/write 函数定向生成 Cowork en/zh/th/ja，四份各 266 条唯一 msgid。没有重写 Core/Control/DMS 语言包或既有 TSV 行，也未改前端。Billing Units 沿用已存在的 Control 四语同义文案；不把全站通用 `excel` 改写为仅本计费流程的含义。

生成器校验、新增行与四语产物一致、重复键与既有跨 App 同名条目一致、TableInputError/EngineError 新键差集检查均通过。已有 `check-i18n.py --missing-all` 分支会忽略 `--app` 过滤，报告全仓既有的 23 个 msgid × 3 语缺项；本批不扩大忽略、不宣称全仓严格 i18n 通过。中泰英日文案已做术语与含义核对；原生 Site 的翻译加载验证归主任务，实际页面布局和泰日母语业务审阅未执行，不代替用户验收。
